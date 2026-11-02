"""Install settings: the model connections, generation and search for this user.

The document lives in the per-user configuration directory (``store.py``) and
the API keys live in the operating system credential store
(``credentials.py``). Environment variables already present when the process
started still win, and are reported as such. This layer reads no ``.env``
file: it is the configuration document plus the credential store.

The screen's existing answer shape is kept. Profiles, the revision and the
source of each value are added beside it, so a client built before the profile
screen does not have to change at the same time.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from noteagent import __version__
from noteagent.acquire.run_search import run_web_search
from noteagent.acquire.search_config import (
    KNOWN_PROVIDERS,
    SearchConfig,
    SearchConfigError,
    SearchError,
    build_native_config,
    native_search_available,
)
from noteagent.acquire.types import ProviderNotConfiguredError
from noteagent.llm.config import (
    LlmConfig,
    LlmConfigError,
    capture_process_env,
    process_env_keys,
)
from noteagent.settings import effective as effective_mod
from noteagent.settings.credentials import (
    CredentialStore,
    CredentialStoreUnavailable,
    keyring_store,
)
from noteagent.settings.fields import (
    F_BASE,
    F_COUNTRY,
    F_LANG,
    F_LLM_KEY,
    F_LLM_TIMEOUT,
    F_MAX_TOKENS,
    F_MEDIA,
    F_MODEL,
    F_PROVIDER,
    F_SEARCH_KEY,
    F_SEARCH_MODE,
    F_SEARCH_TIMEOUT,
    F_TEMPERATURE,
    F_UI_LANG,
    Field,
    MANAGED_ENV_NAMES,
)
from noteagent.settings.store import (
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SEARCH_TIMEOUT,
    DEFAULT_TEMPERATURE,
    PROVIDERS,
    SEARCH_MODES,
    Config,
    ConfigStore,
    Profile,
    default_config_path,
    format_number,
)
from noteagent.util.refusal import Refusal

_PROBE_TIMEOUT = 20.0
_PROBE_QUERY = "example"

#: Model discovery is a suggestion, so it waits less than a real request.
_MODELS_TIMEOUT = 15.0
_MODELS_LIMIT = 200

_LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_HTTP_STATUS = re.compile(r"HTTP (\d+)")

_PROVIDER_NAMES = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "ollama": "Ollama",
}

__all__ = [
    "MANAGED_ENV_NAMES",
    "apply_install",
    "clean_base_url",
    "clean_max_tokens",
    "clean_model",
    "clean_search_mode",
    "clean_secret",
    "clean_temperature",
    "install_view",
    "list_models",
    "probe_llm",
    "probe_search",
    "settings_store",
]


def _invalid(field: str, message: str) -> Refusal:
    return Refusal("settings.invalid", message, params={"field": field}, status=400)


def _locked(field: str) -> Refusal:
    return Refusal(
        "settings.locked",
        f"{field} is set in the process environment; the configuration cannot change it",
        params={"field": field},
        status=409,
    )


def _media_enabled() -> bool:
    """Whether image extraction is on. Same rule as ``media_extract_enabled``."""
    from noteagent.acquire.media.extract import media_extract_enabled

    return media_extract_enabled()


@dataclass(frozen=True, slots=True)
class _Context:
    store: ConfigStore
    config: Config
    effective: effective_mod.Effective


#: The process environment is one value per name, so reading the document and
#: applying it must not interleave with another request doing the same.
_context_lock = threading.Lock()


def _context(
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> _Context:
    """Read the document once and make this process use its values.

    The document is read before the environment is touched: a damaged file
    raises without leaving the process with values that came from nowhere. The
    process snapshot is recorded before any file could be read, so an outside
    override is never mistaken for something the screen may change.
    """
    with _context_lock:
        capture_process_env()
        store = ConfigStore(path or default_config_path(), credentials or keyring_store())
        config = store.read()
        effective_mod.forget_applied()
        effective = effective_mod.resolve(config, store)
        effective_mod.apply(effective)
        return _Context(store=store, config=config, effective=effective)


def settings_store(
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> ConfigStore:
    """The store the running core uses, with the stored document applied."""
    return _context(path, credentials).store


def _current(field: Field) -> str | None:
    """The effective value, or ``None`` when the process has no such name."""
    if field.env not in os.environ:
        return None
    return os.environ[field.env].strip()


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field, f"{field} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise _invalid(field, f"{field} is not a finite number")
    return number


def clean_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(F_BASE.id, "base url is not text")
    text = value.strip().rstrip("/")
    if not text or len(text) > 500 or any(ch.isspace() for ch in text):
        raise _invalid(F_BASE.id, "base url is empty or not a single http(s) URL")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _invalid(F_BASE.id, "base url must start with http:// or https://")
    return text


def clean_model(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(F_MODEL.id, "model is not text")
    text = value.strip()
    if not text or len(text) > 200 or any(ch in text for ch in "\r\n"):
        raise _invalid(F_MODEL.id, "model name is empty or not a single line")
    return text


def clean_secret(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(field, "secret is not text")
    text = value.strip()
    if not text or len(text) > 2000 or any(ch in text for ch in "\r\n"):
        raise _invalid(field, "secret is empty or contains a newline")
    return text


def clean_temperature(value: object) -> str:
    number = _finite(value, F_TEMPERATURE.id)
    if number < 0 or number > 2:
        raise _invalid(F_TEMPERATURE.id, "temperature is outside 0..2")
    return format_number(number)


def clean_max_tokens(value: object) -> str:
    number = _finite(value, F_MAX_TOKENS.id)
    if not number.is_integer():
        raise _invalid(F_MAX_TOKENS.id, "max tokens is not a whole number")
    whole = int(number)
    if whole < 1 or whole > 128_000:
        raise _invalid(F_MAX_TOKENS.id, "max tokens is outside 1..128000")
    return str(whole)


def clean_llm_timeout(value: object) -> str:
    number = _finite(value, F_LLM_TIMEOUT.id)
    if number < 5 or number > 600:
        raise _invalid(F_LLM_TIMEOUT.id, "timeout is outside 5..600")
    return format_number(number)


def clean_search_timeout(value: object) -> str:
    number = _finite(value, F_SEARCH_TIMEOUT.id)
    if number < 1 or number > 120:
        raise _invalid(F_SEARCH_TIMEOUT.id, "search timeout is outside 1..120")
    return format_number(number)


def clean_search_mode(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(F_SEARCH_MODE.id, "search mode is not text")
    text = value.strip().lower()
    if text not in SEARCH_MODES:
        raise _invalid(
            F_SEARCH_MODE.id,
            f"search mode must be one of {', '.join(sorted(SEARCH_MODES))}",
        )
    return text


def clean_provider(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(F_PROVIDER.id, "provider is not text")
    text = value.strip().lower()
    if text not in KNOWN_PROVIDERS:
        raise _invalid(F_PROVIDER.id, f"provider must be one of {', '.join(sorted(KNOWN_PROVIDERS))}")
    return text


def clean_country(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid(F_COUNTRY.id, "country is not text")
    text = value.strip().upper()
    if text and not re.fullmatch(r"[A-Z]{2}", text):
        raise _invalid(F_COUNTRY.id, "country must be two letters or empty")
    return text


def clean_lang(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise _invalid(field, "language is not text")
    text = value.strip()
    if text and not _LANG.fullmatch(text):
        raise _invalid(field, "language code is not usable")
    return text


def _read_float(field: Field, default: float, invalid: list[str]) -> float:
    raw = _current(field)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        invalid.append(field.id)
        return default
    if not math.isfinite(value):
        invalid.append(field.id)
        return default
    return value


def _read_int(field: Field, default: int, invalid: list[str]) -> int:
    value = _read_float(field, float(default), invalid)
    if field.id in invalid:
        return default
    if not float(value).is_integer():
        invalid.append(field.id)
        return default
    return int(value)


def _mapping(body: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _invalid(key, f"{key} is not an object")
    return value


def _check_locked(field: Field, new: str, *, normalise: Callable[[str], str] | None = None) -> None:
    """Refuse a change the process environment owns; the same value is allowed."""
    if field.env not in process_env_keys():
        return
    compare = normalise or (lambda value: value)
    if compare(_current(field) or "") != compare(new):
        raise _locked(field.id)


# --- reading a save request --------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LlmPatch:
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    #: A replacement key, or ``""`` to remove it. ``None`` leaves it alone.
    secret: str | None = None


@dataclass(frozen=True, slots=True)
class _SearchPatch:
    provider: str | None = None
    mode: str | None = None
    timeout_seconds: float | None = None
    country: str | None = None
    search_lang: str | None = None
    ui_lang: str | None = None
    secret: str | None = None


def _unchanged(field: Field, new: str, *, normalise: Callable[[str], str] | None = None) -> bool:
    compare = normalise or (lambda value: value)
    return compare(_current(field) or "") == compare(new)


def _read_secret_patch(
    body: Mapping[str, Any], field: Field, current: effective_mod.Value
) -> str | None:
    """A key replacement, ``""`` to remove it, or ``None`` to leave it alone."""
    if body.get("apiKey"):
        value = clean_secret(body["apiKey"], field.id)
        _check_locked(field, value)
        # The same key already in the credential store needs no write.
        if current.source != effective_mod.SOURCE_ENV and value != current.text:
            return value
        return None
    if body.get("clearApiKey"):
        _check_locked(field, "")
        if current.source != effective_mod.SOURCE_ENV and current.text:
            return ""
    return None


def _read_llm_patch(body: Mapping[str, Any], context: _Context) -> _LlmPatch:
    if body.get("baseUrl") is not None:
        base_url = clean_base_url(body["baseUrl"])
        _check_locked(F_BASE, base_url)
        if _unchanged(F_BASE, base_url):
            base_url = None
    else:
        base_url = None
    if body.get("model") is not None:
        model = clean_model(body["model"])
        _check_locked(F_MODEL, model)
        if _unchanged(F_MODEL, model):
            model = None
    else:
        model = None
    temperature = (
        float(clean_temperature(body["temperature"]))
        if body.get("temperature") is not None
        else None
    )
    if temperature is not None:
        _check_locked(F_TEMPERATURE, format_number(temperature))
        if _unchanged(F_TEMPERATURE, format_number(temperature)):
            temperature = None
    max_tokens = (
        int(clean_max_tokens(body["maxTokens"])) if body.get("maxTokens") is not None else None
    )
    if max_tokens is not None:
        _check_locked(F_MAX_TOKENS, str(max_tokens))
        if _unchanged(F_MAX_TOKENS, str(max_tokens)):
            max_tokens = None
    timeout = (
        float(clean_llm_timeout(body["timeoutSeconds"]))
        if body.get("timeoutSeconds") is not None
        else None
    )
    if timeout is not None:
        _check_locked(F_LLM_TIMEOUT, format_number(timeout))
        if _unchanged(F_LLM_TIMEOUT, format_number(timeout)):
            timeout = None

    secret = _read_secret_patch(body, F_LLM_KEY, context.effective.llm_key)
    return _LlmPatch(
        base_url=base_url,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout,
        secret=secret,
    )


def _read_optional_text(
    body: Mapping[str, Any],
    key: str,
    field: Field,
    clean: Callable[[object], str],
    *,
    normalise: Callable[[str], str] | None = None,
) -> str | None:
    if key not in body or body[key] is None:
        return None
    text = clean(body[key])
    _check_locked(field, text, normalise=normalise)
    if _unchanged(field, text, normalise=normalise):
        return None
    return text


def _read_search_patch(body: Mapping[str, Any], context: _Context) -> _SearchPatch:
    provider = clean_provider(body["provider"]) if body.get("provider") is not None else None
    if provider is not None:
        _check_locked(F_PROVIDER, provider, normalise=str.lower)
        if _unchanged(F_PROVIDER, provider, normalise=str.lower):
            provider = None
    mode = clean_search_mode(body["mode"]) if body.get("mode") is not None else None
    if mode is not None:
        _check_locked(F_SEARCH_MODE, mode)
        if _unchanged(F_SEARCH_MODE, mode):
            mode = None
    timeout = (
        float(clean_search_timeout(body["timeoutSeconds"]))
        if body.get("timeoutSeconds") is not None
        else None
    )
    if timeout is not None:
        _check_locked(F_SEARCH_TIMEOUT, format_number(timeout))
        if _unchanged(F_SEARCH_TIMEOUT, format_number(timeout)):
            timeout = None
    country = _read_optional_text(body, "country", F_COUNTRY, clean_country, normalise=str.upper)
    search_lang = _read_optional_text(
        body, "searchLang", F_LANG, lambda value: clean_lang(value, F_LANG.id)
    )
    ui_lang = _read_optional_text(
        body, "uiLang", F_UI_LANG, lambda value: clean_lang(value, F_UI_LANG.id)
    )

    secret = _read_secret_patch(body, F_SEARCH_KEY, context.effective.search_key)
    return _SearchPatch(
        provider=provider,
        mode=mode,
        timeout_seconds=timeout,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
        secret=secret,
    )


def _read_media(body: Mapping[str, Any]) -> bool | None:
    if "extractMedia" not in body or body["extractMedia"] is None:
        return None
    enabled = body["extractMedia"]
    if not isinstance(enabled, bool):
        raise _invalid(F_MEDIA.id, "extractMedia is not true or false")
    if F_MEDIA.env in process_env_keys() and _media_enabled() != enabled:
        raise _locked(F_MEDIA.id)
    return enabled


# --- writing -----------------------------------------------------------------


def _infer_provider(base_url: str) -> str:
    host = (urlsplit(base_url).hostname or "").lower() if base_url else ""
    if host == "api.openai.com":
        return "openai"
    if host.endswith("anthropic.com"):
        return "anthropic"
    if host.endswith("deepseek.com"):
        return "deepseek"
    if "dashscope" in host or host.endswith("aliyuncs.com"):
        return "qwen"
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return "ollama"
    return "custom"


def _profile_name(provider: str, base_url: str) -> str:
    if provider in _PROVIDER_NAMES:
        return _PROVIDER_NAMES[provider]
    host = (urlsplit(base_url).hostname or "") if base_url else ""
    return host or "Default"


def _new_profile_id(profiles: list[Profile]) -> str:
    taken = {profile.id for profile in profiles}
    index = 1
    while f"p_{index}" in taken:
        index += 1
    return f"p_{index}"


def _with_profile(config: Config, llm: _LlmPatch) -> Config:
    """Apply an endpoint change to the active profile, creating it if needed."""
    if llm.base_url is None and llm.model is None and not llm.secret:
        return config
    profiles = list(config.profiles)
    active = config.active_profile_id
    if active is None:
        base_url = llm.base_url or ""
        provider = _infer_provider(base_url)
        ident = _new_profile_id(profiles)
        profiles.append(
            Profile(
                id=ident,
                name=_profile_name(provider, base_url),
                provider=provider,
                base_url=base_url,
                model=llm.model or "",
            )
        )
        return replace(config, profiles=tuple(profiles), active_profile_id=ident)
    if llm.base_url is None and llm.model is None:
        return config
    updated = [
        replace(
            profile,
            base_url=llm.base_url if llm.base_url is not None else profile.base_url,
            model=llm.model if llm.model is not None else profile.model,
            provider=(
                _infer_provider(llm.base_url) if llm.base_url is not None else profile.provider
            ),
        )
        if profile.id == active
        else profile
        for profile in profiles
    ]
    return replace(config, profiles=tuple(updated))


def _with_generation(config: Config, llm: _LlmPatch) -> Config:
    current = config.generation
    return replace(
        config,
        generation=replace(
            current,
            temperature=llm.temperature if llm.temperature is not None else current.temperature,
            max_tokens=llm.max_tokens if llm.max_tokens is not None else current.max_tokens,
            timeout_seconds=(
                llm.timeout_seconds
                if llm.timeout_seconds is not None
                else current.timeout_seconds
            ),
        ),
    )


def _with_search(config: Config, search: _SearchPatch) -> Config:
    current = config.search
    return replace(
        config,
        search=replace(
            current,
            external_provider=(
                search.provider if search.provider is not None else current.external_provider
            ),
            mode=search.mode if search.mode is not None else current.mode,
            timeout_seconds=(
                search.timeout_seconds
                if search.timeout_seconds is not None
                else current.timeout_seconds
            ),
            country=search.country if search.country is not None else current.country,
            search_lang=(
                search.search_lang if search.search_lang is not None else current.search_lang
            ),
            ui_lang=search.ui_lang if search.ui_lang is not None else current.ui_lang,
        ),
    )


def _mutate(
    config: Config,
    llm: _LlmPatch,
    search: _SearchPatch,
    media: bool | None,
) -> Config:
    config = _with_profile(config, llm)
    config = _with_generation(config, llm)
    config = _with_search(config, search)
    return config if media is None else replace(config, extract_media=media)


def _write_secrets(store: ConfigStore, config: Config, llm: _LlmPatch, search: _SearchPatch) -> None:
    profile = config.active_profile
    if llm.secret is not None:
        if not llm.secret:
            if profile is not None:
                store.delete_llm_key(profile.id)
        elif profile is not None:
            store.set_llm_key(profile.id, llm.secret)
    if search.secret is not None:
        if search.secret:
            store.set_search_key(search.secret)
        else:
            store.delete_search_key()


# --- the screen's answer -----------------------------------------------------


def _key_set(store: ConfigStore, profile_id: str) -> bool:
    """Whether a key is stored for one profile. The key itself never leaves."""
    if not store.keychain_available():
        return False
    try:
        return bool(store.get_llm_key(profile_id))
    except CredentialStoreUnavailable:
        return False


def install_view(
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """What the screen shows. Keys are reported as set or not, never as text."""
    context = _context(path, credentials)
    invalid: list[str] = []
    base_url = _current(F_BASE) or ""
    model = _current(F_MODEL) or ""
    llm_key = _current(F_LLM_KEY) or ""
    temperature = _read_float(F_TEMPERATURE, DEFAULT_TEMPERATURE, invalid)
    max_tokens = _read_int(F_MAX_TOKENS, DEFAULT_MAX_TOKENS, invalid)
    llm_timeout = _read_float(F_LLM_TIMEOUT, DEFAULT_LLM_TIMEOUT, invalid)
    provider = (_current(F_PROVIDER) or "none").lower()
    if provider not in KNOWN_PROVIDERS:
        invalid.append(F_PROVIDER.id)
        provider = "none"
    search_key = _current(F_SEARCH_KEY) or ""
    search_timeout = _read_float(F_SEARCH_TIMEOUT, DEFAULT_SEARCH_TIMEOUT, invalid)
    mode = (_current(F_SEARCH_MODE) or context.config.search.mode).lower()
    if mode not in SEARCH_MODES:
        invalid.append(F_SEARCH_MODE.id)
        mode = "auto"
    config = context.config
    active_profile = config.active_profile
    return {
        "version": __version__,
        "llm": {
            "baseUrl": base_url,
            "apiKeySet": bool(llm_key),
            "model": model,
            "temperature": temperature,
            "maxTokens": max_tokens,
            "timeoutSeconds": llm_timeout,
            "configured": bool(base_url and model and llm_key),
        },
        "search": {
            "provider": provider,
            "apiKeySet": bool(search_key),
            "timeoutSeconds": search_timeout,
            "country": _current(F_COUNTRY) or "",
            "searchLang": _current(F_LANG) or "",
            "uiLang": _current(F_UI_LANG) or "",
            "mode": mode,
            "nativeAvailable": native_search_available(
                provider=active_profile.provider if active_profile else "",
                base_url=_current(F_BASE) or "",
            ),
            "configured": provider not in ("", "none") and bool(search_key),
        },
        "extractMedia": _media_enabled(),
        "locked": context.effective.locked(),
        "invalid": invalid,
        # Added alongside the shape above, for the profile-based screen.
        "schemaVersion": config.schema_version,
        "revision": config.revision,
        "configPath": str(context.store.path),
        "keychainAvailable": context.effective.keychain_available,
        "sources": context.effective.sources(),
        "profiles": [
            {
                "id": profile.id,
                "name": profile.name,
                "provider": profile.provider,
                "baseUrl": profile.base_url,
                "model": profile.model,
                "active": profile.id == config.active_profile_id,
                "apiKeySet": _key_set(context.store, profile.id),
            }
            for profile in config.profiles
        ],
        "activeProfileId": config.active_profile_id,
        "generation": {
            "temperature": config.generation.temperature,
            "maxTokens": config.generation.max_tokens,
            "timeoutSeconds": config.generation.timeout_seconds,
        },
    }


def apply_install(
    body: Mapping[str, Any],
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """Write the given fields and make this process use them immediately."""
    context = _context(path, credentials)
    expected = body.get("revision")
    if expected is not None and (
        isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
    ):
        raise _invalid("revision", "revision is not a whole number")
    llm_body = _mapping(body, "llm")
    search_body = _mapping(body, "search")
    llm = _read_llm_patch(llm_body, context) if llm_body is not None else _LlmPatch()
    search = _read_search_patch(search_body, context) if search_body is not None else _SearchPatch()
    media = _read_media(body)

    writes_secret = llm.secret is not None or search.secret is not None
    if writes_secret:
        # The credential store is checked before the document changes, so the
        # common failure (no backend) leaves nothing half-saved. A backend that
        # fails between the two writes still reaches the caller as an error; the
        # document keeps the revision that write gave it.
        context.store.require_keychain()

    updated = context.store.update(
        lambda current: _mutate(current, llm, search, media),
        expected_revision=expected,
        force=writes_secret,
    )
    if writes_secret:
        _write_secrets(context.store, updated, llm, search)
    return install_view(path=path, credentials=credentials)


# --- trial calls -------------------------------------------------------------


def _use(field: Field, new: str) -> str:
    """Accept *new* for a trial call, unless the environment owns a different value."""
    current = _current(field) or ""
    if field.env in process_env_keys() and new != current:
        raise _locked(field.id)
    return new


def _overlay_secret(body: Mapping[str, Any], field: Field) -> str:
    current = _current(field) or ""
    if body.get("apiKey"):
        return _use(field, clean_secret(body["apiKey"], field.id))
    if body.get("clearApiKey"):
        return _use(field, "")
    return current


def _llm_config_for(body: Mapping[str, Any]) -> LlmConfig:
    if "baseUrl" in body and body["baseUrl"] is not None:
        base = _use(F_BASE, clean_base_url(body["baseUrl"]))
    else:
        base = _current(F_BASE) or ""
    if "model" in body and body["model"] is not None:
        model = _use(F_MODEL, clean_model(body["model"]))
    else:
        model = _current(F_MODEL) or ""
    key = _overlay_secret(body, F_LLM_KEY)
    # A stored key belongs to the service it was saved for. If a caller could
    # name any address while keeping that key, this trial call would be a way to
    # send the key wherever the caller likes. A new endpoint needs a new key.
    if not body.get("apiKey"):
        _key_fits_origin(key, _current(F_BASE) or "", base)
    if "temperature" in body and body["temperature"] is not None:
        temperature = float(_use(F_TEMPERATURE, clean_temperature(body["temperature"])))
    else:
        temperature = _read_float(F_TEMPERATURE, DEFAULT_TEMPERATURE, [])
    if "maxTokens" in body and body["maxTokens"] is not None:
        max_tokens = int(_use(F_MAX_TOKENS, clean_max_tokens(body["maxTokens"])))
    else:
        max_tokens = _read_int(F_MAX_TOKENS, DEFAULT_MAX_TOKENS, [])
    if "timeoutSeconds" in body and body["timeoutSeconds"] is not None:
        timeout = float(_use(F_LLM_TIMEOUT, clean_llm_timeout(body["timeoutSeconds"])))
    else:
        timeout = _read_float(F_LLM_TIMEOUT, DEFAULT_LLM_TIMEOUT, [])
    if not base or not model or not key:
        raise LlmConfigError(
            "Missing LLM config: base URL, API key and model are required to test the connection."
        )
    return LlmConfig(
        base_url=base,
        api_key=key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_seconds=timeout,
    )


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(value: str) -> tuple[str, str, int] | None:
    """Scheme, host and effective port of a URL, or ``None`` when it has none.

    A key belongs to this, not to the whole address: another path under the same
    service still receives the same key, a different port or scheme must not.
    """
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if not scheme or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(scheme, 0)
    return scheme, host.lower(), port


def _key_fits_origin(key: str, saved_base: str, base: str) -> None:
    """A stored key may only travel to the origin it was saved for."""
    if key and _origin(base) != _origin(saved_base):
        raise Refusal(
            "settings.probeKeyRequired",
            "the stored API key belongs to a different service address; "
            "enter the key again for this one",
            status=409,
        )


def _search_config_for(body: Mapping[str, Any], context: _Context) -> SearchConfig:
    current_provider = (_current(F_PROVIDER) or "none").lower()
    if "provider" in body and body["provider"] is not None:
        provider = clean_provider(body["provider"])
        # Compare the choice, not the spelling: ``Brave`` and ``brave`` are one
        # provider, and a locked name should not fail a trial of its own value.
        if F_PROVIDER.env in process_env_keys() and provider != current_provider:
            raise _locked(F_PROVIDER.id)
    else:
        provider = current_provider
    key = _overlay_secret(body, F_SEARCH_KEY)
    if "timeoutSeconds" in body and body["timeoutSeconds"] is not None:
        timeout = float(_use(F_SEARCH_TIMEOUT, clean_search_timeout(body["timeoutSeconds"])))
    else:
        timeout = _read_float(F_SEARCH_TIMEOUT, DEFAULT_SEARCH_TIMEOUT, [])
    if "country" in body and body["country"] is not None:
        country = _use(F_COUNTRY, clean_country(body["country"]))
    else:
        country = _current(F_COUNTRY) or ""
    if "searchLang" in body and body["searchLang"] is not None:
        search_lang = _use(F_LANG, clean_lang(body["searchLang"], F_LANG.id))
    else:
        search_lang = _current(F_LANG) or ""
    if "uiLang" in body and body["uiLang"] is not None:
        ui_lang = _use(F_UI_LANG, clean_lang(body["uiLang"], F_UI_LANG.id))
    else:
        ui_lang = _current(F_UI_LANG) or ""
    if "mode" in body and body["mode"] is not None:
        mode = _use(F_SEARCH_MODE, clean_search_mode(body["mode"]))
    else:
        mode = clean_search_mode(_current(F_SEARCH_MODE) or "auto")
    if provider not in KNOWN_PROVIDERS:
        raise SearchConfigError(
            f"Unknown search provider {provider!r}. "
            f"Use one of: {', '.join(sorted(KNOWN_PROVIDERS))}."
        )
    profile = context.config.active_profile
    provider_id = profile.provider if profile else ""
    native = build_native_config(
        provider_id,
        _current(F_BASE) or "",
        _current(F_LLM_KEY) or "",
        _current(F_MODEL) or "",
        _read_float(F_LLM_TIMEOUT, DEFAULT_LLM_TIMEOUT, []),
    )
    return SearchConfig(
        provider=provider,
        api_key=key,
        timeout_seconds=timeout,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
        mode=mode,
        llm_provider=provider_id,
        native=native,
    )


def _probe_refusal(message: str, *, search: bool) -> Refusal:
    """Why the trial call failed, in the words the screen can say.

    The codes are written out here rather than passed in: the error-code check
    reads them out of the source, and one that only travelled as an argument
    would reach the screen with no sentence of its own.
    """
    found = _HTTP_STATUS.search(message)
    status_text = found.group(1) if found else ""
    params = {"status": status_text} if status_text else {}
    rejected = status_text in {"401", "403"}
    unreachable = "request failed" in message or message.startswith("LLM probe failed")
    if search:
        if rejected:
            return Refusal("settings.searchRejected", message[:300], params=params, status=502)
        if unreachable:
            return Refusal("settings.searchUnreachable", message[:300], params=params, status=502)
        return Refusal("settings.searchFailed", message[:300], params=params, status=502)
    if rejected:
        return Refusal("settings.probeRejected", message[:300], params=params, status=502)
    if unreachable:
        return Refusal("settings.probeUnreachable", message[:300], params=params, status=502)
    return Refusal("settings.probeFailed", message[:300], params=params, status=502)


def probe_llm(
    body: Mapping[str, Any] | None = None,
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
    poster: Callable[..., Any] | None = None,
) -> None:
    """Ask the model endpoint for one short answer, without saving.

    HTTP 200 is enough: a reasoning model may spend a tiny budget before it
    emits text, and the question here is whether the connection is accepted.
    """
    _context(path, credentials)
    config = _llm_config_for(body or {})
    timeout = min(config.timeout_seconds, _PROBE_TIMEOUT)
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": "Reply with ok."}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    try:
        if poster is not None:
            response = poster(config.chat_completions_url, headers=headers, json=payload, timeout=timeout)
        else:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(config.chat_completions_url, headers=headers, json=payload)
    except httpx.HTTPError as err:
        raise _probe_refusal(f"LLM probe failed: {err}", search=False) from err
    if response.status_code >= 400:
        raise _probe_refusal(
            f"LLM probe HTTP {response.status_code}: {response.text[:120]}",
            search=False,
        )


def probe_search(
    body: Mapping[str, Any] | None = None,
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
    runner: Callable[..., Any] | None = None,
) -> int:
    """Run one small search with the draft, without saving. Returns how many cards came back."""
    context = _context(path, credentials)
    config = _search_config_for(body or {}, context)
    external_needed = config.mode == "external" or (
        config.mode == "auto" and config.native is None
    )
    if external_needed:
        if config.provider in ("", "none"):
            raise Refusal("settings.searchOff", "search provider is none", status=400)
        if not config.api_key:
            raise ProviderNotConfiguredError(
                "web_search is not configured. Set NOTEAGENT_SEARCH_PROVIDER "
                "and NOTEAGENT_SEARCH_API_KEY."
            )
    try:
        if runner is not None:
            run = runner(_PROBE_QUERY, top_k=3, config=config)
        else:
            run = run_web_search(_PROBE_QUERY, top_k=3, config=config, caller="settings")
    except SearchError as err:
        raise _probe_refusal(str(err), search=True) from err
    return len(run.cards)


# --- model discovery ---------------------------------------------------------


def _models_refusal(status: int | None, message: str) -> Refusal:
    """Why a model list did not arrive, in the words the screen can say."""
    params = {"status": str(status)} if status is not None else {}
    if status in (401, 403):
        return Refusal("settings.probeRejected", message[:300], params=params, status=502)
    if status == 404:
        return Refusal("settings.modelsUnavailable", message[:300], params=params, status=502)
    if status is None:
        return Refusal("settings.probeUnreachable", message[:300], params=params, status=502)
    return Refusal("settings.probeFailed", message[:300], params=params, status=502)


def _model_ids(payload: object) -> list[str]:
    """Model names from the shapes compatible endpoints use, in their order."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, Mapping):
        items = payload.get("data") or payload.get("models") or []
    else:
        items = []
    if not isinstance(items, list):
        return []
    found: list[str] = []
    for item in items:
        if isinstance(item, str):
            ident = item
        elif isinstance(item, Mapping):
            ident = item.get("id") or item.get("name") or ""
        else:
            continue
        text = str(ident).strip()
        if text and text not in found:
            found.append(text)
    return found[:_MODELS_LIMIT]


def _models_key(
    body: Mapping[str, Any],
    context: _Context,
    profile: Profile | None,
    base: str,
) -> str:
    """The key this request may use, and only for the origin it belongs to."""
    if body.get("apiKey"):
        return clean_secret(body["apiKey"], F_LLM_KEY.id)

    key = ""
    bound_base = ""
    if profile is not None and context.store.keychain_available():
        stored = context.store.get_llm_key(profile.id)
        if stored:
            key, bound_base = stored, profile.base_url
    if not key:
        effective = _current(F_LLM_KEY) or ""
        if effective:
            key, bound_base = effective, _current(F_BASE) or ""
    # A key never travels to another origin; the request itself still may, so a
    # local endpoint is asked without one instead of being blocked by an
    # unrelated saved key.
    if key and _origin(base) != _origin(bound_base):
        return ""
    return key


def list_models(
    body: Mapping[str, Any] | None = None,
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
    getter: Callable[..., Any] | None = None,
) -> list[str]:
    """Ask a connection's endpoint which models it offers.

    The key comes from the draft, the selected profile, or the effective
    configuration, and never travels to an origin it was not saved for. A
    suggestion list is optional: an endpoint that cannot answer is a refusal
    the screen words, not a reason to block saving.
    """
    context = _context(path, credentials)
    body = body or {}

    profile: Profile | None = None
    profile_id = body.get("profileId")
    if profile_id is not None:
        if not isinstance(profile_id, str) or not profile_id:
            raise _invalid("profileId", "profile id is not text")
        profile = next(
            (item for item in context.config.profiles if item.id == profile_id),
            None,
        )
        if profile is None:
            raise Refusal(
                "settings.profileNameNotFound",
                f"no connection profile has id {profile_id!r}",
                params={"id": profile_id},
                status=404,
            )

    if body.get("baseUrl") is not None:
        base = clean_base_url(body["baseUrl"])
    elif profile is not None:
        base = profile.base_url
    else:
        base = _current(F_BASE) or ""
    if not base:
        raise _invalid(F_BASE.id, "a service address is required to list models")

    raw_provider = body.get("provider")
    if raw_provider is None and profile is not None:
        raw_provider = profile.provider
    provider = "custom"
    if raw_provider is not None:
        if not isinstance(raw_provider, str):
            raise _invalid("provider", "the provider is not text")
        provider = raw_provider.strip().lower()
        if provider not in PROVIDERS:
            raise _invalid("provider", "the provider is not one this build knows")

    key = _models_key(body, context, profile, base)
    headers = {"Accept": "application/json"}
    if provider == "anthropic":
        if key:
            headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    elif key:
        headers["Authorization"] = f"Bearer {key}"

    url = f"{base.rstrip('/')}/models"
    try:
        if getter is not None:
            response = getter(url, headers=headers, timeout=_MODELS_TIMEOUT)
        else:
            with httpx.Client(timeout=_MODELS_TIMEOUT) as client:
                response = client.get(url, headers=headers)
    except httpx.HTTPError as err:
        raise _models_refusal(None, f"Model list request failed: {err}") from err
    if response.status_code >= 400:
        raise _models_refusal(
            response.status_code,
            f"Model list HTTP {response.status_code}: {response.text[:120]}",
        )
    try:
        payload = response.json()
    except ValueError as err:
        raise Refusal(
            "settings.modelsUnavailable",
            f"Model list was not JSON: {response.text[:120]}",
            status=502,
        ) from err
    return _model_ids(payload)
