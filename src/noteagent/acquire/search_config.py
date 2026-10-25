from __future__ import annotations

import os
from dataclasses import dataclass

from noteagent.llm.config import load_dotenv
from noteagent.util.refusal import CodedError

KNOWN_PROVIDERS = frozenset({"none", "brave", "serper"})


class SearchConfigError(CodedError, ValueError):
    """Raised when search provider settings are invalid."""

    code = "provider.misconfigured"


class SearchError(CodedError, RuntimeError):
    """Raised when a search HTTP call fails."""

    code = "provider.failed"
    status = 502


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Install-level settings for the generic web_search tool.

    Environment only carries *who searches* and *for which locale*.
    Ranking, pagination, snippet decoration, and extra result types are fixed
    in the provider adapter (see ``providers/brave.py``).
    """

    provider: str
    api_key: str
    timeout_seconds: float = 30.0
    country: str = ""
    search_lang: str = ""
    ui_lang: str = ""

    @property
    def configured(self) -> bool:
        return self.provider not in ("", "none") and bool(self.api_key)


def load_search_config() -> SearchConfig:
    """
    Load search settings from the environment (after optional ``.env``).

        NOTEAGENT_SEARCH_PROVIDER   none | brave | serper
        NOTEAGENT_SEARCH_API_KEY
        NOTEAGENT_SEARCH_TIMEOUT    optional, default 30
        NOTEAGENT_SEARCH_COUNTRY    optional, 2-letter (Brave ``country``, Serper ``gl``)
        NOTEAGENT_SEARCH_LANG       optional (Brave ``search_lang`` / Serper ``hl``).
                                    Brave Chinese: ``zh-hans`` or ``zh-hant``.
                                    ``zh`` is accepted and sent as ``zh-hans``.
        NOTEAGENT_SEARCH_UI_LANG    optional, Brave ``ui_lang`` (e.g. ``zh-CN``)
    """
    load_dotenv()
    provider = os.environ.get("NOTEAGENT_SEARCH_PROVIDER", "none").strip().lower() or "none"
    if provider not in KNOWN_PROVIDERS:
        raise SearchConfigError(
            f"Unknown NOTEAGENT_SEARCH_PROVIDER={provider!r}. "
            f"Use one of: {', '.join(sorted(KNOWN_PROVIDERS))}."
        )
    api_key = os.environ.get("NOTEAGENT_SEARCH_API_KEY", "").strip()
    timeout_seconds = float(os.environ.get("NOTEAGENT_SEARCH_TIMEOUT", "30"))
    country = os.environ.get("NOTEAGENT_SEARCH_COUNTRY", "").strip()
    search_lang = os.environ.get("NOTEAGENT_SEARCH_LANG", "").strip()
    ui_lang = os.environ.get("NOTEAGENT_SEARCH_UI_LANG", "").strip()
    return SearchConfig(
        provider=provider,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        country=country,
        search_lang=search_lang,
        ui_lang=ui_lang,
    )
