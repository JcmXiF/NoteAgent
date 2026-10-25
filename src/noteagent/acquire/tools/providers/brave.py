"""Brave Search API → SerpHit list."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from noteagent.acquire.search_config import SearchError
from noteagent.acquire.types import SerpHit

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# Fixed request shape (not env). Only ``web.results`` is parsed, and snippets
# stay plain text for humans. Other Brave knobs stay at API defaults:
# safesearch=moderate, spellcheck on, operators on, first page (offset=0), no
# freshness / goggles / summarizer.
BRAVE_RESULT_FILTER = "web"
BRAVE_TEXT_DECORATIONS = False

# Brave's search_lang enum is not plain ISO 639-1: ``zh`` and the usual
# regional tags are mapped onto ``zh-hans`` / ``zh-hant`` before the request.
_SEARCH_LANG_ALIASES = {
    "zh": "zh-hans",
    "zh-cn": "zh-hans",
    "zh-sg": "zh-hans",
    "zh-hans": "zh-hans",
    "zh-tw": "zh-hant",
    "zh-hk": "zh-hant",
    "zh-mo": "zh-hant",
    "zh-hant": "zh-hant",
    "ja": "jp",
    "jp": "jp",
}


def _site(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def normalize_brave_search_lang(search_lang: str) -> str:
    """Map install-level language tags onto Brave's search_lang enum."""
    cleaned = search_lang.strip()
    if not cleaned:
        return ""
    return _SEARCH_LANG_ALIASES.get(cleaned.lower(), cleaned)


def _nested_str(item: dict[str, Any], *keys: str) -> str:
    cur: Any = item
    for key in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return str(cur).strip() if cur else ""


def parse_brave_payload(payload: dict[str, Any]) -> list[SerpHit]:
    web = payload.get("web") if isinstance(payload.get("web"), dict) else {}
    raw_results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(raw_results, list):
        return []
    hits: list[SerpHit] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        snippet = str(item.get("description") or item.get("snippet") or "").strip()
        host = _nested_str(item, "meta_url", "hostname").removeprefix("www.") or _site(url)
        site_name = _nested_str(item, "profile", "name")
        favicon = _nested_str(item, "meta_url", "favicon") or _nested_str(
            item, "profile", "img"
        )
        hits.append(
            SerpHit(
                title=title,
                url=url,
                snippet=snippet,
                site=host,
                site_name=site_name,
                favicon=favicon,
            )
        )
    return hits


def brave_web_search(
    query: str,
    *,
    api_key: str,
    top_k: int,
    timeout_seconds: float = 30.0,
    country: str = "",
    search_lang: str = "",
    ui_lang: str = "",
    client: httpx.Client | None = None,
) -> list[SerpHit]:
    """
    Query the Brave Web Search API for webpage cards.

    Locale comes from SearchConfig. ``count``, ``result_filter=web`` and
    ``text_decorations=false`` are fixed here.
    """
    params: dict[str, Any] = {
        "q": query,
        "count": max(1, min(int(top_k), 20)),
        "result_filter": BRAVE_RESULT_FILTER,
        "text_decorations": BRAVE_TEXT_DECORATIONS,
    }
    if country:
        params["country"] = country
    brave_lang = normalize_brave_search_lang(search_lang)
    if brave_lang:
        params["search_lang"] = brave_lang
    if ui_lang:
        params["ui_lang"] = ui_lang
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Cache-Control": "no-cache",
        "X-Subscription-Token": api_key,
    }
    own_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http.get(BRAVE_WEB_SEARCH_URL, params=params, headers=headers)
    except httpx.HTTPError as err:
        raise SearchError(f"Brave search request failed: {err}") from err
    finally:
        if own_client:
            http.close()
    if response.status_code >= 400:
        raise SearchError(
            f"Brave search HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as err:
        raise SearchError("Brave search returned non-JSON") from err
    if not isinstance(payload, dict):
        raise SearchError("Brave search JSON was not an object")
    return parse_brave_payload(payload)
