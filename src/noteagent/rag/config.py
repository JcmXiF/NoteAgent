"""Where the embedding endpoint and its settings come from.

The process environment is the input, as it is for the model endpoint: the
settings layer will apply the saved document to these names, and a value set
before the process started keeps winning. The API key is the one value that may
also live in the operating system's credential store, because a local service
sometimes asks for one and the document never holds a secret.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from noteagent.settings.credentials import (
    RAG_EMBEDDING_KEY,
    CredentialStore,
    CredentialStoreUnavailable,
    keyring_store,
)
from noteagent.util.refusal import CodedError

__all__ = [
    "DEFAULT_BATCH",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_DIM",
    "DEFAULT_MODEL",
    "DEFAULT_PAGE_TIMEOUT",
    "DEFAULT_QUERY_PREFIX",
    "DEFAULT_QUERY_TIMEOUT",
    "DEFAULT_RETRIES",
    "ENV_API_KEY",
    "ENV_BATCH",
    "ENV_CONCURRENCY",
    "ENV_ENDPOINT",
    "ENV_MODEL",
    "ENV_PAGE_TIMEOUT",
    "ENV_QUERY_PREFIX",
    "ENV_QUERY_TIMEOUT",
    "ENV_RETRIES",
    "EmbeddingConfig",
    "EmbeddingConfigError",
    "EmbeddingError",
    "load_embedding_config",
]

DEFAULT_MODEL = "Qwen3-Embedding-0.6B"
DEFAULT_DIM = 1024
DEFAULT_BATCH = 64
DEFAULT_CONCURRENCY = 1
DEFAULT_RETRIES = 3
DEFAULT_QUERY_TIMEOUT = 8.0
#: One ``embed`` call is one provider phase; the indexer makes one per page.
DEFAULT_PAGE_TIMEOUT = 300.0
#: Qwen3-Embedding is instruction-aware; the document side gets no prefix.
DEFAULT_QUERY_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages "
    "that answer the query\nQuery: {query}"
)

ENV_ENDPOINT = "NOTEAGENT_RAG_EMBEDDING_ENDPOINT"
ENV_MODEL = "NOTEAGENT_RAG_EMBEDDING_MODEL"
ENV_QUERY_PREFIX = "NOTEAGENT_RAG_EMBEDDING_QUERY_PREFIX"
ENV_BATCH = "NOTEAGENT_RAG_EMBEDDING_BATCH"
ENV_CONCURRENCY = "NOTEAGENT_RAG_EMBEDDING_CONCURRENCY"
ENV_RETRIES = "NOTEAGENT_RAG_EMBEDDING_RETRIES"
ENV_QUERY_TIMEOUT = "NOTEAGENT_RAG_EMBEDDING_QUERY_TIMEOUT"
ENV_PAGE_TIMEOUT = "NOTEAGENT_RAG_EMBEDDING_PAGE_TIMEOUT"
ENV_API_KEY = "NOTEAGENT_RAG_EMBEDDING_API_KEY"


class EmbeddingError(CodedError, RuntimeError):
    """The embedding endpoint or its configuration cannot produce vectors."""

    code = "rag.embeddingFailed"


class EmbeddingConfigError(EmbeddingError, ValueError):
    """The embedding settings are incomplete or contradictory."""

    code = "rag.embeddingNotConfigured"
    status = 503


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """One OpenAI-compatible embedding endpoint and the contract it must keep.

    ``retries`` counts attempts after the first request, so three means at most
    four requests. ``concurrency`` is a caller hint: this client sends its
    batches in order, and a page-level caller decides whether to overlap pages.
    """

    endpoint: str = ""
    model: str = DEFAULT_MODEL
    query_prefix: str = DEFAULT_QUERY_PREFIX
    dim: int = DEFAULT_DIM
    batch_size: int = DEFAULT_BATCH
    concurrency: int = DEFAULT_CONCURRENCY
    api_key: str = ""
    retries: int = DEFAULT_RETRIES
    query_timeout_s: float = DEFAULT_QUERY_TIMEOUT
    page_timeout_s: float = DEFAULT_PAGE_TIMEOUT

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise EmbeddingConfigError("the embedding model must not be empty")
        if self.dim <= 0:
            raise EmbeddingConfigError("the embedding dimension must be positive")
        if self.batch_size <= 0:
            raise EmbeddingConfigError("the embedding batch must be positive")
        if self.concurrency <= 0:
            raise EmbeddingConfigError("embedding concurrency must be positive")
        if self.retries < 0:
            raise EmbeddingConfigError("embedding retries cannot be negative")
        if self.query_timeout_s <= 0 or self.page_timeout_s <= 0:
            raise EmbeddingConfigError("embedding timeouts must be positive")
        if self.query_prefix and "{query}" not in self.query_prefix:
            raise EmbeddingConfigError(
                "the query prefix must contain the {query} placeholder"
            )


def load_embedding_config(
    *,
    environ: Mapping[str, str] | None = None,
    credential_store: CredentialStore | None = None,
) -> EmbeddingConfig:
    """Read the embedding settings, with the process environment winning.

    The key order is environment, then the credential store. An unavailable
    store is not an error: a local endpoint usually asks for no key at all.
    """
    source = os.environ if environ is None else environ
    prefix = (
        source[ENV_QUERY_PREFIX]
        if ENV_QUERY_PREFIX in source
        else DEFAULT_QUERY_PREFIX
    )
    # Presence in the environment wins even when the value is empty: an empty
    # key is how a person says "this endpoint needs none", and the store must
    # not quietly put the old one back.
    key = (
        source[ENV_API_KEY].strip()
        if ENV_API_KEY in source
        else _stored_key(credential_store)
    )
    return EmbeddingConfig(
        endpoint=source.get(ENV_ENDPOINT, "").strip(),
        model=(
            source.get(ENV_MODEL, "").strip() or DEFAULT_MODEL
        ),
        query_prefix=prefix,
        dim=DEFAULT_DIM,
        batch_size=_integer(
            source, ENV_BATCH, DEFAULT_BATCH, minimum=1
        ),
        concurrency=_integer(
            source, ENV_CONCURRENCY, DEFAULT_CONCURRENCY, minimum=1
        ),
        api_key=key,
        retries=_integer(
            source, ENV_RETRIES, DEFAULT_RETRIES, minimum=0
        ),
        query_timeout_s=_number(
            source, ENV_QUERY_TIMEOUT, DEFAULT_QUERY_TIMEOUT
        ),
        page_timeout_s=_number(
            source, ENV_PAGE_TIMEOUT, DEFAULT_PAGE_TIMEOUT
        ),
    )


def _stored_key(store: CredentialStore | None) -> str:
    try:
        chosen = store if store is not None else keyring_store()
        if not chosen.available():
            return ""
        return (chosen.get(RAG_EMBEDDING_KEY) or "").strip()
    except CredentialStoreUnavailable:
        return ""


def _integer(
    source: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as err:
        raise EmbeddingConfigError(
            f"{name} must be an integer, got {raw!r}"
        ) from err
    if value < minimum:
        raise EmbeddingConfigError(
            f"{name} must be at least {minimum}, got {value}"
        )
    return value


def _number(
    source: Mapping[str, str], name: str, default: float
) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as err:
        raise EmbeddingConfigError(
            f"{name} must be a number, got {raw!r}"
        ) from err
    if value <= 0:
        raise EmbeddingConfigError(f"{name} must be positive, got {value}")
    return value
