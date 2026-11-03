"""Anthropic's own web search, through the Messages API.

The ``web_search`` server tool runs inside the model service. Its answer carries
two kinds of source: ``web_search_tool_result`` blocks list every page the search
read, and the answer text's ``web_search_result_location`` citations name the
pages the model used along with the quoted passage. The adapter prefers the
result list and fills a card's snippet from the citation for the same URL.

Anthropic authenticates with ``x-api-key`` and a fixed API version header, not
the bearer token the OpenAI-compatible client sends.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from noteagent.acquire.search_config import NativeSearchConfig, NativeSearchError
from noteagent.acquire.tools.providers.native_request import (
    clean_query,
    post_search,
    result_limit,
)
from noteagent.acquire.tools.providers.sources import SourceCollector, source_text
from noteagent.acquire.types import SerpHit

#: The basic spelling of the server-side tool. The newer dated variants are
#: tied to recent model generations; this one is accepted by every endpoint
#: the app can be pointed at.
NATIVE_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
#: Required by the Messages API regardless of the key.
API_VERSION = "2023-06-01"

#: Room for the answer that carries the citations.
_ANSWER_TOKENS = 1024


def _tool_error_text(result: Mapping[str, object]) -> str:
    return (
        source_text(result.get("error_code"))
        or source_text(result.get("type"))
        or "unknown error"
    )


def _hits_from_payload(payload: Mapping[str, object], *, top_k: int) -> list[SerpHit]:
    """The sources in one Messages answer, deduplicated in the order found."""
    sources = SourceCollector()
    content = payload.get("content")
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind == "web_search_tool_result":
            result = block.get("content")
            # A successful search is a list of results; a failed one is a
            # single error object, and still comes back as HTTP 200.
            if isinstance(result, Mapping):
                raise NativeSearchError(
                    f"native search failed: {_tool_error_text(result)}"
                )
            if isinstance(result, list):
                for raw in result:
                    if isinstance(raw, Mapping):
                        sources.add(raw.get("title"), raw.get("url"))
        elif kind == "text":
            citations = block.get("citations")
            if not isinstance(citations, list):
                continue
            for citation in citations:
                if not isinstance(citation, Mapping):
                    continue
                if citation.get("type") != "web_search_result_location":
                    continue
                sources.add(
                    citation.get("title"),
                    citation.get("url"),
                    citation.get("cited_text"),
                )
    return sources.hits(top_k)


def native_search(
    query: str,
    *,
    top_k: int,
    config: NativeSearchConfig,
    client: httpx.Client | None = None,
) -> list[SerpHit]:
    """Run one search with the provider's own tool, and return its sources."""
    cleaned = clean_query(query)
    count = result_limit(top_k)
    url = f"{config.base_url.rstrip('/')}/messages"
    body = {
        "model": config.model,
        "max_tokens": _ANSWER_TOKENS,
        "messages": [{"role": "user", "content": cleaned}],
        "tools": [NATIVE_TOOL],
    }
    headers = {
        "x-api-key": config.api_key,
        "anthropic-version": API_VERSION,
        "Content-Type": "application/json",
    }
    answer = post_search(
        url,
        headers=headers,
        body=body,
        timeout_seconds=config.timeout_seconds,
        client=client,
    )
    if answer.get("type") == "error" or answer.get("error"):
        raise NativeSearchError(f"native search error: {str(answer.get('error'))[:120]}")
    return _hits_from_payload(answer, top_k=count)
