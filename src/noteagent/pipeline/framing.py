"""Deterministic frames: headings first, pages next, the block cap last.

A frame is a run of blocks that belongs together: one heading and what it
introduces, or a stretch of pages when the document has no headings. It becomes
a selection of block ids, in document order, not a copy of their text: the text
is read from the staged material when a later step needs it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from noteagent.ingest.clips import (
    MAX_BLOCKS_PER_COLLECTION,
    ClipCollection,
)
from noteagent.pipeline.frame_model import MAX_FRAMES

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


__all__ = [
    "FRAME_PREFIX",
    "HEADING_TAGS",
    "MAX_FRAMES",
    "frame_blocks",
    "frame_id",
    "is_frame",
]
