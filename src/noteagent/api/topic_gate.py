"""HTTP helpers for topic-map planning and gated ingest."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from noteagent.api.ingest import ingest_result_payload
from noteagent.api.sse import iter_job_sse
from noteagent.ingest.clips import ClipCollection, load_clip_store, save_clip_store
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.topic_map import (
    TopicMap,
    carry_over_choices,
    map_clips,
    merge_plan_board,
    page_slug,
    parse_topic_map,
)
from noteagent.wiki.deep_search import (
    act_session,
    collections_for_planning,
    load_session,
    plan_targets,
    proposals_map,
    refine_proposals,
    round_is_planning,
)
from noteagent.llm.config import LlmConfig
from noteagent.project.layout import ProjectLayout
from noteagent.util.cancel import check_cancelled
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger


def _collections_for_source(
    collections: tuple[ClipCollection, ...], source_identity: str
) -> tuple[ClipCollection, ...]:
    key = source_identity.replace("\\", "/").strip()
    if not key:
        return collections
    chosen = tuple(
        item
        for item in collections
        if item.source_key.replace("\\", "/").strip() == key
    )
    if not chosen:
        raise Refusal("plan.noSelection", "nothing selected to plan from")
    return chosen


def run_topic_map_job(
    layout: ProjectLayout,
    *,
    intent: str = "",
    source_identity: str = "",
    language: str = "",
    on_status=None,
    llm_config: LlmConfig | None = None,
    map_text: str | None = None,
    cancel=None,
) -> dict[str, Any]:
    store = load_clip_store(layout)
    planning = _collections_for_source(
        collections_for_planning(layout, store),
        source_identity,
    )
    goal = intent.strip()
    targets = plan_targets(layout, store)
    logger = RunLogger.for_layout(layout)
    topic_map = map_clips(
        layout,
        intent=goal,
        llm_config=llm_config,
        map_text=map_text,
        store=replace(store, collections=planning),
        targets=targets,
        language=language,
        on_status=on_status,
        run_logger=logger,
        cancel=cancel,
    )
    check_cancelled(cancel)
    if round_is_planning(layout):
        # A round keeps its own list of pages, whether it proposed them before reading
        # or the plan named them: one place, whichever way they came about.
        refine_proposals(layout, topic_map)
        return topic_map_payload(topic_map)
    latest = load_clip_store(layout)
    # A plan answers about the material it was handed. Re-planning one source is a
    # plan of that source's pages, so it replaces those and leaves the rest of the
    # board standing; a plan of everything answers for the whole board.
    board = topic_map
    if source_identity.strip() and latest.topic_map is not None:
        try:
            standing = parse_topic_map(
                latest.topic_map,
                intent=latest.intent,
                collections=latest.collections,
                layout=layout,
            )
        except ValueError:
            standing = None
        if standing is not None:
            board = merge_plan_board(standing, topic_map, covered=planning)
    updated = replace(
        latest,
        intent=goal,
        topic_map=board.to_dict(),
        decisions=(),
        choices=carry_over_choices(latest.choices, board),
    )
    save_clip_store(layout, updated)
    return topic_map_payload(board)


def topic_map_payload(topic_map: TopicMap) -> dict[str, Any]:
    return topic_map.to_dict()


def run_ingest_confirmed_job(
    layout: ProjectLayout,
    confirmed: list[dict[str, Any]],
    *,
    intent: str = "",
    on_status=None,
    llm_config: LlmConfig | None = None,
    generation_text: str | None = None,
    dry_run: bool = False,
    cancel=None,
) -> dict[str, Any]:
    logger = RunLogger.for_layout(layout)
    result = ingest_confirmed(
        layout,
        confirmed=confirmed,
        intent=intent or None,
        llm_config=llm_config,
        generation_text=generation_text,
        dry_run=dry_run,
        on_status=on_status,
        run_logger=logger,
        cancel=cancel,
    )
    if not dry_run:
        session = load_session(layout)
        if session.round_id and session.step >= 4:
            write_slugs = {
                page_slug(str(item.get("slug") or item.get("name") or ""))
                for item in confirmed
                if str(item.get("action") or "").strip() == "write"
            }
            write_slugs.discard("")
            used: list[str] = []
            seen: set[str] = set()
            store = load_clip_store(layout)
            # A search's pages are its own list, refined by its plan; everything else
            # is the project's plan.
            round_map = proposals_map(layout, intent=intent or store.intent)
            source_map = round_map.to_dict() if round_map is not None else store.topic_map
            if source_map and write_slugs:
                parsed = parse_topic_map(
                    source_map,
                    intent=intent or store.intent,
                    collections=store.collections,
                    layout=layout,
                )
                for node in parsed.nodes():
                    if node.slug not in write_slugs:
                        continue
                    for ident in node.collection_ids:
                        if ident in seen:
                            continue
                        seen.add(ident)
                        used.append(ident)
            act_session(
                layout,
                {
                    "action": "markIngested",
                    "collectionIds": used,
                },
            )
    return {
        "results": [ingest_result_payload(result, dry_run=dry_run)],
        "errors": [],
    }


def iter_topic_map_sse(
    layout: ProjectLayout,
    *,
    intent: str = "",
    source_identity: str = "",
    language: str = "",
) -> Iterator[bytes]:
    def worker(on_status, cancel, _emit):
        return run_topic_map_job(
            layout,
            intent=intent,
            source_identity=source_identity,
            language=language,
            on_status=on_status,
            cancel=cancel,
        )

    return iter_job_sse(worker, default_step_id="plan")


def iter_ingest_confirmed_sse(
    layout: ProjectLayout,
    confirmed: list[dict[str, Any]],
    *,
    intent: str = "",
) -> Iterator[bytes]:
    def worker(on_status, cancel, _emit):
        return run_ingest_confirmed_job(
            layout,
            confirmed,
            intent=intent,
            on_status=on_status,
            cancel=cancel,
        )

    return iter_job_sse(worker, default_step_id="compose")


__all__ = [
    "iter_ingest_confirmed_sse",
    "iter_topic_map_sse",
    "run_ingest_confirmed_job",
    "run_topic_map_job",
    "topic_map_payload",
]
