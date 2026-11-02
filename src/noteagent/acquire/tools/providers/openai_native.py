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

from noteagent.acquire.cards import host_from_url
from noteagent.acquire.search_config import (
    NativeSearchConfig,
    NativeSearchError,
    NativeSearchRejected,
    NativeSearchUnreachable,
)
from noteagent.acquire.types import SerpHit

#: The GA tool name. ``web_search_preview`` is the legacy spelling.
NATIVE_TOOL = {"type": "web_search"}
#: Ask for the raw hits the call found, not only the answer's citations.
INCLUDE_RESULTS = ["web_search_call.results"]

_SNIPPET_LIMIT = 280


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _hits_from_payload(payload: Mapping[str, object], *, top_k: int) -> list[SerpHit]:
    """The sources in one Responses answer, deduplicated in the order found."""
    found: list[tuple[str, str, str]] = []

    def add(title: object, url: object, snippet: object = "") -> None:
        address = _text(url)
        if not address or any(existing[1] == address for existing in found):
            return
        found.append((_text(title), address, _text(snippet)[:_SNIPPET_LIMIT]))

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
                        add(raw.get("title"), raw.get("url"), raw.get("snippet"))
        elif kind == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                text = _text(block.get("text"))
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
                    add(annotation.get("title"), annotation.get("url"), snippet)

    hits: list[SerpHit] = []
    for title, url, snippet in found[:top_k]:
        site = host_from_url(url)
        hits.append(
            SerpHit(
                title=title or url,
                url=url,
                snippet=snippet,
                site=site,
                site_name=site,
            )
        )
    return hits


def native_search(
    query: str,
    *,
    top_k: int,
    config: NativeSearchConfig,
    client: httpx.Client | None = None,
) -> list[SerpHit]:
    """Run one search with the provider's own tool, and return its sources."""
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("search query must not be empty")
    count = max(1, min(int(top_k), 20))
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
    try:
        if client is not None:
            response = client.post(
                url, headers=headers, json=body, timeout=config.timeout_seconds
            )
        else:
            with httpx.Client(timeout=config.timeout_seconds) as own:
                response = own.post(url, headers=headers, json=body)
    except httpx.HTTPError as err:
        raise NativeSearchUnreachable(f"native search request failed: {err}") from err
    if response.status_code in (401, 403):
        raise NativeSearchRejected(
            f"native search HTTP {response.status_code}: {response.text[:120]}"
        )
    if response.status_code >= 400:
        raise NativeSearchError(
            f"native search HTTP {response.status_code}: {response.text[:120]}"
        )
    try:
        answer = response.json()
    except ValueError as err:
        raise NativeSearchError(
            f"native search was not JSON: {response.text[:120]}"
        ) from err
    if not isinstance(answer, Mapping):
        raise NativeSearchError("native search did not return an object")
    if answer.get("error"):
        raise NativeSearchError(f"native search error: {str(answer.get('error'))[:120]}")
    if answer.get("status") == "failed":
        raise NativeSearchError("native search finished with status failed")
    return _hits_from_payload(answer, top_k=count)
