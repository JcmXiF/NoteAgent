"""HTTP helpers for acquire search.

A search belongs to the project it was asked from: its run-log lines go to that
project's ``.noteagent/run-log.jsonl``, which is what makes a search findable
again later.
"""

from __future__ import annotations

from noteagent.acquire.run_search import run_web_search
from noteagent.acquire.types import CandidateCard
from noteagent.api.projects import logger_for_project


def card_to_dict(card: CandidateCard) -> dict[str, str]:
    return {
        "title": card.title,
        "url": card.url,
        "site": card.site,
        "siteName": card.site_name or card.site,
        "favicon": card.favicon,
        "snippet": card.snippet,
        "via": card.via,
        "why": card.why,
    }


def run_search_payload(
    query: str,
    *,
    top_k: int | None = None,
    project: str,
) -> dict[str, object]:
    run = run_web_search(
        query,
        top_k=top_k,
        run_logger=logger_for_project(project),
        caller="api",
    )
    return {
        "query": run.query,
        "provider": run.provider,
        "cards": [card_to_dict(card) for card in run.cards],
    }


__all__ = [
    "card_to_dict",
    "run_search_payload",
]
