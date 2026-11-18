"""HTTP helpers for persisted clip collections."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from noteagent.agent.clock import Clock, SystemClock
from noteagent.ingest.clips import (
    RAW_PREFIX,
    ClipCollection,
    ClipStore,
    clip_store_from_dict,
    clip_store_to_dict,
    decision_to_dict,
    durable_source_file,
    is_preview_source_key,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.revision_inbox import note_source_change
from noteagent.ingest.topic_map import parse_topic_map
from noteagent.pipeline.materials import Materials
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


def put_clips_payload(
    layout: ProjectLayout,
    body: dict[str, Any],
    *,
    clock: Clock | None = None,
) -> dict[str, Any]:
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
        layout, disk.collections, store.collections
    ):
        # A plan answers the intent and frames it was made against. Once either
        # changes, the plan is a statement about a question nobody asked.
        store = replace(store, topic_map=None, decisions=())
    save_clip_store(layout, store)
    _mark_human_frames(
        layout,
        disk.collections,
        store.collections,
        clock=clock or SystemClock(),
    )
    note_source_change(layout)
    return clips_payload(store)


def _mark_human_frames(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
    *,
    clock: Clock,
) -> None:
    """Record the frames a person drew, so a later run leaves them alone."""
    changed = _changed_sources(layout, before, after)
    if not changed:
        return
    materials = Materials(layout, clock=clock)
    states = materials.states()
    for source, frames in changed.items():
        state = states.get(source)
        if state is None:
            # A selection from a page that is not a material of this pipeline.
            continue
        try:
            materials.mark(
                source,
                "framed",
                run_id=state.run_id,
                source="human",
                detail={"frames": frames},
            )
        except OSError:
            continue


def _changed_sources(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
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
    new = grouped(_with_original_source_keys(layout, before, after))
    return {
        source: len(new.get(source, []))
        for source in set(old) | set(new)
        if old.get(source) != new.get(source)
    }


def _collections_changed(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
) -> bool:
    """Whether the selections themselves changed, ignoring their order.

    A preview key becoming the raw path its original was stored at is the same
    selection: the blocks are the same, the original exists, and the rewrite is
    what makes the plan compilable.
    """
    return _signature(before) != _signature(
        _with_original_source_keys(layout, before, after)
    )


def _rewritten_collection_ids(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
) -> set[str]:
    """Collections whose key became a stored raw path, with the same identity.

    The frontend knows the exact pair it wrote; the core keeps only the identity
    a selection has (its blocks, round and target) and that the path is a durable
    original. A preview key that points at nothing stored is not a rewrite.
    """
    old = {collection.id: collection for collection in before}
    out: set[str] = set()
    for collection in after:
        prior = old.get(collection.id)
        if prior is None or prior.source_key == collection.source_key:
            continue
        if not is_preview_source_key(prior.source_key):
            continue
        if not collection.source_key.startswith(RAW_PREFIX):
            continue
        if durable_source_file(layout, collection.source_key) is None:
            continue
        if _selection_identity(prior) == _selection_identity(collection):
            out.add(collection.id)
    return out


def _selection_identity(
    collection: ClipCollection,
) -> tuple[object, ...]:
    """What a selection is, apart from where its original is kept."""
    return (collection.block_ids, collection.round_id, collection.target_slug)


def _with_original_source_keys(
    layout: ProjectLayout,
    before: Sequence[ClipCollection],
    after: Sequence[ClipCollection],
) -> list[ClipCollection]:
    """The after side, with a rewritten key read under the key it came from."""
    rewritten = _rewritten_collection_ids(layout, before, after)
    old = {collection.id: collection for collection in before}
    return [
        replace(collection, source_key=old[collection.id].source_key)
        if collection.id in rewritten
        else collection
        for collection in after
    ]


def _signature(
    collections: Sequence[ClipCollection],
) -> dict[str, tuple[object, ...]]:
    return {
        collection.id: (collection.source_key, *_selection_identity(collection))
        for collection in collections
    }


__all__ = [
    "clips_payload",
    "get_clips_payload",
    "put_clips_payload",
]
