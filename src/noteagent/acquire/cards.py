"""Turn SERP hits into resource cards. Site name / icon come from the engine."""

from __future__ import annotations

from urllib.parse import urlparse

from noteagent.acquire.budget import MAX_CANDIDATES
from noteagent.acquire.types import CandidateCard, SerpHit


def host_from_url(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def favicon_for_host(host: str, provided: str = "") -> str:
    """Prefer the search API's favicon; otherwise a well-known icon resolver."""
    cleaned = provided.strip()
    if cleaned:
        return cleaned
    if host:
        return f"https://icons.duckduckgo.com/ip3/{host}.ico"
    return ""


def cards_from_hits(
    hits: list[SerpHit],
    *,
    query: str,
    limit: int | None = None,
) -> list[CandidateCard]:
    """
    Map engine hits to cards. Does not LLM-rank; the human picks raw sources.
    """
    cap = MAX_CANDIDATES if limit is None else max(0, limit)
    cards: list[CandidateCard] = []
    for hit in hits[:cap]:
        site = hit.site or host_from_url(hit.url)
        site_name = hit.site_name.strip() or site
        cards.append(
            CandidateCard(
                title=hit.title,
                url=hit.url,
                site=site,
                snippet=hit.snippet,
                why=f"web_search:{query}",
                via="web_search",
                site_name=site_name,
                favicon=favicon_for_host(site, hit.favicon),
            )
        )
    return cards


def format_cards(cards: list[CandidateCard]) -> str:
    if not cards:
        return "(no cards)\n"
    lines: list[str] = []
    for index, card in enumerate(cards, start=1):
        name = card.site_name or card.site
        lines.append(f"{index}. [{name}] {card.title}")
        lines.append(f"   {card.url}")
        if card.snippet:
            lines.append(f"   {card.snippet}")
        lines.append("")
    return "\n".join(lines)
