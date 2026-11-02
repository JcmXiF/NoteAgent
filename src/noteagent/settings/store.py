"""The installation configuration: one versioned JSON document, one writer.

A connection profile is a name, an endpoint and a model; the secrets that go
with it live in the credential store (``credentials.py``), never in the file.
One profile is active. Generation parameters are global, because they describe
how this installation writes, not how one endpoint answers.

Every save is one transaction: the file is read and replaced while holding a
lock, and the revision in the file grows by one. A caller that read an older
revision is refused rather than allowed to overwrite a change it never saw.
The write itself goes through ``io.atomic``, so a reader sees the old document
or the new one and never a half-written file.

The module is deliberately usable with an injected path and credential store:
the rules below are the test seam, and the API is only one caller.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from noteagent.io.atomic import write_text_atomic
from noteagent.settings.credentials import (
    SEARCH_KEY,
    CredentialStore,
    CredentialStoreUnavailable,
    llm_key,
)
from noteagent.util.refusal import Refusal

SCHEMA_VERSION = 1

#: Canonical provider ids. A preset may add an endpoint and a suggested model,
#: but the stored profile only needs to say which one it came from.
PROVIDERS = frozenset({"openai", "anthropic", "deepseek", "qwen", "ollama", "custom"})

SEARCH_MODES = frozenset({"auto", "native", "external"})
EXTERNAL_PROVIDERS = frozenset({"none", "brave", "serper"})

DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_TOKENS = 4096
DEFAULT_LLM_TIMEOUT = 120.0
DEFAULT_SEARCH_TIMEOUT = 30.0

_PID = r"[A-Za-z0-9_-]{1,80}"
_LANG = r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*"

#: How long a save waits for another writer before saying it is busy.
LOCK_TIMEOUT = 5.0


def format_number(value: float) -> str:
    """A number as the environment stores it: no trailing zeros, never empty."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _reject_secret(raw: Mapping[str, Any], field: str) -> None:
    for secret in ("apiKey", "api_key", "secret"):
        if secret in raw:
            raise _config_invalid(f"{field}.{secret}", "a secret must not be stored in the configuration")


@dataclass(frozen=True, slots=True)
class Profile:
    """One named model endpoint."""

    id: str
    name: str
    provider: str
    base_url: str
    model: str


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    """How this installation writes, whichever profile is active."""

    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT


@dataclass(frozen=True, slots=True)
class SearchSettings:
    """Retrieval settings, stored beside the model they are used with."""

    mode: str = "auto"
    external_provider: str = "none"
    timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT
    country: str = ""
    search_lang: str = ""
    ui_lang: str = ""


@dataclass(frozen=True, slots=True)
class Config:
    """The whole document, validated and ready to use."""

    profiles: tuple[Profile, ...] = ()
    active_profile_id: str | None = None
    generation: GenerationSettings = GenerationSettings()
    search: SearchSettings = SearchSettings()
    extract_media: bool = True
    revision: int = 0
    schema_version: int = SCHEMA_VERSION

    @property
    def active_profile(self) -> Profile | None:
        return next((p for p in self.profiles if p.id == self.active_profile_id), None)


def default_config() -> Config:
    return Config()


def default_config_path() -> Path:
    """The per-user configuration file.

    ``NOTEAGENT_CONFIG_DIR`` exists for portable installs and for tests; without
    it the platform's own application-data directory is used, so the app never
    writes beside a project or a working directory.
    """
    override = os.environ.get("NOTEAGENT_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser() / "config.json"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
        return root / "NoteAgent" / "config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "NoteAgent" / "config.json"
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".config"
    return root / "noteagent" / "config.json"


def _config_invalid(field: str, message: str) -> Refusal:
    return Refusal(
        "settings.configInvalid",
        f"configuration is invalid at {field}: {message}",
        params={"field": field},
        status=500,
    )


def _config_version(found: object) -> Refusal:
    return Refusal(
        "settings.configVersion",
        f"configuration schema version {found!r} is not supported",
        params={"found": str(found), "supported": str(SCHEMA_VERSION)},
        status=500,
    )


# --- reading -----------------------------------------------------------------


def _text(value: object, field: str, *, limit: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _config_invalid(field, "expected text")
    text = value.strip()
    if not text and not allow_empty:
        raise _config_invalid(field, "must not be empty")
    if len(text) > limit or any(ch in text for ch in "\r\n\x00"):
        raise _config_invalid(field, "is not a single usable line")
    return text


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _config_invalid(field, "expected a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise _config_invalid(field, "expected a finite number")
    return number


def _url(value: object, field: str) -> str:
    text = _text(value, field, limit=500, allow_empty=True).rstrip("/")
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _config_invalid(field, "must be an http(s) URL")
    if any(ch.isspace() for ch in text):
        raise _config_invalid(field, "must be one URL")
    return text


def _parse_profile(raw: object, index: int) -> Profile:
    field = f"profiles[{index}]"
    if not isinstance(raw, Mapping):
        raise _config_invalid(field, "expected an object")
    _reject_secret(raw, field)
    ident = _text(raw.get("id"), f"{field}.id", limit=80)
    if not re.fullmatch(_PID, ident):
        raise _config_invalid(f"{field}.id", "contains characters a profile id cannot use")
    name = _text(raw.get("name"), f"{field}.name", limit=120)
    provider = _text(raw.get("provider"), f"{field}.provider", limit=40).lower()
    if provider not in PROVIDERS:
        raise _config_invalid(f"{field}.provider", f"must be one of {', '.join(sorted(PROVIDERS))}")
    base_url = _url(raw.get("baseUrl"), f"{field}.baseUrl")
    model = _text(raw.get("model"), f"{field}.model", limit=200, allow_empty=True)
    return Profile(id=ident, name=name, provider=provider, base_url=base_url, model=model)


def _parse_generation(raw: object) -> GenerationSettings:
    if raw is None:
        return GenerationSettings()
    if not isinstance(raw, Mapping):
        raise _config_invalid("generation", "expected an object")
    temperature = _number(raw.get("temperature", DEFAULT_TEMPERATURE), "generation.temperature")
    if temperature < 0 or temperature > 2:
        raise _config_invalid("generation.temperature", "must be between 0 and 2")
    max_tokens = _number(raw.get("maxTokens", DEFAULT_MAX_TOKENS), "generation.maxTokens")
    if not max_tokens.is_integer() or not 1 <= int(max_tokens) <= 128_000:
        raise _config_invalid("generation.maxTokens", "must be a whole number between 1 and 128000")
    timeout = _number(raw.get("timeoutSeconds", DEFAULT_LLM_TIMEOUT), "generation.timeoutSeconds")
    if timeout < 5 or timeout > 600:
        raise _config_invalid("generation.timeoutSeconds", "must be between 5 and 600")
    return GenerationSettings(
        temperature=temperature,
        max_tokens=int(max_tokens),
        timeout_seconds=timeout,
    )


def _parse_search(raw: object) -> SearchSettings:
    if raw is None:
        return SearchSettings()
    if not isinstance(raw, Mapping):
        raise _config_invalid("search", "expected an object")
    _reject_secret(raw, "search")
    mode = _text(raw.get("mode", "auto"), "search.mode", limit=20).lower()
    if mode not in SEARCH_MODES:
        raise _config_invalid("search.mode", f"must be one of {', '.join(sorted(SEARCH_MODES))}")
    provider = _text(raw.get("externalProvider", "none"), "search.externalProvider", limit=20).lower()
    if provider not in EXTERNAL_PROVIDERS:
        raise _config_invalid(
            "search.externalProvider",
            f"must be one of {', '.join(sorted(EXTERNAL_PROVIDERS))}",
        )
    timeout = _number(raw.get("timeoutSeconds", DEFAULT_SEARCH_TIMEOUT), "search.timeoutSeconds")
    if timeout < 1 or timeout > 120:
        raise _config_invalid("search.timeoutSeconds", "must be between 1 and 120")
    country = _text(raw.get("country", ""), "search.country", limit=2, allow_empty=True).upper()
    if country and (len(country) != 2 or not country.isalpha()):
        raise _config_invalid("search.country", "must be two letters or empty")
    search_lang = _text(raw.get("searchLang", ""), "search.searchLang", limit=20, allow_empty=True)
    ui_lang = _text(raw.get("uiLang", ""), "search.uiLang", limit=20, allow_empty=True)
    for value, field in ((search_lang, "search.searchLang"), (ui_lang, "search.uiLang")):
        if value and not re.fullmatch(_LANG, value):
            raise _config_invalid(field, "is not a language code")
    return SearchSettings(
        mode=mode,
        external_provider=provider,
        timeout_seconds=timeout,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
    )


def parse_config(raw: object, *, revision: int = 0) -> Config:
    """Validate a decoded document. Unknown keys are ignored for forward safety."""
    if not isinstance(raw, Mapping):
        raise _config_invalid("document", "expected an object")
    for secret in ("apiKey", "api_key", "secret", "llmKey"):
        if secret in raw:
            raise _config_invalid(secret, "a secret must not be stored in the configuration")
    version = raw.get("schemaVersion")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise _config_version(version)
    stored_revision = raw.get("revision", revision)
    if isinstance(stored_revision, bool) or not isinstance(stored_revision, int) or stored_revision < 0:
        raise _config_invalid("revision", "expected a whole number")
    profiles_raw = raw.get("profiles", [])
    if not isinstance(profiles_raw, list):
        raise _config_invalid("profiles", "expected a list")
    profiles = tuple(_parse_profile(item, index) for index, item in enumerate(profiles_raw))
    seen: set[str] = set()
    for profile in profiles:
        if profile.id in seen:
            raise _config_invalid("profiles", f"duplicate id {profile.id!r}")
        seen.add(profile.id)
    active = raw.get("activeProfileId")
    if active is not None:
        active = _text(active, "activeProfileId", limit=80)
        if active not in seen:
            raise _config_invalid("activeProfileId", "names no profile in this document")
    if profiles and active is None:
        raise _config_invalid("activeProfileId", "exactly one profile must be active")
    if not profiles and active is not None:
        raise _config_invalid("activeProfileId", "no profile can be active when none exist")
    extract_media = raw.get("extractMedia", True)
    if not isinstance(extract_media, bool):
        raise _config_invalid("extractMedia", "expected true or false")
    return Config(
        profiles=profiles,
        active_profile_id=active,
        generation=_parse_generation(raw.get("generation")),
        search=_parse_search(raw.get("search")),
        extract_media=extract_media,
        revision=stored_revision,
        schema_version=version,
    )


def dump_config(config: Config) -> str:
    """The document as it is written. It never contains a secret."""
    payload = {
        "schemaVersion": config.schema_version,
        "revision": config.revision,
        "profiles": [
            {
                "id": profile.id,
                "name": profile.name,
                "provider": profile.provider,
                "baseUrl": profile.base_url,
                "model": profile.model,
            }
            for profile in config.profiles
        ],
        "activeProfileId": config.active_profile_id,
        "generation": {
            "temperature": config.generation.temperature,
            "maxTokens": config.generation.max_tokens,
            "timeoutSeconds": config.generation.timeout_seconds,
        },
        "search": {
            "mode": config.search.mode,
            "externalProvider": config.search.external_provider,
            "timeoutSeconds": config.search.timeout_seconds,
            "country": config.search.country,
            "searchLang": config.search.search_lang,
            "uiLang": config.search.ui_lang,
        },
        "extractMedia": config.extract_media,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# --- active profile rules ----------------------------------------------------


def _profile_not_found(profile_id: str) -> Refusal:
    return Refusal(
        "settings.profileNameNotFound",
        f"no connection profile has id {profile_id!r}",
        params={"id": profile_id},
        status=404,
    )


def set_active_profile(config: Config, profile_id: str) -> Config:
    """Mark one existing profile active. There is no other way to change it."""
    if not any(profile.id == profile_id for profile in config.profiles):
        raise _profile_not_found(profile_id)
    return replace(config, active_profile_id=profile_id)


def remove_profile(config: Config, profile_id: str) -> Config:
    """Drop one profile and keep exactly one active while any remain.

    Deleting the active profile selects the one after it, or the one before it
    when it was last. Deleting the last profile leaves the empty state.
    """
    index = next((i for i, profile in enumerate(config.profiles) if profile.id == profile_id), None)
    if index is None:
        raise _profile_not_found(profile_id)
    remaining = list(config.profiles)
    del remaining[index]
    if not remaining:
        active = None
    elif config.active_profile_id != profile_id:
        active = config.active_profile_id
    else:
        active = remaining[min(index, len(remaining) - 1)].id
    return replace(config, profiles=tuple(remaining), active_profile_id=active)


# --- credential access -------------------------------------------------------


class ConfigStore:
    """The configuration file and the credentials that go with it."""

    def __init__(
        self,
        path: Path,
        credentials: CredentialStore,
        *,
        lock_timeout: float = LOCK_TIMEOUT,
    ) -> None:
        self.path = Path(path)
        self.credentials = credentials
        self.lock_timeout = lock_timeout

    def exists(self) -> bool:
        return self.path.is_file()

    def keychain_available(self) -> bool:
        try:
            return bool(self.credentials.available())
        except Exception:
            return False

    def require_keychain(self) -> None:
        if not self.keychain_available():
            raise CredentialStoreUnavailable()

    def read(self) -> Config:
        """The stored document, or the empty state when the file does not exist."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default_config()
        except OSError as err:
            raise _config_invalid("document", f"the file cannot be read ({err.strerror or err})") from err
        try:
            raw = json.loads(text)
        except ValueError as err:
            raise _config_invalid("document", "the file is not valid JSON") from err
        return parse_config(raw)

    def update(
        self,
        mutate: Callable[[Config], Config],
        *,
        expected_revision: int | None = None,
        force: bool = False,
    ) -> Config:
        """Read, change and write under one lock, refusing a stale revision.

        ``force`` records a change that lives outside the document (a key in the
        credential store) so a secret-only save still advances the revision.
        """
        with _locked(self.path, self.lock_timeout):
            current = self.read()
            if expected_revision is not None and expected_revision != current.revision:
                raise Refusal(
                    "settings.staleRevision",
                    "the configuration changed since this draft was read",
                    params={"expected": str(expected_revision), "current": str(current.revision)},
                    status=409,
                )
            changed = mutate(current)
            if changed == current and not force:
                return current
            next_config = replace(changed, revision=current.revision + 1)
            write_text_atomic(self.path, dump_config(next_config))
            return next_config

    # Secrets are addressed by the key they belong to, never by their content.

    def get_llm_key(self, profile_id: str) -> str:
        return self._get(llm_key(profile_id))

    def set_llm_key(self, profile_id: str, value: str) -> None:
        self._set(llm_key(profile_id), value)

    def delete_llm_key(self, profile_id: str) -> None:
        self._delete(llm_key(profile_id))

    def get_search_key(self) -> str:
        return self._get(SEARCH_KEY)

    def set_search_key(self, value: str) -> None:
        self._set(SEARCH_KEY, value)

    def delete_search_key(self) -> None:
        self._delete(SEARCH_KEY)

    def delete_profile(self, profile_id: str, *, expected_revision: int | None = None) -> Config:
        """Remove a profile, its key and its claim on the active slot."""
        self.require_keychain()
        config = self.update(
            lambda current: remove_profile(current, profile_id),
            expected_revision=expected_revision,
            force=True,
        )
        self.delete_llm_key(profile_id)
        return config

    def _get(self, key: str) -> str:
        if not self.keychain_available():
            raise CredentialStoreUnavailable()
        value = self.credentials.get(key)
        return value or ""

    def _set(self, key: str, value: str) -> None:
        if not value:
            self._delete(key)
            return
        self.require_keychain()
        self.credentials.set(key, value)

    def _delete(self, key: str) -> None:
        self.require_keychain()
        self.credentials.delete(key)


# --- the lock ----------------------------------------------------------------

_thread_locks: dict[str, threading.Lock] = {}
_thread_guard = threading.Lock()


def _thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _thread_guard:
        return _thread_locks.setdefault(key, threading.Lock())


@contextmanager
def _locked(path: Path, timeout: float) -> Iterator[None]:
    """One writer at a time, across threads and across processes.

    An OS file lock alone is not enough on every platform: some implementations
    treat locks as owned by the process, so two threads would pass each other.
    The per-path thread lock closes that hole; the file lock covers a second
    window or a CLI.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise _config_invalid("document", f"the configuration folder cannot be created ({err})") from err
    thread_lock = _thread_lock(path)
    if not thread_lock.acquire(timeout=timeout):
        raise _busy(path)
    try:
        with _os_lock(path, timeout):
            yield
    finally:
        thread_lock.release()


def _busy(path: Path) -> Refusal:
    return Refusal(
        "settings.writeBusy",
        f"another writer holds {path}",
        status=503,
    )


@contextmanager
def _os_lock(path: Path, timeout: float) -> Iterator[None]:
    lock_path = path.with_name(f"{path.name}.lock")
    handle = open(lock_path, "a+b")
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(handle)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise _busy(path) from None
                time.sleep(0.02)
        yield
    finally:
        if acquired:
            try:
                _unlock(handle)
            except OSError:
                pass
        handle.close()


def _try_lock(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        if not handle.read(1):
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "DEFAULT_LLM_TIMEOUT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_SEARCH_TIMEOUT",
    "DEFAULT_TEMPERATURE",
    "EXTERNAL_PROVIDERS",
    "PROVIDERS",
    "SCHEMA_VERSION",
    "SEARCH_MODES",
    "Config",
    "ConfigStore",
    "GenerationSettings",
    "Profile",
    "SearchSettings",
    "default_config",
    "default_config_path",
    "dump_config",
    "format_number",
    "parse_config",
    "remove_profile",
    "set_active_profile",
]
