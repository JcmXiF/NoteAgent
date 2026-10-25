"""Run web_search then turn hits into labeled cards for the client/CLI."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import httpx

from noteagent.acquire.budget import MAX_CANDIDATES
from noteagent.acquire.cards import cards_from_hits
from noteagent.acquire.search_config import SearchConfig, load_search_config
from noteagent.acquire.tools.web_search import web_search
from noteagent.acquire.types import CandidateCard, SerpHit
from noteagent.util.run_log import RunLogger

_ERROR_MESSAGE_LIMIT = 300


@dataclass(frozen=True, slots=True)
class WebSearchRun:
    query: str
    provider: str
    hits: list[SerpHit]
    cards: list[CandidateCard]


def run_web_search(
    query: str,
    *,
    top_k: int | None = None,
    config: SearchConfig | None = None,
    client: httpx.Client | None = None,
    run_logger: RunLogger | None = None,
    caller: str | None = None,
) -> WebSearchRun:
    logger = run_logger or RunLogger.disabled()
    cleaned = " ".join(query.split())
    limit = MAX_CANDIDATES if top_k is None else max(1, min(int(top_k), 20))
    started = perf_counter()
    provider: str | None = None
    try:
        cfg = config if config is not None else load_search_config()
        provider = cfg.provider
        logger.append(
            "search.start",
            component="acquire",
            query=cleaned,
            provider=provider,
            top_k=limit,
            caller=caller,
        )
        hits = web_search(query, top_k=limit, config=cfg, client=client)
        cards = cards_from_hits(hits, query=cleaned, limit=limit)
    except Exception as err:
        logger.append(
            "search.error",
            component="acquire",
            query=cleaned,
            provider=provider,
            caller=caller,
            error=type(err).__name__,
            message=str(err)[:_ERROR_MESSAGE_LIMIT],
            duration_ms=int((perf_counter() - started) * 1000),
        )
        raise
    logger.append(
        "search.end",
        component="acquire",
        query=cleaned,
        provider=provider,
        caller=caller,
        hits=len(hits),
        cards=len(cards),
        urls=[card.url for card in cards],
        duration_ms=int((perf_counter() - started) * 1000),
    )
    return WebSearchRun(
        query=cleaned,
        provider=cfg.provider,
        hits=hits,
        cards=cards,
    )
