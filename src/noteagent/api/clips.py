"""HTTP helpers for persisted clip collections."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from noteagent.ingest.clips import (
    ClipStore,
    clip_store_from_dict,
    clip_store_to_dict,
    decision_to_dict,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.revision_inbox import note_source_change
from noteagent.ingest.topic_map import parse_topic_map
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
    save_clip_store(layout, store)
    note_source_change(layout)
    return clips_payload(store)


__all__ = [
    "clips_payload",
    "get_clips_payload",
    "put_clips_payload",
]
