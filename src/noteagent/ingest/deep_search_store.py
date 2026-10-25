"""Persist one Deep Search round under ``.noteagent/deep-search.json``."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from noteagent.ingest.proposal import (
    PAGE_ACTIONS,
    PageProposal,
    proposal_from_wire,
    proposal_to_wire,
)
from noteagent.project.layout import ProjectLayout

_T = TypeVar("_T")

SESSION_VERSION = 2
MODES = frozenset({"", "chew", "explore"})
MAX_WHITELIST = 8
#: How many pages one pass may propose. A round proposes up to ``MAX_WHITELIST``; the
#: plan drawn from its material may refine those and add what reading turned up, so
#: the list it is stored in is the wider of the two.
MAX_PLAN_PAGES = 24
MAX_OVERLAY_EDGES = 16
MAX_INTENT = 200
MAX_HOPS = 1
CARDS_PER_PAGE = 5
MAX_CARDS = 24
SKIM_FILES = 8
SKIM_PER_FILE = 2_000
SKIM_TOTAL = 8_000
BLOCKS_PER_PAGE = 8
MAX_ROUND_COLLECTIONS = 24
GRAPH_NODE_ROWS = 80
GRAPH_EDGE_ROWS = 120
INDEX_CHARS = 4_000
STEP_CHARS = 24_000
BLOCK_PREVIEW = 200
MAX_TEXT = 400


@dataclass(frozen=True, slots=True)
class DeepSearchSeed:
    kind: str = ""
    path: str = ""
    slug: str = ""
    title: str = ""


#: A page a round proposes. The same type a plan proposes pages with, so that a
#: round's list and the plan drawn from it are two stages of one proposal.
WhitelistPage = PageProposal


@dataclass(frozen=True, slots=True)
class OverlayEdge:
    source: str
    target: str
    relation: str = ""


@dataclass(frozen=True, slots=True)
class ProposedBlock:
    page_slug: str
    locator: str
    preview: str = ""
    accepted: bool = False


@dataclass(frozen=True, slots=True)
class ExploreCard:
    page_slug: str
    title: str
    url: str
    snippet: str = ""
    site: str = ""
    picked: bool = False


@dataclass(frozen=True, slots=True)
class DeepSearchSession:
    round_id: str = ""
    mode: str = ""
    step: int = 0
    intent: str = ""
    seed: DeepSearchSeed | None = None
    whitelist: tuple[WhitelistPage, ...] = ()
    whitelist_omitted: int = 0
    overlay_edges: tuple[OverlayEdge, ...] = ()
    proposed_blocks: tuple[ProposedBlock, ...] = ()
    cards: tuple[ExploreCard, ...] = ()
    cards_omitted: int = 0
    collection_ids: tuple[str, ...] = ()
    ingested_ids: tuple[str, ...] = ()
    truncated: bool = False
    notice: str = ""
    stashed_intent: str = ""
    stashed_topic_map: dict[str, Any] | None = None
    stashed_decisions: tuple[dict[str, Any], ...] | None = None


def empty_session() -> DeepSearchSession:
    return DeepSearchSession()


def load_session(layout: ProjectLayout) -> DeepSearchSession:
    path = layout.deep_search
    if not path.is_file():
        return empty_session()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_session()
    if not isinstance(raw, dict):
        return empty_session()
    try:
        return session_from_dict(raw)
    except (TypeError, ValueError):
        return empty_session()


def save_session(layout: ProjectLayout, session: DeepSearchSession) -> None:
    layout.meta.mkdir(parents=True, exist_ok=True)
    tmp = layout.deep_search.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(session_to_dict(session), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(layout.deep_search)


def session_to_dict(session: DeepSearchSession) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": SESSION_VERSION,
        "roundId": session.round_id,
        "mode": session.mode,
        "step": session.step,
        "intent": session.intent,
        "seed": seed_to_dict(session.seed) if session.seed else None,
        "whitelist": [page_to_dict(item) for item in session.whitelist],
        "whitelistOmitted": session.whitelist_omitted,
        "overlayEdges": [edge_to_dict(item) for item in session.overlay_edges],
        "proposedBlocks": [block_to_dict(item) for item in session.proposed_blocks],
        "cards": [card_to_dict(item) for item in session.cards],
        "cardsOmitted": session.cards_omitted,
        "collectionIds": list(session.collection_ids),
        "ingestedIds": list(session.ingested_ids),
        "truncated": session.truncated,
        "notice": session.notice,
    }
    if session.stashed_intent or session.stashed_topic_map is not None:
        payload["stashedIntent"] = session.stashed_intent
        payload["stashedTopicMap"] = session.stashed_topic_map
        payload["stashedDecisions"] = list(session.stashed_decisions or ())
    return payload


def session_from_dict(raw: dict[str, Any]) -> DeepSearchSession:
    version = _int_field(raw.get("version"), 0, 99)
    if version != SESSION_VERSION:
        return empty_session()
    mode = str(raw.get("mode") or "").strip()
    if mode not in MODES:
        return empty_session()
    seed_raw = raw.get("seed")
    stash_map = raw.get("stashedTopicMap")
    if stash_map is not None and not isinstance(stash_map, dict):
        stash_map = None
    stash_decisions = raw.get("stashedDecisions")
    decisions: tuple[dict[str, Any], ...] | None = None
    if isinstance(stash_decisions, list):
        decisions = tuple(item for item in stash_decisions if isinstance(item, dict))
    return DeepSearchSession(
        round_id=_clip_text(str(raw.get("roundId") or raw.get("round_id") or ""), 40),
        mode=mode,
        step=_int_field(raw.get("step"), 0, 5),
        intent=_clip_text(str(raw.get("intent") or ""), MAX_INTENT),
        seed=seed_from_dict(seed_raw) if isinstance(seed_raw, dict) else None,
        # Read back everything that was written: a round's refined list carries the
        # plan's pages too, which are more than the eight a round proposes.
        whitelist=_tuple_of(raw.get("whitelist"), page_from_dict, MAX_PLAN_PAGES),
        whitelist_omitted=_int_field(
            raw.get("whitelistOmitted", raw.get("whitelist_omitted")), 0, 10_000
        ),
        overlay_edges=_tuple_of(
            raw.get("overlayEdges") or raw.get("overlay_edges"),
            edge_from_dict,
            MAX_OVERLAY_EDGES,
        ),
        proposed_blocks=_tuple_of(
            raw.get("proposedBlocks") or raw.get("proposed_blocks"),
            block_from_dict,
            MAX_WHITELIST * BLOCKS_PER_PAGE,
        ),
        cards=_tuple_of(raw.get("cards"), card_from_dict, MAX_CARDS),
        cards_omitted=_int_field(
            raw.get("cardsOmitted", raw.get("cards_omitted")), 0, 10_000
        ),
        collection_ids=_id_tuple(raw.get("collectionIds") or raw.get("collection_ids")),
        ingested_ids=_id_tuple(raw.get("ingestedIds") or raw.get("ingested_ids")),
        truncated=bool(raw.get("truncated")),
        notice=_clip_text(str(raw.get("notice") or "")),
        stashed_intent=_clip_text(
            str(raw.get("stashedIntent") or raw.get("stashed_intent") or ""),
            MAX_INTENT,
        ),
        stashed_topic_map=stash_map,
        stashed_decisions=decisions,
    )


def seed_to_dict(seed: DeepSearchSeed) -> dict[str, str]:
    return {
        "kind": seed.kind,
        "path": seed.path,
        "slug": seed.slug,
        "title": seed.title,
    }


def seed_from_dict(raw: dict[str, Any]) -> DeepSearchSeed:
    return DeepSearchSeed(
        kind=_clip_text(str(raw.get("kind") or ""), 40),
        path=_clip_text(str(raw.get("path") or ""), 500),
        slug=_clip_text(str(raw.get("slug") or ""), 160),
        title=_clip_text(str(raw.get("title") or ""), 160),
    )


def page_to_dict(item: WhitelistPage) -> dict[str, object]:
    """One proposal as JSON — the same shape a plan's proposal is written in."""
    return proposal_to_wire(item)


def page_from_dict(raw: object) -> WhitelistPage:
    if not isinstance(raw, dict):
        raise ValueError("each whitelist page must be an object")
    item = proposal_from_wire(raw)
    slug = _clip_text(item.slug, 160)
    if not slug:
        raise ValueError("whitelist page needs slug")
    return replace(
        item,
        slug=slug,
        title=_clip_text(item.title, 160) or slug.split("/")[-1],
        # A round's list only knows two answers to "is this page already there".
        action=item.action if item.action in PAGE_ACTIONS else "add",
        query=_clip_text(item.query, 200),
        section=_clip_text(item.section, 120),
        plan=_clip_text(item.plan),
        evidence=_clip_text(item.evidence, 400),
    )


def edge_to_dict(item: OverlayEdge) -> dict[str, str]:
    return {
        "source": item.source,
        "target": item.target,
        "relation": item.relation,
    }


def edge_from_dict(raw: object) -> OverlayEdge:
    if not isinstance(raw, dict):
        raise ValueError("each overlay edge must be an object")
    source = _clip_text(str(raw.get("source") or ""), 160)
    target = _clip_text(str(raw.get("target") or ""), 160)
    if not source or not target:
        raise ValueError("overlay edge needs source and target")
    return OverlayEdge(
        source=source,
        target=target,
        relation=_clip_text(str(raw.get("relation") or ""), 40),
    )


def block_to_dict(item: ProposedBlock) -> dict[str, object]:
    return {
        "pageSlug": item.page_slug,
        "locator": item.locator,
        "preview": item.preview,
        "accepted": item.accepted,
    }


def block_from_dict(raw: object) -> ProposedBlock:
    if not isinstance(raw, dict):
        raise ValueError("each proposed block must be an object")
    locator = _clip_text(str(raw.get("locator") or ""), 240)
    if not locator:
        raise ValueError("proposed block needs locator")
    return ProposedBlock(
        page_slug=_clip_text(str(raw.get("pageSlug") or raw.get("page_slug") or ""), 160),
        locator=locator,
        preview=_clip_text(str(raw.get("preview") or ""), BLOCK_PREVIEW),
        accepted=bool(raw.get("accepted")),
    )


def card_to_dict(item: ExploreCard) -> dict[str, object]:
    return {
        "pageSlug": item.page_slug,
        "title": item.title,
        "url": item.url,
        "snippet": item.snippet,
        "site": item.site,
        "picked": item.picked,
    }


def card_from_dict(raw: object) -> ExploreCard:
    if not isinstance(raw, dict):
        raise ValueError("each card must be an object")
    url = _clip_text(str(raw.get("url") or ""), 500)
    if not url:
        raise ValueError("card needs url")
    return ExploreCard(
        page_slug=_clip_text(str(raw.get("pageSlug") or raw.get("page_slug") or ""), 160),
        title=_clip_text(str(raw.get("title") or ""), 200) or url,
        url=url,
        snippet=_clip_text(str(raw.get("snippet") or ""), 280),
        site=_clip_text(str(raw.get("site") or ""), 120),
        picked=bool(raw.get("picked")),
    )


def _tuple_of(
    raw: object,
    parse: Callable[[object], _T],
    cap: int,
) -> tuple[_T, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[_T] = []
    for item in raw[:cap]:
        try:
            out.append(parse(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _id_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw[:MAX_ROUND_COLLECTIONS]:
        text = _clip_text(str(item), 80)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _clip_text(text: str, limit: int = MAX_TEXT) -> str:
    return " ".join(text.split())[:limit]


def _int_field(raw: object, default: int, cap: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return max(0, min(cap, value))
