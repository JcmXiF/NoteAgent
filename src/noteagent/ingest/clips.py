"""Persist marquee clip collections under ``.noteagent/`` (never raw bytes)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from noteagent.project.layout import ProjectLayout
from noteagent.util.refusal import Refusal

CLIPS_VERSION = 1
MAX_COLLECTIONS = 40
MAX_BLOCKS_PER_COLLECTION = 200
MAX_TEXT_CHARS = 8_000
MAX_INTENT_CHARS = 200
MAX_DECISIONS = 20
PREVIEW_PREFIX = "preview:"
RAW_PREFIX = "raw/"
GATE_ACTIONS = frozenset({"write", "skip", "mention"})


@dataclass(frozen=True, slots=True)
class DerivedBlock:
    """One leaf block snapshot copied from the reader HTML."""

    id: str
    tag: str
    text: str
    preview: str


@dataclass(frozen=True, slots=True)
class ClipCollection:
    """One marquee: ordered block ids plus text snapshots."""

    id: str
    source_key: str
    source_label: str
    block_ids: tuple[str, ...]
    blocks: tuple[DerivedBlock, ...]
    round_id: str = ""
    target_slug: str = ""


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Human topic-gate choice for one planned page. Empty store means ungated."""

    slug: str
    action: str
    section_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClipStore:
    """Project-scoped clip list plus the session intent string."""

    intent: str
    collections: tuple[ClipCollection, ...]
    topic_map: dict[str, Any] | None = None
    decisions: tuple[GateDecision, ...] = ()
    #: What the reader answered on the plan board, kept so a later plan can carry it
    #: over instead of asking again. Not the same thing as ``decisions``: that is the
    #: gate — only the answers belonging to the pass that was confirmed — and a
    #: re-plan ends a confirmation without the reader having re-answered.
    choices: tuple[GateDecision, ...] = ()


_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIP_HEADLINES = frozenset({"编辑", "编辑 ]", "edit", "[edit]"})


def collection_headline(collection: ClipCollection, *, max_chars: int = 42) -> str:
    """
    Short title for planning UI: first real heading, else first prose line.
    """
    by_id = {block.id: block for block in collection.blocks}
    fallback = ""
    for block_id in collection.block_ids:
        block = by_id.get(block_id)
        if block is None:
            continue
        text = " ".join(block.text.split())
        if not text:
            continue
        tag = (block.tag or "").lower()
        if tag in _HEADING_TAGS and text not in _SKIP_HEADLINES:
            return _clip_chars(text, max_chars)
        if not fallback and text not in _SKIP_HEADLINES and not text.startswith("[图"):
            fallback = text
    if fallback:
        return _clip_chars(fallback, max_chars)
    label = (collection.source_label or "").strip()
    return _clip_chars(label, max_chars) if label else "未命名集合"


def _clip_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def format_collection_text(collection: ClipCollection) -> str:
    """Join selected blocks as ``[bN] text`` for prompts and the UI."""
    by_id = {block.id: block for block in collection.blocks}
    parts: list[str] = []
    for block_id in collection.block_ids:
        block = by_id.get(block_id)
        if block is None or not block.text.strip():
            continue
        parts.append(f"[{block.id}] {block.text.strip()}")
    return "\n\n".join(parts)


def collections_as_source_text(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> str:
    """Derived text for the topic-map / confirmed-generation prompts."""
    chunks: list[str] = []
    for index, collection in enumerate(collections, start=1):
        body = format_collection_text(collection)
        chunks.append(
            f"### 集合 {index} · {collection_headline(collection)}\n"
            f"id={collection.id}\n"
            f"source: {collection.source_key}\n"
            f"label: {collection.source_label}\n\n"
            f"{body or '（空集合）'}"
        )
    return "\n\n".join(chunks)


def source_locator(source_key: str, block_id: str) -> str | None:
    """
    Frontmatter locator ``path#block_id``. Preview-only keys are not durable.
    """
    key = source_key.replace("\\", "/").strip()
    bid = block_id.strip()
    if not key.startswith(RAW_PREFIX) or not bid:
        return None
    return f"{key}#{bid}"


def is_preview_source_key(source_key: str) -> bool:
    return source_key.replace("\\", "/").strip().startswith(PREVIEW_PREFIX)


def durable_source_file(layout: ProjectLayout, source_key: str) -> Path | None:
    """On-disk file for a ``raw/`` key, or None if the key is not durable."""
    key = source_key.replace("\\", "/").strip()
    if not key.startswith(RAW_PREFIX) or ".." in key.split("/"):
        return None
    try:
        root = layout.root.expanduser().resolve()
        candidate = (root / key).resolve()
        relative = candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    if not relative.as_posix().startswith(RAW_PREFIX):
        return None
    if not candidate.is_file():
        return None
    return candidate


def require_durable_sources(
    layout: ProjectLayout,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> None:
    """
    After ingest stores selected originals, every collection must point at an
    existing raw file. Preview keys and missing files are rejected.
    """
    missing: list[str] = []
    for collection in collections:
        key = collection.source_key.replace("\\", "/").strip()
        if is_preview_source_key(key) or durable_source_file(layout, key) is None:
            missing.append(collection_headline(collection))
    if not missing:
        return
    listed = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
    raise Refusal(
        "source.missingOriginal",
        "selected collections have no saved original",
        params={"collections": listed},
    )


def locators_for_blocks(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
    block_ids: set[str] | frozenset[str],
) -> list[str]:
    """Stable ``path#bN`` list for the given block ids across collections."""
    seen: set[str] = set()
    out: list[str] = []
    wanted = {item.strip() for item in block_ids if item.strip()}
    for collection in collections:
        for block_id in collection.block_ids:
            if block_id not in wanted:
                continue
            locator = source_locator(collection.source_key, block_id)
            if locator is None or locator in seen:
                continue
            seen.add(locator)
            out.append(locator)
    return out


def locators_for_refs(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
    refs: list[tuple[str, str]],
) -> list[str]:
    """Locators for ``(collection_id, block_id)`` pairs; ignores unknown ids."""
    by_id = {item.id: item for item in collections}
    seen: set[str] = set()
    out: list[str] = []
    for collection_id, block_id in refs:
        collection = by_id.get(collection_id)
        if collection is None or block_id not in collection.block_ids:
            continue
        locator = source_locator(collection.source_key, block_id)
        if locator is None or locator in seen:
            continue
        seen.add(locator)
        out.append(locator)
    return out


def format_collection_catalog(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> str:
    """Compact catalog (id, source, block ids) for planning prompts."""
    lines: list[str] = []
    for index, collection in enumerate(collections, start=1):
        blocks = ", ".join(collection.block_ids)
        lines.append(
            f"- 集合 {index} id={collection.id} · {collection_headline(collection)}\n"
            f"  source: {collection.source_key}\n"
            f"  blocks: {blocks or '（空）'}"
        )
    return "\n".join(lines)


def empty_store() -> ClipStore:
    return ClipStore(intent="", collections=(), topic_map=None)


def load_clip_store(layout: ProjectLayout) -> ClipStore:
    path = layout.clips
    if not path.is_file():
        return empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"clips.json is not valid JSON: {err}") from err
    if not isinstance(raw, dict):
        raise ValueError("clips.json must be an object")
    return clip_store_from_dict(raw)


def save_clip_store(layout: ProjectLayout, store: ClipStore) -> None:
    layout.meta.mkdir(parents=True, exist_ok=True)
    payload = clip_store_to_dict(store)
    tmp = layout.clips.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(layout.clips)


def clip_store_to_dict(store: ClipStore) -> dict[str, Any]:
    return {
        "version": CLIPS_VERSION,
        "intent": store.intent,
        "collections": [collection_to_dict(item) for item in store.collections],
        "topicMap": store.topic_map,
        "decisions": [decision_to_dict(item) for item in store.decisions],
        "choices": [decision_to_dict(item) for item in store.choices],
    }


def clip_store_from_dict(raw: dict[str, Any]) -> ClipStore:
    intent = raw.get("intent") or ""
    if not isinstance(intent, str):
        raise ValueError("intent must be a string")
    intent = intent.strip()[:MAX_INTENT_CHARS]
    items = raw.get("collections")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("collections must be a list")
    if len(items) > MAX_COLLECTIONS:
        raise ValueError(f"at most {MAX_COLLECTIONS} clip collections")
    collections: list[ClipCollection] = []
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each collection must be an object")
        collection = collection_from_dict(item)
        if collection.id in seen_ids:
            raise ValueError(f"duplicate collection id: {collection.id}")
        seen_ids.add(collection.id)
        collections.append(collection)
    topic_map = raw.get("topicMap")
    if topic_map is not None and not isinstance(topic_map, dict):
        raise ValueError("topicMap must be an object or null")
    return ClipStore(
        intent=intent,
        collections=tuple(collections),
        topic_map=topic_map,
        decisions=decisions_from_raw(raw.get("decisions")),
        choices=decisions_from_raw(raw.get("choices")),
    )


def decision_to_dict(item: GateDecision) -> dict[str, Any]:
    return {
        "slug": item.slug,
        "action": item.action,
        "sectionIds": list(item.section_ids),
    }


def decisions_from_raw(raw: object) -> tuple[GateDecision, ...]:
    if raw is None:
        return ()
    if isinstance(raw, dict):
        items = list(raw.values())
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("decisions must be a list")
    if len(items) > MAX_DECISIONS:
        raise ValueError(f"at most {MAX_DECISIONS} gate decisions")
    out: list[GateDecision] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each decision must be an object")
        slug = str(item.get("slug") or "").strip()
        action = str(item.get("action") or "skip").strip()
        if not slug or action not in GATE_ACTIONS or slug in seen:
            continue
        seen.add(slug)
        section_raw = item.get("sectionIds")
        if section_raw is None:
            section_raw = item.get("section_ids") or []
        if not isinstance(section_raw, list):
            raise ValueError("sectionIds must be a list")
        section_ids = tuple(
            str(sid).strip()
            for sid in section_raw
            if isinstance(sid, str) and sid.strip()
        )
        out.append(GateDecision(slug=slug, action=action, section_ids=section_ids))
    return tuple(out)


def collection_to_dict(collection: ClipCollection) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": collection.id,
        "sourceKey": collection.source_key,
        "sourceLabel": collection.source_label,
        "blockIds": list(collection.block_ids),
        "blocks": [
            {
                "id": block.id,
                "tag": block.tag,
                "text": block.text,
                "preview": block.preview,
            }
            for block in collection.blocks
        ],
    }
    if collection.round_id:
        payload["roundId"] = collection.round_id
    if collection.target_slug:
        payload["targetSlug"] = collection.target_slug
    return payload


def collection_from_dict(raw: dict[str, Any]) -> ClipCollection:
    ident = _required_str(raw, "id", also="id")
    source_key = _normalize_source_key(_required_str(raw, "sourceKey", also="source_key"))
    source_label = str(raw.get("sourceLabel") or raw.get("source_label") or "").strip()
    if len(source_label) > 240:
        source_label = source_label[:240]
    block_ids_raw = raw.get("blockIds")
    if block_ids_raw is None:
        block_ids_raw = raw.get("block_ids")
    if not isinstance(block_ids_raw, list) or not block_ids_raw:
        raise ValueError("blockIds must be a non-empty list")
    if len(block_ids_raw) > MAX_BLOCKS_PER_COLLECTION:
        raise ValueError(
            f"at most {MAX_BLOCKS_PER_COLLECTION} blocks per collection"
        )
    block_ids: list[str] = []
    for item in block_ids_raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("block id must be a non-empty string")
        bid = item.strip()
        if bid not in block_ids:
            block_ids.append(bid)
    blocks_raw = raw.get("blocks") or []
    if not isinstance(blocks_raw, list):
        raise ValueError("blocks must be a list")
    blocks: list[DerivedBlock] = []
    seen_block: set[str] = set()
    for item in blocks_raw:
        if not isinstance(item, dict):
            raise ValueError("each block must be an object")
        block = _block_from_dict(item)
        if block.id in seen_block:
            continue
        seen_block.add(block.id)
        blocks.append(block)
    return ClipCollection(
        id=ident,
        source_key=source_key,
        source_label=source_label or source_key,
        block_ids=tuple(block_ids),
        blocks=tuple(blocks),
        round_id=str(raw.get("roundId") or raw.get("round_id") or "").strip()[:40],
        target_slug=str(raw.get("targetSlug") or raw.get("target_slug") or "").strip()[:160],
    )


def drop_collections_for_source(store: ClipStore, identity: str) -> ClipStore:
    """Drop clip collections whose durable source was deleted."""
    kept = tuple(
        item for item in store.collections if item.source_key != identity
    )
    if len(kept) == len(store.collections):
        return store
    return replace(store, collections=kept)


def _block_from_dict(raw: dict[str, Any]) -> DerivedBlock:
    ident = _required_str(raw, "id")
    tag = str(raw.get("tag") or "").strip()[:32]
    text = str(raw.get("text") or "")[:MAX_TEXT_CHARS]
    preview = str(raw.get("preview") or "")[:240]
    return DerivedBlock(id=ident, tag=tag, text=text, preview=preview)


def _required_str(raw: dict[str, Any], camel: str, *, also: str | None = None) -> str:
    value = raw.get(camel)
    if value is None and also is not None:
        value = raw.get(also)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{camel} must be a non-empty string")
    text = value.strip()
    if len(text) > 240:
        raise ValueError(f"{camel} is too long")
    return text


def _normalize_source_key(key: str) -> str:
    normalized = key.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("sourceKey must be a non-empty string")
    if ".." in normalized.split("/"):
        raise ValueError(f"unsafe sourceKey: {key}")
    if normalized.startswith(PREVIEW_PREFIX):
        rest = normalized[len(PREVIEW_PREFIX) :].strip()
        if not rest:
            raise ValueError("preview sourceKey needs a URL")
        return f"{PREVIEW_PREFIX}{rest}"
    if normalized.startswith(RAW_PREFIX):
        return normalized
    raise ValueError("sourceKey must be a raw/ path or preview: URL")


__all__ = [
    "CLIPS_VERSION",
    "ClipCollection",
    "ClipStore",
    "DerivedBlock",
    "GATE_ACTIONS",
    "GateDecision",
    "MAX_BLOCKS_PER_COLLECTION",
    "MAX_COLLECTIONS",
    "MAX_DECISIONS",
    "MAX_INTENT_CHARS",
    "MAX_TEXT_CHARS",
    "PREVIEW_PREFIX",
    "RAW_PREFIX",
    "clip_store_from_dict",
    "clip_store_to_dict",
    "collection_from_dict",
    "collection_headline",
    "collection_to_dict",
    "collections_as_source_text",
    "decision_to_dict",
    "decisions_from_raw",
    "drop_collections_for_source",
    "durable_source_file",
    "empty_store",
    "format_collection_catalog",
    "format_collection_text",
    "is_preview_source_key",
    "load_clip_store",
    "locators_for_blocks",
    "locators_for_refs",
    "require_durable_sources",
    "save_clip_store",
    "source_locator",
]
