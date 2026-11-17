"""Deterministic frames: headings first, pages next, the block cap last.

A frame is a run of blocks that belongs together: one heading and what it
introduces, or a stretch of pages when the document has no headings. It becomes
a selection of block ids, in document order, not a copy of their text: the text
is read from the staged material when a later step needs it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from noteagent.ingest.clips import (
    MAX_BLOCKS_PER_COLLECTION,
    MAX_COLLECTIONS,
    ClipCollection,
    load_clip_store,
    save_clip_store,
)
from noteagent.pipeline.frame_model import MAX_FRAMES, ModelFrames, ModelFraming
from noteagent.pipeline.materials import MaterialState, Materials
from noteagent.pipeline.stage import FrameSummary, StageContext
from noteagent.project.layout import ProjectLayout

#: A heading opens a frame; everything after it belongs to that frame until the
#: next heading.
HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: The id prefix that marks a collection made by framing rather than by a person.
FRAME_PREFIX = "frame-"


def frame_blocks(blocks: Sequence[Mapping[str, object]]) -> list[list[str]]:
    """The block ids of one material, grouped into frames in document order."""
    ids: list[str] = []
    tags: list[str] = []
    for block in blocks:
        block_id = str(block.get("id") or "").strip()
        if not block_id:
            continue
        ids.append(block_id)
        tags.append(str(block.get("tag") or "").lower())
    if not ids:
        return []

    starts = [index for index, tag in enumerate(tags) if tag in HEADING_TAGS]
    if starts:
        groups: list[list[str]] = []
        if starts[0] > 0:
            groups.append(ids[: starts[0]])
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(ids)
            groups.append(ids[start:end])
    elif any(tag == "page" for tag in tags):
        # No headings, but the document has pages (a PDF): a page is the frame,
        # and the cap below gathers them when there are more than eight.
        groups = [[block_id] for block_id in ids]
    else:
        # No headings and no pages: the cap below is the only structure there is.
        groups = [ids]

    if len(groups) > MAX_FRAMES:
        flat = [block_id for group in groups for block_id in group]
        base, extra = divmod(len(flat), MAX_FRAMES)
        groups = []
        offset = 0
        for index in range(MAX_FRAMES):
            size = base + (1 if index < extra else 0)
            groups.append(flat[offset : offset + size])
            offset += size

    # Eight frames is the target, applied above. A document over 8*200 blocks
    # cannot satisfy both that and the collection cap; the collection cap is the
    # hard one, so such a document gets more than eight frames rather than
    # losing blocks that no one would ever see.
    frames: list[list[str]] = []
    for group in groups:
        for offset in range(0, len(group), MAX_BLOCKS_PER_COLLECTION):
            chunk = group[offset : offset + MAX_BLOCKS_PER_COLLECTION]
            if chunk:
                frames.append(chunk)
    return frames


def frame_id(material: str, index: int) -> str:
    """A stable name for one material's frame, so re-framing replaces it."""
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]
    return f"{FRAME_PREFIX}{digest}-{index}"


def is_frame(collection: ClipCollection) -> bool:
    return collection.id.startswith(FRAME_PREFIX)


class Framing:
    """Turns a run's staged materials into selections of their blocks."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        materials: Materials,
        model: ModelFraming | None = None,
    ) -> None:
        self._layout = layout
        self._materials = materials
        self._model = model

    def frame_run(self, run_id: str) -> FrameSummary:
        """Frame the run's staged materials; answers how many and what it cost."""
        all_states = self._materials.for_run(run_id)
        # A material a person edited keeps the frames they gave it. Auto framing
        # is the default, not a thing that overwrites a decision.
        human_owned = {
            state.material
            for state in self._materials.states().values()
            if state.human_edited
        }
        states = [
            state
            for state in all_states
            if state.state in {"staged", "framed"}
            and state.material not in human_owned
        ]
        keep_sources = {state.material for state in states} | human_owned
        store = load_clip_store(self._layout)
        # Frames of materials this run no longer holds, and nobody edited, are
        # from an older run and would keep the selection list growing.
        kept = [
            collection
            for collection in store.collections
            if not (
                is_frame(collection)
                and collection.source_key not in keep_sources
            )
        ]

        changed = False
        pending: list[_Pending] = []
        model_calls = 0
        chars_read = 0
        fallbacks = 0
        omitted = 0
        for state in states:
            blocks = self._materials.blocks(state.material, run_id=run_id)
            if blocks is None:
                continue
            result = (
                self._model.frames(
                    material=state.material,
                    title=state.title,
                    kind=str(state.detail.get("kind") or ""),
                    blocks=blocks,
                )
                if self._model is not None
                else None
            )
            if result is not None:
                model_calls += result.calls
                chars_read += result.chars
                omitted += result.omitted
            if result is not None and result.frames:
                # A frame the model gave still has to fit one collection.
                frames = _split_long_frames(
                    [list(frame) for frame in result.frames]
                )
                source = "model"
            else:
                # A model that could not or would not return frames leaves the
                # deterministic split to do it, with no error and no hole.
                frames = frame_blocks(blocks)
                source = "system"
                if self._model is not None:
                    fallbacks += 1
            if not frames:
                continue
            existing = [
                collection
                for collection in kept
                if is_frame(collection) and collection.source_key == state.material
            ]
            new = [
                self._collection(state, frame, index)
                for index, frame in enumerate(frames)
            ]
            changed = changed or _frames_differ(existing, new)
            kept = [
                collection
                for collection in kept
                if not (
                    is_frame(collection) and collection.source_key == state.material
                )
            ] + new
            pending.append(_Pending(state, len(new), source, result))

        final = (
            kept if len(kept) <= MAX_COLLECTIONS else _fit(kept, MAX_COLLECTIONS)
        )
        surviving = {
            collection.source_key for collection in final if is_frame(collection)
        }
        framed = 0
        for entry in pending:
            # A material whose frames did not fit is not marked framed: its
            # selection list would claim frames that are not there.
            if entry.state.material not in surviving:
                continue
            self._materials.mark(
                entry.state.material,
                "framed",
                run_id=run_id,
                source=entry.source,
                detail={
                    "frames": entry.count,
                    "title": entry.state.title,
                    "kind": entry.state.detail.get("kind", ""),
                    "usage": _usage(entry.result),
                },
            )
            framed += 1

        updated = replace(store, collections=tuple(final))
        if changed:
            # A plan answers the frames it was made against; new frames make it
            # a statement about material that is no longer the material.
            updated = replace(updated, topic_map=None, decisions=())
        save_clip_store(self._layout, updated)
        return FrameSummary(
            framed=framed,
            model_calls=model_calls,
            chars_read=chars_read,
            fallbacks=fallbacks,
            omitted=omitted,
        )

    def _collection(
        self, state: MaterialState, frame: Sequence[str], index: int
    ) -> ClipCollection:
        return ClipCollection(
            id=frame_id(state.material, index),
            source_key=state.material,
            source_label=state.title,
            block_ids=tuple(frame),
            blocks=(),
        )


@dataclass(slots=True)
class FrameStage:
    """The stage that frames whatever the acquisition collected."""

    name: str = "frame"

    def __call__(self, context: StageContext) -> None:
        summary = context.frame_materials()
        context.add_detail(
            framedMaterials=summary.framed,
            frameUsage=summary.usage(),
        )


@dataclass(frozen=True, slots=True)
class _Pending:
    """One material's frames, waiting to learn whether they fit the store."""

    state: MaterialState
    count: int
    source: str
    result: ModelFrames | None


def _usage(result: ModelFrames | None) -> dict[str, object]:
    if result is None:
        return {"calls": 0, "chars": 0, "omitted": 0, "failed": False}
    return {
        "calls": result.calls,
        "chars": result.chars,
        "omitted": result.omitted,
        "failed": result.failed,
    }


def _split_long_frames(frames: Sequence[Sequence[str]]) -> list[list[str]]:
    """Cut a model's frame at the collection cap; a frame is a selection."""
    out: list[list[str]] = []
    for frame in frames:
        for offset in range(0, len(frame), MAX_BLOCKS_PER_COLLECTION):
            chunk = list(frame[offset : offset + MAX_BLOCKS_PER_COLLECTION])
            if chunk:
                out.append(chunk)
    return out


def _frames_differ(
    existing: Sequence[ClipCollection], new: Sequence[ClipCollection]
) -> bool:
    return [collection.block_ids for collection in existing] != [
        collection.block_ids for collection in new
    ]


def _fit(collections: list[ClipCollection], limit: int) -> list[ClipCollection]:
    """Keep a person's selections, then one frame per material, then the rest."""
    manual = [collection for collection in collections if not is_frame(collection)]
    frames = [collection for collection in collections if is_frame(collection)]
    first: dict[str, ClipCollection] = {}
    rest: list[ClipCollection] = []
    for collection in frames:
        if collection.source_key in first:
            rest.append(collection)
        else:
            first[collection.source_key] = collection
    ordered = list(first.values()) + rest
    room = max(0, limit - len(manual))
    return manual + ordered[:room]


__all__ = [
    "FRAME_PREFIX",
    "FrameStage",
    "Framing",
    "HEADING_TAGS",
    "MAX_FRAMES",
    "frame_blocks",
    "frame_id",
    "is_frame",
]
