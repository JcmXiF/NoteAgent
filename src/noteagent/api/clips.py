"""HTTP helpers for persisted clip collections."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from noteagent.agent.clock import SystemClock, iso_timestamp
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    clip_store_from_dict,
    clip_store_to_dict,
    decision_to_dict,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.revision_inbox import note_source_change
from noteagent.ingest.topic_map import parse_topic_map
from noteagent.pipeline.materials import MaterialLog, MaterialTransition
from noteagent.project.layout import ProjectLayout


def clips_payload(store: ClipStore) -> dict[str, Any]:
    data = clip_store_to_dict(store)
    return {
        "intent": data["intent"],
        "collections": data["collections"],
        "topicMap": data["topicMap"],
        "decisions": data.get("decisions") or [],
        "choices": data.get("choices") or [],
    }


def get_clips_payload(layout: ProjectLayout) -> dict[str, Any]:
    store = load_clip_store(layout)
    if store.topic_map is not None:
        try:
            parsed = parse_topic_map(
                store.topic_map,
                intent=store.intent,
                collections=store.collections,
                layout=layout,
            )
            store = replace(store, topic_map=parsed.to_dict())
        except ValueError:
            store = replace(store, topic_map=None, decisions=())
    return clips_payload(store)


def put_clips_payload(layout: ProjectLayout, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    # An answer nobody sent is not the same as an answer cleared: a caller that does
    # not know about a field must not wipe it.
    disk = load_clip_store(layout)
    if payload.get("decisions") is None:
        payload["decisions"] = [decision_to_dict(item) for item in disk.decisions]
    if payload.get("choices") is None:
        payload["choices"] = [decision_to_dict(item) for item in disk.choices]
    store = clip_store_from_dict(payload)
    if store.topic_map is not None:
        # Re-validate so a corrupt map cannot sit on disk.
        parsed = parse_topic_map(
            store.topic_map,
            intent=store.intent,
            collections=store.collections,
            layout=layout,
        )
        store = replace(store, topic_map=parsed.to_dict())
    if disk.intent != store.intent or _collections_changed(
        disk.collections, store.collections
    ):
        # A plan answers the intent and frames it was made against. Once either
        # changes, the plan is a statement about a question nobody asked.
        store = replace(store, topic_map=None, decisions=())
    save_clip_store(layout, store)
    _mark_human_frames(layout, disk.collections, store.collections)
    note_source_change(layout)
    return clips_payload(store)


def _mark_human_frames(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
) -> None:
    """Record the frames a person drew, so a later run leaves them alone."""
    changed = _changed_sources(before, after)
    if not changed:
        return
    log = MaterialLog(layout.materials_log)
    states = log.states()
    now = iso_timestamp(SystemClock())
    for source, frames in changed.items():
        state = states.get(source)
        if state is None:
            # A selection from a page that is not a material of this pipeline.
            continue
        try:
            log.append(
                MaterialTransition(
                    at=now,
                    material=source,
                    state="framed",
                    run_id=state.run_id,
                    source="human",
                    detail={"frames": frames},
                )
            )
        except OSError:
            continue


def _changed_sources(
    before: Sequence[ClipCollection], after: Sequence[ClipCollection]
) -> dict[str, int]:
    """Which materials' selections changed, with how many frames each now has."""

    def grouped(
        collections: Sequence[ClipCollection],
    ) -> dict[str, list[tuple[str, ...]]]:
        out: dict[str, list[tuple[str, ...]]] = {}
        for collection in collections:
            out.setdefault(collection.source_key, []).append(collection.block_ids)
        return out

    old = grouped(before)
    new = grouped(after)
    return {
        source: len(new.get(source, []))
        for source in set(old) | set(new)
        if old.get(source) != new.get(source)
    }


def _collections_changed(
    before: Sequence[ClipCollection], after: Sequence[ClipCollection]
) -> bool:
    """Whether the selections themselves changed, ignoring their order."""
    return _signature(before) != _signature(after)


def _signature(
    collections: Sequence[ClipCollection],
) -> dict[str, tuple[object, ...]]:
    return {
        collection.id: (
            collection.source_key,
            collection.block_ids,
            collection.round_id,
            collection.target_slug,
        )
        for collection in collections
    }


__all__ = [
    "clips_payload",
    "get_clips_payload",
    "put_clips_payload",
]
