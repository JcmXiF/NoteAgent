"""Shape what a native-search adapter found into SERP hits.

Both adapters answer with sources rather than a search-engine document: a
title, a URL and, when the provider has one, a passage. This module owns the
shared decisions - the first URL wins, a later passage fills an empty snippet,
the result is capped, and the site metadata comes from the URL - so an adapter
only has to say where those three fields live in its own payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.acquire.cards import host_from_url
from noteagent.acquire.types import SerpHit

SNIPPET_LIMIT = 280


def source_text(value: object) -> str:
    """A payload field as trimmed text, or '' when it is not text."""
    return value.strip() if isinstance(value, str) else ""


@dataclass(slots=True)
class _FoundSource:
    title: str
    url: str
    snippet: str


class SourceCollector:
    """Sources found in one provider answer, deduplicated in the order found."""

    def __init__(self) -> None:
        self._found: list[_FoundSource] = []

    def add(self, title: object, url: object, snippet: object = "") -> None:
        address = source_text(url)
        if not address:
            return
        title_text = source_text(title)
        snippet_text = source_text(snippet)[:SNIPPET_LIMIT]
        for known in self._found:
            if known.url != address:
                continue
            # A citation often arrives after the result list and carries the
            # passage the result block lacks; keep the first title, fill the gap.
            if not known.snippet and snippet_text:
                known.title = known.title or title_text
                known.snippet = snippet_text
            return
        self._found.append(_FoundSource(title_text, address, snippet_text))

    def hits(self, top_k: int) -> list[SerpHit]:
        hits: list[SerpHit] = []
        for found in self._found[: max(0, top_k)]:
            site = host_from_url(found.url)
            hits.append(
                SerpHit(
                    title=found.title or found.url,
                    url=found.url,
                    snippet=found.snippet,
                    site=site,
                    site_name=site,
                )
            )
        return hits
