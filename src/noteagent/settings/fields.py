"""The values the settings screen edits, and the environment name for each.

One definition, used by the store, the precedence resolver and the screen's
answer. The environment names are also the names the command line and the
runtime already read, so a value set there keeps working while the
configuration document takes over.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Field:
    """One editable value and the environment name that can override it.

    ``empty_is_value`` marks a field whose loader tells "set to empty" apart
    from "not set"; an empty document value then stays in the environment
    instead of being removed.
    """

    id: str
    env: str
    empty_is_value: bool = False


F_BASE = Field("llm.baseUrl", "NOTEAGENT_LLM_BASE_URL")
F_LLM_KEY = Field("llm.apiKey", "NOTEAGENT_LLM_API_KEY")
F_MODEL = Field("llm.model", "NOTEAGENT_LLM_MODEL")
F_TEMPERATURE = Field("llm.temperature", "NOTEAGENT_LLM_TEMPERATURE")
F_MAX_TOKENS = Field("llm.maxTokens", "NOTEAGENT_LLM_MAX_TOKENS")
F_LLM_TIMEOUT = Field("llm.timeoutSeconds", "NOTEAGENT_LLM_TIMEOUT")
F_PROVIDER = Field("search.provider", "NOTEAGENT_SEARCH_PROVIDER")
F_SEARCH_MODE = Field("search.mode", "NOTEAGENT_SEARCH_MODE")
F_SEARCH_KEY = Field("search.apiKey", "NOTEAGENT_SEARCH_API_KEY")
F_SEARCH_TIMEOUT = Field("search.timeoutSeconds", "NOTEAGENT_SEARCH_TIMEOUT")
F_COUNTRY = Field("search.country", "NOTEAGENT_SEARCH_COUNTRY")
F_LANG = Field("search.searchLang", "NOTEAGENT_SEARCH_LANG")
F_UI_LANG = Field("search.uiLang", "NOTEAGENT_SEARCH_UI_LANG")
F_MEDIA = Field("extractMedia", "NOTEAGENT_EXTRACT_MEDIA")
F_RAG_ENABLED = Field("rag.enabled", "NOTEAGENT_RAG_ENABLED")
F_RAG_EMBEDDING_ENDPOINT = Field(
    "rag.embedding.endpoint", "NOTEAGENT_RAG_EMBEDDING_ENDPOINT"
)
F_RAG_EMBEDDING_MODEL = Field(
    "rag.embedding.model", "NOTEAGENT_RAG_EMBEDDING_MODEL"
)
F_RAG_QUERY_PREFIX = Field(
    "rag.embedding.queryPrefix",
    "NOTEAGENT_RAG_EMBEDDING_QUERY_PREFIX",
    empty_is_value=True,
)
F_RAG_BATCH = Field("rag.embedding.batch", "NOTEAGENT_RAG_EMBEDDING_BATCH")
F_RAG_CONCURRENCY = Field(
    "rag.embedding.concurrency", "NOTEAGENT_RAG_EMBEDDING_CONCURRENCY"
)
F_RAG_EMBEDDING_KEY = Field(
    "rag.embedding.apiKey", "NOTEAGENT_RAG_EMBEDDING_API_KEY"
)

FIELDS = (
    F_BASE,
    F_LLM_KEY,
    F_MODEL,
    F_TEMPERATURE,
    F_MAX_TOKENS,
    F_LLM_TIMEOUT,
    F_PROVIDER,
    F_SEARCH_MODE,
    F_SEARCH_KEY,
    F_SEARCH_TIMEOUT,
    F_COUNTRY,
    F_LANG,
    F_UI_LANG,
    F_MEDIA,
    F_RAG_ENABLED,
    F_RAG_EMBEDDING_ENDPOINT,
    F_RAG_EMBEDDING_MODEL,
    F_RAG_QUERY_PREFIX,
    F_RAG_BATCH,
    F_RAG_CONCURRENCY,
    F_RAG_EMBEDDING_KEY,
)

MANAGED_ENV_NAMES = frozenset(field.env for field in FIELDS)


__all__ = [
    "FIELDS",
    "F_BASE",
    "F_COUNTRY",
    "F_LANG",
    "F_LLM_KEY",
    "F_LLM_TIMEOUT",
    "F_MAX_TOKENS",
    "F_MEDIA",
    "F_MODEL",
    "F_PROVIDER",
    "F_RAG_BATCH",
    "F_RAG_CONCURRENCY",
    "F_RAG_EMBEDDING_ENDPOINT",
    "F_RAG_EMBEDDING_KEY",
    "F_RAG_EMBEDDING_MODEL",
    "F_RAG_ENABLED",
    "F_RAG_QUERY_PREFIX",
    "F_SEARCH_MODE",
    "F_SEARCH_KEY",
    "F_SEARCH_TIMEOUT",
    "F_TEMPERATURE",
    "F_UI_LANG",
    "Field",
    "MANAGED_ENV_NAMES",
]
