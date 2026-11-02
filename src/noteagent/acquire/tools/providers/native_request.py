"""Shared request handling for the native-search adapters.

OpenAI and Anthropic differ only in the endpoint, headers and body they send.
The rest - cleaning the query, capping the result count, sending one JSON POST
and naming a rejected, unreachable or malformed answer - is the same decision
for both, so it lives here instead of being spelled twice.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from noteagent.acquire.search_config import (
    NativeSearchError,
    NativeSearchRejected,
    NativeSearchUnreachable,
)

#: The most sources one search may ask for.
MAX_RESULTS = 20


def clean_query(query: str) -> str:
    """The query with its whitespace collapsed, refusing an empty one."""
    cleaned = " ".join(query.split())
    if not cleaned:
        raise ValueError("search query must not be empty")
    return cleaned


def result_limit(top_k: int) -> int:
    """The number of results to keep, within what the adapters accept."""
    return max(1, min(int(top_k), MAX_RESULTS))


def post_search(
    url: str,
    *,
    headers: Mapping[str, str],
    body: Mapping[str, object],
    timeout_seconds: float,
    client: httpx.Client | None = None,
) -> Mapping[str, object]:
    """POST one search request and return its JSON object, or name the failure."""
    try:
        if client is not None:
            response = client.post(
                url, headers=headers, json=body, timeout=timeout_seconds
            )
        else:
            with httpx.Client(timeout=timeout_seconds) as own:
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
    return answer
