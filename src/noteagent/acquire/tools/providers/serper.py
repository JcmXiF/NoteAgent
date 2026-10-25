"""Serper (Google-like SERP JSON) → SerpHit list."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from noteagent.acquire.search_config import SearchError
from noteagent.acquire.types import SerpHit

SERPER_SEARCH_URL = "https://google.serper.dev/search"


def _site(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def parse_serper_payload(payload: dict[str, Any]) -> list[SerpHit]:
    raw_results = payload.get("organic")
    if not isinstance(raw_results, list):
        return []
    hits: list[SerpHit] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not url or not title:
            continue
        snippet = str(item.get("snippet") or "").strip()
        site_name = str(item.get("sitename") or item.get("source") or "").strip()
        favicon = str(item.get("favicon") or "").strip()
        hits.append(
            SerpHit(
                title=title,
                url=url,
                snippet=snippet,
                site=_site(url),
                site_name=site_name,
                favicon=favicon,
            )
        )
    return hits


def serper_web_search(
    query: str,
    *,
    api_key: str,
    top_k: int,
    timeout_seconds: float = 30.0,
    country: str = "",
    search_lang: str = "",
    client: httpx.Client | None = None,
) -> list[SerpHit]:
    body: dict[str, Any] = {
        "q": query,
        "num": max(1, min(top_k, 20)),
    }
    if country:
        body["gl"] = country.lower()
    if search_lang:
        body["hl"] = search_lang
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    own_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    try:
        response = http.post(SERPER_SEARCH_URL, json=body, headers=headers)
    except httpx.HTTPError as err:
        raise SearchError(f"Serper search request failed: {err}") from err
    finally:
        if own_client:
            http.close()
    if response.status_code >= 400:
        raise SearchError(
            f"Serper search HTTP {response.status_code}: {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError as err:
        raise SearchError("Serper search returned non-JSON") from err
    if not isinstance(payload, dict):
        raise SearchError("Serper search JSON was not an object")
    return parse_serper_payload(payload)
