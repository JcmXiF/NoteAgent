"""Install settings: one model connection and one search setup for this computer.

They live in the ``.env`` the process already reads, so the command line and
the screen cannot drift into two configurations. A name that was already in
the environment when the process started is left alone — the file has never
overridden those, and saving must not pretend otherwise.

Secrets are never returned. An empty key field means "leave it"; clearing is
a separate, explicit request.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
)
from noteagent.acquire.types import ProviderNotConfiguredError
from noteagent.llm.config import LlmConfig, LlmConfigError, load_dotenv, process_env_keys
from noteagent.settings.envfile import dotenv_path, update_env_text, write_env_file
from noteagent.util.refusal import Refusal

_HEADER = "# NOTEAGENT settings. The app writes values here and keeps comments.\n"

_PROBE_TIMEOUT = 20.0
_PROBE_QUERY = "example"

_DEFAULT_TEMPERATURE = 0.1
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_LLM_TIMEOUT = 120.0
_DEFAULT_SEARCH_TIMEOUT = 30.0

_LANG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_HTTP_STATUS = re.compile(r"HTTP (\d+)")


@dataclass(frozen=True, slots=True)
class _Field:
    """One value the screen can edit, and the environment name that stores it."""

    id: str
    env: str


F_BASE = _Field("llm.baseUrl", "NOTEAGENT_LLM_BASE_URL")
F_LLM_KEY = _Field("llm.apiKey", "NOTEAGENT_LLM_API_KEY")
F_MODEL = _Field("llm.model", "NOTEAGENT_LLM_MODEL")
F_TEMPERATURE = _Field("llm.temperature", "NOTEAGENT_LLM_TEMPERATURE")
F_MAX_TOKENS = _Field("llm.maxTokens", "NOTEAGENT_LLM_MAX_TOKENS")
F_LLM_TIMEOUT = _Field("llm.timeoutSeconds", "NOTEAGENT_LLM_TIMEOUT")
F_PROVIDER = _Field("search.provider", "NOTEAGENT_SEARCH_PROVIDER")
F_SEARCH_KEY = _Field("search.apiKey", "NOTEAGENT_SEARCH_API_KEY")
F_SEARCH_TIMEOUT = _Field("search.timeoutSeconds", "NOTEAGENT_SEARCH_TIMEOUT")
F_COUNTRY = _Field("search.country", "NOTEAGENT_SEARCH_COUNTRY")
F_LANG = _Field("search.searchLang", "NOTEAGENT_SEARCH_LANG")
F_UI_LANG = _Field("search.uiLang", "NOTEAGENT_SEARCH_UI_LANG")
F_MEDIA = _Field("extractMedia", "NOTEAGENT_EXTRACT_MEDIA")

_FIELDS = (
    F_BASE,
    F_LLM_KEY,
    F_MODEL,
    F_TEMPERATURE,
    F_MAX_TOKENS,
    F_LLM_TIMEOUT,
    F_PROVIDER,
    F_SEARCH_KEY,
    F_SEARCH_TIMEOUT,
    F_COUNTRY,
    F_LANG,
    F_UI_LANG,
    F_MEDIA,
)

MANAGED_ENV_NAMES = frozenset(field.env for field in _FIELDS)


def _invalid(field: str, message: str) -> Refusal:
    return Refusal("settings.invalid", message, params={"field": field}, status=400)


def _locked(field: str) -> Refusal:
    return Refusal(
        "settings.locked",
        f"{field} is set in the process environment; the settings file cannot change it",
        params={"field": field},
        status=409,
    )


def _media_enabled() -> bool:
    """Whether image extraction is on. Same rule as ``media_extract_enabled``."""
    from noteagent.acquire.media.extract import media_extract_enabled

    return media_extract_enabled()


def _prepare(env_file: Path | None) -> Path:
    """Read the settings file this call is about, and no other one.

    An explicit path must not walk upward: a test, or a write aimed at one
    file, would otherwise adopt a ``.env`` that happened to sit above it.
    """
    if env_file is not None:
        path = Path(env_file)
        load_dotenv(path.parent, walk=False)
        return path
    load_dotenv()
    return dotenv_path()


def _current(field: _Field) -> str | None:
    """The effective value, or ``None`` when the process has no such name."""
    if field.env not in os.environ:
        return None
    return os.environ[field.env].strip()


def _stage(changes: dict[str, str | None], field: _Field, new: str | None) -> None:
    """Record one change, or refuse it when the environment owns the name.

    ``None`` deletes the name. A value equal to what is already in effect is
    not a change, including on a locked name — saving the form again must not
    fail just because the process started with that value.
    """
    current = _current(field)
    if new is None:
        if current is None:
            return
        if field.env in process_env_keys():
            raise _locked(field.id)
        changes[field.env] = None
        return
    if current == new:
        return
    if field.env in process_env_keys():
        raise _locked(field.id)
    changes[field.env] = new


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field, f"{field} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise _invalid(field, f"{field} is not a finite number")
    return number


def _format_number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


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
    return _format_number(number)


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
    return _format_number(number)


def clean_search_timeout(value: object) -> str:
    number = _finite(value, F_SEARCH_TIMEOUT.id)
    if number < 1 or number > 120:
        raise _invalid(F_SEARCH_TIMEOUT.id, "search timeout is outside 1..120")
    return _format_number(number)


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


def _read_float(field: _Field, default: float, invalid: list[str]) -> float:
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


def _read_int(field: _Field, default: int, invalid: list[str]) -> int:
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


def _collect_llm(body: Mapping[str, Any], changes: dict[str, str | None]) -> None:
    if "baseUrl" in body and body["baseUrl"] is not None:
        _stage(changes, F_BASE, clean_base_url(body["baseUrl"]))
    # A replacement wins over a clear sent in the same request: the field the
    # person just typed is the one they mean to keep.
    if body.get("apiKey"):
        _stage(changes, F_LLM_KEY, clean_secret(body["apiKey"], F_LLM_KEY.id))
    elif body.get("clearApiKey"):
        _stage(changes, F_LLM_KEY, None)
    if "model" in body and body["model"] is not None:
        _stage(changes, F_MODEL, clean_model(body["model"]))
    if "temperature" in body and body["temperature"] is not None:
        _stage(changes, F_TEMPERATURE, clean_temperature(body["temperature"]))
    if "maxTokens" in body and body["maxTokens"] is not None:
        _stage(changes, F_MAX_TOKENS, clean_max_tokens(body["maxTokens"]))
    if "timeoutSeconds" in body and body["timeoutSeconds"] is not None:
        _stage(changes, F_LLM_TIMEOUT, clean_llm_timeout(body["timeoutSeconds"]))


def _collect_optional_text(
    changes: dict[str, str | None],
    field: _Field,
    body: Mapping[str, Any],
    key: str,
    clean: Callable[[object], str],
) -> None:
    if key not in body or body[key] is None:
        return
    text = clean(body[key])
    # An empty optional is the default, so the file does not keep a blank line.
    _stage(changes, field, text or None)


def _collect_search(body: Mapping[str, Any], changes: dict[str, str | None]) -> None:
    if "provider" in body and body["provider"] is not None:
        _stage(changes, F_PROVIDER, clean_provider(body["provider"]))
    if body.get("apiKey"):
        _stage(changes, F_SEARCH_KEY, clean_secret(body["apiKey"], F_SEARCH_KEY.id))
    elif body.get("clearApiKey"):
        _stage(changes, F_SEARCH_KEY, None)
    if "timeoutSeconds" in body and body["timeoutSeconds"] is not None:
        _stage(changes, F_SEARCH_TIMEOUT, clean_search_timeout(body["timeoutSeconds"]))
    _collect_optional_text(changes, F_COUNTRY, body, "country", clean_country)
    _collect_optional_text(changes, F_LANG, body, "searchLang", lambda value: clean_lang(value, F_LANG.id))
    _collect_optional_text(
        changes, F_UI_LANG, body, "uiLang", lambda value: clean_lang(value, F_UI_LANG.id)
    )


def _apply_process(changes: Mapping[str, str | None]) -> None:
    for key, value in changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def install_view(*, env_file: Path | None = None) -> dict[str, Any]:
    """What the screen shows. Keys are reported as set or not, never as text."""
    _prepare(env_file)
    invalid: list[str] = []
    base_url = _current(F_BASE) or ""
    model = _current(F_MODEL) or ""
    llm_key = _current(F_LLM_KEY) or ""
    temperature = _read_float(F_TEMPERATURE, _DEFAULT_TEMPERATURE, invalid)
    max_tokens = _read_int(F_MAX_TOKENS, _DEFAULT_MAX_TOKENS, invalid)
    llm_timeout = _read_float(F_LLM_TIMEOUT, _DEFAULT_LLM_TIMEOUT, invalid)
    provider = (_current(F_PROVIDER) or "none").lower()
    if provider not in KNOWN_PROVIDERS:
        invalid.append(F_PROVIDER.id)
        provider = "none"
    search_key = _current(F_SEARCH_KEY) or ""
    search_timeout = _read_float(F_SEARCH_TIMEOUT, _DEFAULT_SEARCH_TIMEOUT, invalid)
    locked = [field.id for field in _FIELDS if field.env in process_env_keys()]
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
            "configured": provider not in ("", "none") and bool(search_key),
        },
        "extractMedia": _media_enabled(),
        "locked": locked,
        "invalid": invalid,
    }


def apply_install(body: Mapping[str, Any], *, env_file: Path | None = None) -> dict[str, Any]:
    """Write the given fields and make this process use them immediately."""
    path = _prepare(env_file)
    changes: dict[str, str | None] = {}
    llm = _mapping(body, "llm")
    if llm is not None:
        _collect_llm(llm, changes)
    search = _mapping(body, "search")
    if search is not None:
        _collect_search(search, changes)
    if "extractMedia" in body and body["extractMedia"] is not None:
        enabled = body["extractMedia"]
        if not isinstance(enabled, bool):
            raise _invalid(F_MEDIA.id, "extractMedia is not true or false")
        if _media_enabled() != enabled:
            _stage(changes, F_MEDIA, "1" if enabled else "0")
    if changes:
        text = path.read_text(encoding="utf-8") if path.is_file() else _HEADER
        write_env_file(path, update_env_text(text, changes))
        _apply_process(changes)
    return install_view(env_file=path)


def _use(field: _Field, new: str) -> str:
    """Accept *new* for a trial call, unless the environment owns a different value."""
    current = _current(field) or ""
    if field.env in process_env_keys() and new != current:
        raise _locked(field.id)
    return new


def _overlay_secret(body: Mapping[str, Any], field: _Field) -> str:
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
    if "temperature" in body and body["temperature"] is not None:
        temperature = float(_use(F_TEMPERATURE, clean_temperature(body["temperature"])))
    else:
        temperature = _read_float(F_TEMPERATURE, _DEFAULT_TEMPERATURE, [])
    if "maxTokens" in body and body["maxTokens"] is not None:
        max_tokens = int(_use(F_MAX_TOKENS, clean_max_tokens(body["maxTokens"])))
    else:
        max_tokens = _read_int(F_MAX_TOKENS, _DEFAULT_MAX_TOKENS, [])
    if "timeoutSeconds" in body and body["timeoutSeconds"] is not None:
        timeout = float(_use(F_LLM_TIMEOUT, clean_llm_timeout(body["timeoutSeconds"])))
    else:
        timeout = _read_float(F_LLM_TIMEOUT, _DEFAULT_LLM_TIMEOUT, [])
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


def _search_config_for(body: Mapping[str, Any]) -> SearchConfig:
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
        timeout = _read_float(F_SEARCH_TIMEOUT, _DEFAULT_SEARCH_TIMEOUT, [])
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
    if provider not in KNOWN_PROVIDERS:
        raise SearchConfigError(
            f"Unknown NOTEAGENT_SEARCH_PROVIDER={provider!r}. "
            f"Use one of: {', '.join(sorted(KNOWN_PROVIDERS))}."
        )
    return SearchConfig(
        provider=provider,
        api_key=key,
        timeout_seconds=timeout,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
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
    env_file: Path | None = None,
    poster: Callable[..., Any] | None = None,
) -> None:
    """Ask the model endpoint for one short answer, without saving.

    HTTP 200 is enough: a reasoning model may spend a tiny budget before it
    emits text, and the question here is whether the connection is accepted.
    """
    _prepare(env_file)
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
    env_file: Path | None = None,
    runner: Callable[..., Any] | None = None,
) -> int:
    """Run one small search with the draft, without saving. Returns how many cards came back."""
    _prepare(env_file)
    config = _search_config_for(body or {})
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
