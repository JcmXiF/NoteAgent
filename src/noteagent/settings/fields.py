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
    """One editable value and the environment name that can override it."""

    id: str
    env: str


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
    "F_SEARCH_MODE",
    "F_SEARCH_KEY",
    "F_SEARCH_TIMEOUT",
    "F_TEMPERATURE",
    "F_UI_LANG",
    "Field",
    "MANAGED_ENV_NAMES",
]
