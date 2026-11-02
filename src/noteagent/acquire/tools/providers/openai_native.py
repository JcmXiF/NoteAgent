"""OpenAI's own web search, through the Responses API.

The Responses API runs the search inside the provider and returns citations;
there is no separate SERP document to parse. Two fields carry what a card
needs: the opt-in ``web_search_call.results`` list (title, URL, snippet) and
the ``url_citation`` annotations on the answer text, which name a source but
often carry no snippet. The adapter prefers the former and fills gaps from the
latter, so a result is never dropped because only one of the two was present.
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

#: The GA tool name. ``web_search_preview`` is the legacy spelling.
NATIVE_TOOL = {"type": "web_search"}
#: Ask for the raw hits the call found, not only the answer's citations.
INCLUDE_RESULTS = ["web_search_call.results"]


def _hits_from_payload(payload: Mapping[str, object], *, top_k: int) -> list[SerpHit]:
    """The sources in one Responses answer, deduplicated in the order found."""
    sources = SourceCollector()
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("type")
        if kind == "web_search_call":
            results = item.get("results")
            if isinstance(results, list):
                for raw in results:
                    if isinstance(raw, Mapping):
                        sources.add(
                            raw.get("title"), raw.get("url"), raw.get("snippet")
                        )
        elif kind == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                text = source_text(block.get("text"))
                annotations = block.get("annotations")
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if not isinstance(annotation, Mapping):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    start = annotation.get("start_index")
                    end = annotation.get("end_index")
                    snippet = ""
                    if (
                        isinstance(start, int)
                        and isinstance(end, int)
                        and 0 <= start < end <= len(text)
                    ):
                        snippet = text[start:end]
                    sources.add(annotation.get("title"), annotation.get("url"), snippet)

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
    url = f"{config.base_url.rstrip('/')}/responses"
    body = {
        "model": config.model,
        "tools": [NATIVE_TOOL],
        "input": cleaned,
        "include": INCLUDE_RESULTS,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    answer = post_search(
        url,
        headers=headers,
        body=body,
        timeout_seconds=config.timeout_seconds,
        client=client,
    )
    if answer.get("error"):
        raise NativeSearchError(f"native search error: {str(answer.get('error'))[:120]}")
    if answer.get("status") == "failed":
        raise NativeSearchError("native search finished with status failed")
    return _hits_from_payload(answer, top_k=count)
