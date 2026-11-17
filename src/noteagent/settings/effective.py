"""What the running core will actually use, and where each value came from.

Precedence is the whole point of this module: a name already in the process
environment wins over the configuration document, and the defaults are last.
The screen needs both halves of that sentence — the value and its source — so a
field the screen cannot change is shown as such instead of silently ignored.

Secrets follow the same order, with one difference: the credential store sits
where the document sits, because a secret never goes into the document and is
never read from a plaintext file.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from noteagent.llm.config import process_env_keys
from noteagent.rag.config import (
    DEFAULT_BATCH,
    DEFAULT_CONCURRENCY,
    DEFAULT_MODEL as DEFAULT_EMBEDDING_MODEL,
    DEFAULT_QUERY_PREFIX,
)
from noteagent.rag.rerank_config import DEFAULT_TOP_N
from noteagent.rag.types import DEFAULT_CANDIDATE_LIMIT
from noteagent.settings.credentials import CredentialStoreUnavailable
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
    F_RAG_BATCH,
    F_RAG_CONCURRENCY,
    F_RAG_EMBEDDING_ENDPOINT,
    F_RAG_EMBEDDING_KEY,
    F_RAG_EMBEDDING_MODEL,
    F_RAG_ENABLED,
    F_RAG_QUERY_PREFIX,
    F_RAG_RERANK_CANDIDATES,
    F_RAG_RERANK_ENABLED,
    F_RAG_RERANK_ENDPOINT,
    F_RAG_RERANK_KEY,
    F_RAG_RERANK_MODEL,
    F_RAG_RERANK_TOP_N,
    F_SEARCH_MODE,
    F_SEARCH_KEY,
    F_SEARCH_TIMEOUT,
    F_TEMPERATURE,
    F_UI_LANG,
    Field,
)
from noteagent.settings.store import (
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_MAX_TOKENS,
    DEFAULT_SEARCH_TIMEOUT,
    DEFAULT_TEMPERATURE,
    Config,
    ConfigStore,
    format_number,
)

#: Where a value came from, in the order it is allowed to win.
SOURCE_ENV = "env"
SOURCE_CONFIG = "config"
SOURCE_KEYCHAIN = "keychain"
SOURCE_DEFAULT = "default"

#: The field id the screen uses, and the attribute holding its resolved value.
_VALUE_FIELDS: tuple[tuple[Field, str], ...] = (
    (F_BASE, "base_url"),
    (F_LLM_KEY, "llm_key"),
    (F_MODEL, "model"),
    (F_TEMPERATURE, "temperature"),
    (F_MAX_TOKENS, "max_tokens"),
    (F_LLM_TIMEOUT, "llm_timeout"),
    (F_PROVIDER, "search_provider"),
    (F_SEARCH_MODE, "search_mode"),
    (F_SEARCH_KEY, "search_key"),
    (F_SEARCH_TIMEOUT, "search_timeout"),
    (F_COUNTRY, "country"),
    (F_LANG, "search_lang"),
    (F_UI_LANG, "ui_lang"),
    (F_MEDIA, "extract_media"),
    (F_RAG_ENABLED, "rag_enabled"),
    (F_RAG_EMBEDDING_ENDPOINT, "rag_embedding_endpoint"),
    (F_RAG_EMBEDDING_MODEL, "rag_embedding_model"),
    (F_RAG_QUERY_PREFIX, "rag_query_prefix"),
    (F_RAG_BATCH, "rag_batch"),
    (F_RAG_CONCURRENCY, "rag_concurrency"),
    (F_RAG_EMBEDDING_KEY, "rag_embedding_key"),
    (F_RAG_RERANK_ENABLED, "rag_rerank_enabled"),
    (F_RAG_RERANK_ENDPOINT, "rag_rerank_endpoint"),
    (F_RAG_RERANK_MODEL, "rag_rerank_model"),
    (F_RAG_RERANK_CANDIDATES, "rag_rerank_candidates"),
    (F_RAG_RERANK_TOP_N, "rag_rerank_top_n"),
    (F_RAG_RERANK_KEY, "rag_rerank_key"),
)

#: Names this module wrote into the process environment. A value it wrote is
#: not an outside override, so the next resolution must be able to remove it
#: when the document no longer owns it.
_applied: set[str] = set()


def forget_applied() -> None:
    """Remove the values this module applied last time.

    Called before a fresh read, so a value the document has dropped does not
    linger in the process the runtime reads from.
    """
    for name in _applied:
        os.environ.pop(name, None)
    _applied.clear()


@dataclass(frozen=True, slots=True)
class Value:
    """One resolved value. ``locked`` means the environment owns the name."""

    text: str
    source: str
    locked: bool = False


@dataclass(frozen=True, slots=True)
class Effective:
    """Every value the settings screen and the runtime read."""

    base_url: Value
    llm_key: Value
    model: Value
    temperature: Value
    max_tokens: Value
    llm_timeout: Value
    search_provider: Value
    search_mode: Value
    search_key: Value
    search_timeout: Value
    country: Value
    search_lang: Value
    ui_lang: Value
    extract_media: Value
    rag_enabled: Value
    rag_embedding_endpoint: Value
    rag_embedding_model: Value
    rag_query_prefix: Value
    rag_batch: Value
    rag_concurrency: Value
    rag_embedding_key: Value
    rag_rerank_enabled: Value
    rag_rerank_endpoint: Value
    rag_rerank_model: Value
    rag_rerank_candidates: Value
    rag_rerank_top_n: Value
    rag_rerank_key: Value
    keychain_available: bool

    def sources(self) -> dict[str, str]:
        """Value sources by the field id the screen already uses."""
        return {field.id: getattr(self, name).source for field, name in _VALUE_FIELDS}

    def locked(self) -> list[str]:
        return [field.id for field, name in _VALUE_FIELDS if getattr(self, name).locked]


def _plain(
    name: str,
    config_value: str,
    default: str,
    *,
    file_exists: bool,
    process_keys: frozenset[str],
    environ: Mapping[str, str],
) -> Value:
    if name in process_keys:
        return Value(environ.get(name, ""), SOURCE_ENV, locked=True)
    if file_exists:
        return Value(config_value, SOURCE_CONFIG)
    return Value(default, SOURCE_DEFAULT)


def _secret(
    name: str,
    stored: str,
    *,
    process_keys: frozenset[str],
    environ: Mapping[str, str],
) -> Value:
    if name in process_keys:
        return Value(environ.get(name, ""), SOURCE_ENV, locked=True)
    if stored:
        return Value(stored, SOURCE_KEYCHAIN)
    return Value("", SOURCE_DEFAULT)


def _runtime_default(value: Value, default: str) -> Value:
    """What the runtime uses when an environment value is present but empty.

    The embedding loader substitutes its default for an empty model, so the
    screen must show that model rather than the empty text it was given.
    """
    if value.source == SOURCE_ENV and not value.text:
        return Value(default, value.source, locked=value.locked)
    return value


def _read_secret(reader: Callable[[], str], store: ConfigStore) -> tuple[str, bool]:
    """A secret and whether the credential store could be used at all."""
    if not store.keychain_available():
        return "", False
    try:
        return reader(), True
    except CredentialStoreUnavailable:
        return "", False


def resolve(
    config: Config,
    store: ConfigStore,
    *,
    file_exists: bool | None = None,
    process_keys: frozenset[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Effective:
    """Resolve every value once, so the screen and a save read the same state."""
    exists = store.exists() if file_exists is None else file_exists
    keys = process_env_keys() if process_keys is None else process_keys
    source = os.environ if environ is None else environ

    profile = config.active_profile
    if profile is None:
        stored_llm_key, keychain_available = "", store.keychain_available()
    else:
        stored_llm_key, keychain_available = _read_secret(
            lambda: store.get_llm_key(profile.id), store
        )
    stored_search_key, search_available = _read_secret(store.get_search_key, store)
    stored_rag_key, rag_available = _read_secret(
        store.get_rag_embedding_key, store
    )
    stored_rerank_key, rerank_available = _read_secret(
        store.get_rag_rerank_key, store
    )
    keychain_available = (
        keychain_available
        and search_available
        and rag_available
        and rerank_available
    )

    generation = config.generation
    search = config.search
    values = {
        "base_url": _plain(
            F_BASE.env,
            profile.base_url if profile else "",
            "",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "llm_key": _secret(
            F_LLM_KEY.env, stored_llm_key, process_keys=keys, environ=source
        ),
        "model": _plain(
            F_MODEL.env,
            profile.model if profile else "",
            "",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "temperature": _plain(
            F_TEMPERATURE.env,
            format_number(generation.temperature),
            format_number(DEFAULT_TEMPERATURE),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "max_tokens": _plain(
            F_MAX_TOKENS.env,
            str(generation.max_tokens),
            str(DEFAULT_MAX_TOKENS),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "llm_timeout": _plain(
            F_LLM_TIMEOUT.env,
            format_number(generation.timeout_seconds),
            format_number(DEFAULT_LLM_TIMEOUT),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "search_provider": _plain(
            F_PROVIDER.env,
            search.external_provider,
            "none",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "search_mode": _plain(
            F_SEARCH_MODE.env,
            search.mode,
            "auto",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "search_key": _secret(
            F_SEARCH_KEY.env, stored_search_key, process_keys=keys, environ=source
        ),
        "search_timeout": _plain(
            F_SEARCH_TIMEOUT.env,
            format_number(search.timeout_seconds),
            format_number(DEFAULT_SEARCH_TIMEOUT),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "country": _plain(
            F_COUNTRY.env, search.country, "", file_exists=exists, process_keys=keys, environ=source
        ),
        "search_lang": _plain(
            F_LANG.env, search.search_lang, "", file_exists=exists, process_keys=keys, environ=source
        ),
        "ui_lang": _plain(
            F_UI_LANG.env, search.ui_lang, "", file_exists=exists, process_keys=keys, environ=source
        ),
        "extract_media": _plain(
            F_MEDIA.env,
            "1" if config.extract_media else "0",
            "1",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_enabled": _plain(
            F_RAG_ENABLED.env,
            "1" if config.rag.enabled else "0",
            "0",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_embedding_endpoint": _plain(
            F_RAG_EMBEDDING_ENDPOINT.env,
            config.rag.embedding.endpoint,
            "",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_embedding_model": _runtime_default(
            _plain(
                F_RAG_EMBEDDING_MODEL.env,
                config.rag.embedding.model,
                DEFAULT_EMBEDDING_MODEL,
                file_exists=exists,
                process_keys=keys,
                environ=source,
            ),
            DEFAULT_EMBEDDING_MODEL,
        ),
        "rag_query_prefix": _plain(
            F_RAG_QUERY_PREFIX.env,
            config.rag.embedding.query_prefix,
            DEFAULT_QUERY_PREFIX,
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_batch": _plain(
            F_RAG_BATCH.env,
            str(config.rag.embedding.batch),
            str(DEFAULT_BATCH),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_concurrency": _plain(
            F_RAG_CONCURRENCY.env,
            str(config.rag.embedding.concurrency),
            str(DEFAULT_CONCURRENCY),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_embedding_key": _secret(
            F_RAG_EMBEDDING_KEY.env,
            stored_rag_key,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_enabled": _plain(
            F_RAG_RERANK_ENABLED.env,
            "1" if config.rag.rerank.enabled else "0",
            "0",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_endpoint": _plain(
            F_RAG_RERANK_ENDPOINT.env,
            config.rag.rerank.endpoint,
            "",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_model": _plain(
            F_RAG_RERANK_MODEL.env,
            config.rag.rerank.model,
            "",
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_candidates": _plain(
            F_RAG_RERANK_CANDIDATES.env,
            str(config.rag.rerank.candidates),
            str(DEFAULT_CANDIDATE_LIMIT),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_top_n": _plain(
            F_RAG_RERANK_TOP_N.env,
            str(config.rag.rerank.top_n),
            str(DEFAULT_TOP_N),
            file_exists=exists,
            process_keys=keys,
            environ=source,
        ),
        "rag_rerank_key": _secret(
            F_RAG_RERANK_KEY.env,
            stored_rerank_key,
            process_keys=keys,
            environ=source,
        ),
    }
    return Effective(keychain_available=keychain_available, **values)


def apply(
    effective: Effective,
    *,
    process_keys: frozenset[str] | None = None,
    environ: dict[str, str] | None = None,
) -> None:
    """Make this process read the resolved values.

    Only values the document (or its credential store) owns are written: a name
    the process was started with must not be touched at all. Writing an empty
    value removes the name, so a removed setting does not survive in the
    runtime.
    """
    keys = process_env_keys() if process_keys is None else process_keys
    target = os.environ if environ is None else environ
    track = environ is None
    for field, name in _VALUE_FIELDS:
        if field.env in keys:
            continue
        value = getattr(effective, name)
        if value.source not in (SOURCE_CONFIG, SOURCE_KEYCHAIN):
            continue
        if value.text:
            target[field.env] = value.text
        elif field.empty_is_value:
            # The loader distinguishes an empty value from a missing name.
            target[field.env] = ""
        else:
            target.pop(field.env, None)
        if track:
            _applied.add(field.env)


__all__ = [
    "SOURCE_CONFIG",
    "SOURCE_DEFAULT",
    "SOURCE_ENV",
    "SOURCE_KEYCHAIN",
    "Effective",
    "Value",
    "apply",
    "forget_applied",
    "resolve",
]
