"""Run web_search then turn hits into labeled cards for the client/CLI."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import httpx

from noteagent.acquire.budget import MAX_CANDIDATES
from noteagent.acquire.cards import cards_from_hits
from noteagent.acquire.search_config import (
    NativeSearchConfigMissing,
    NativeSearchError,
    SearchConfig,
    SearchConfigError,
    SearchModeUnavailable,
    load_search_config,
    native_support,
)
from noteagent.acquire.tools.providers.openai_native import native_search
from noteagent.acquire.tools.web_search import web_search
from noteagent.acquire.types import CandidateCard, ProviderNotConfiguredError, SerpHit
from noteagent.settings.store import SEARCH_MODES
from noteagent.util.run_log import RunLogger

_ERROR_MESSAGE_LIMIT = 300
_EXTERNAL_MISSING = (
    "web_search is not configured. Set NOTEAGENT_SEARCH_PROVIDER "
    "to brave or serper, and NOTEAGENT_SEARCH_API_KEY."
)
_NO_ADAPTER = (
    "native search is selected, but this build has no adapter "
    "for the active model service"
)
_NATIVE_MISSING = (
    "native search needs a usable model connection: "
    "service address, key and model"
)


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
    mode: str | None = None
    try:
        cfg = config if config is not None else load_search_config()
        mode = cfg.mode
        if mode not in SEARCH_MODES:
            raise SearchConfigError(f"Unknown search mode {mode!r}.")
        native = cfg.native
        external_ready = cfg.configured
        support = native_support(cfg)
        if mode == "external":
            if not external_ready:
                raise ProviderNotConfiguredError(_EXTERNAL_MISSING)
        elif mode == "native":
            if native is None:
                if support == "config-missing":
                    raise NativeSearchConfigMissing(_NATIVE_MISSING)
                raise SearchModeUnavailable(_NO_ADAPTER)
        elif native is None and not external_ready:
            if support == "config-missing":
                raise NativeSearchConfigMissing(_NATIVE_MISSING)
            raise ProviderNotConfiguredError(_EXTERNAL_MISSING)

        provider = (
            native.provider
            if native is not None and mode != "external"
            else cfg.provider
        )
        logger.append(
            "search.start",
            component="acquire",
            query=cleaned,
            provider=provider,
            mode=mode,
            top_k=limit,
            caller=caller,
        )
        if mode == "external" or native is None:
            hits = web_search(query, top_k=limit, config=cfg, client=client)
        else:
            try:
                hits = native_search(
                    query,
                    top_k=limit,
                    config=native,
                    client=client,
                )
            except NativeSearchError:
                # Native mode was an explicit choice; only auto may fall back.
                if mode == "native" or not external_ready:
                    raise
                provider = cfg.provider
                hits = web_search(query, top_k=limit, config=cfg, client=client)
        cards = cards_from_hits(hits, query=cleaned, limit=limit, source=provider)
    except Exception as err:
        logger.append(
            "search.error",
            component="acquire",
            query=cleaned,
            provider=provider,
            mode=mode,
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
        mode=mode,
        caller=caller,
        hits=len(hits),
        cards=len(cards),
        urls=[card.url for card in cards],
        duration_ms=int((perf_counter() - started) * 1000),
    )
    return WebSearchRun(
        query=cleaned,
        provider=provider,
        hits=hits,
        cards=cards,
    )
