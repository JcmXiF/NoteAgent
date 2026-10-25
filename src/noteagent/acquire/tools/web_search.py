from __future__ import annotations

import httpx

from noteagent.acquire.search_config import SearchConfig, SearchError, load_search_config
from noteagent.acquire.tools.providers.brave import brave_web_search
from noteagent.acquire.tools.providers.serper import serper_web_search
from noteagent.acquire.types import ProviderNotConfiguredError, SerpHit

TOOL_NAME = "web_search"
TOOL_DESCRIPTION = (
    "Search the open web by keyword and return SERP metadata "
    "(title, URL, snippet, site). Does not open pages or fetch bodies."
)


def web_search(
    query: str,
    *,
    top_k: int = 10,
    config: SearchConfig | None = None,
    client: httpx.Client | None = None,
) -> list[SerpHit]:
    """
    Generic webpage search. Provider is selected by SearchConfig (brave / serper).
    """
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("search query must not be empty")
    cfg = config if config is not None else load_search_config()
    if not cfg.configured:
        raise ProviderNotConfiguredError(
            "web_search is not configured. Set NOTEAGENT_SEARCH_PROVIDER "
            "to brave or serper, and NOTEAGENT_SEARCH_API_KEY "
            "(the app's settings, or the .env it writes)."
        )
    count = max(1, min(int(top_k), 20))
    if cfg.provider == "brave":
        return brave_web_search(
            cleaned,
            api_key=cfg.api_key,
            top_k=count,
            timeout_seconds=cfg.timeout_seconds,
            country=cfg.country,
            search_lang=cfg.search_lang,
            ui_lang=cfg.ui_lang,
            client=client,
        )
    if cfg.provider == "serper":
        return serper_web_search(
            cleaned,
            api_key=cfg.api_key,
            top_k=count,
            timeout_seconds=cfg.timeout_seconds,
            country=cfg.country,
            search_lang=cfg.search_lang,
            client=client,
        )
    raise SearchError(f"unsupported search provider: {cfg.provider}")
