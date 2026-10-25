"""Index which wiki notes cite each raw file.

Reads durable ``sources:`` locators (``path`` or ``path#block_id``) and splits
each note into one mark per wiki section (``<!-- na-clip:id -->``) or clip
collection. Index / overview / log / wiki/sources summaries are not notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from noteagent.ingest.clips import ClipCollection, load_clip_store
from noteagent.ingest.clip_ledger import (
    parse_clip_summary_collections,
    raw_source_path,
    split_source_locator,
    unique_ids,
)
from noteagent.ingest.frontmatter import ParsedPage, parse_page, unquote_scalar
from noteagent.ingest.marker_lines import (
    CLIP_MARK_LINE,
    heading_text,
    next_content_line,
    split_lines,
)
from noteagent.ingest.page_merge import page_sources
from noteagent.project.layout import (
    WIKI_INDEX,
    WIKI_LOG,
    WIKI_OVERVIEW,
    ProjectLayout,
)

_SKIP_WIKI = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})
_SUMMARY_PREFIX = "wiki/sources/"
_BLOCK_INDEX = re.compile(r"^[bB](\d+)$")


@dataclass(frozen=True, slots=True)
class CitingNote:
    """One wiki note that cites a raw original (list / badge)."""

    path: str
    title: str


@dataclass(frozen=True, slots=True)
class CitationMark:
    """One citation of a raw original: a wiki section or leftover locator set."""

    id: str
    path: str
    title: str
    heading: str
    collection_id: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClipSection:
    """A wiki heading bound to ``<!-- na-clip:id -->``."""

    heading: str
    collection_id: str
    block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClipMark:
    """One ``na-clip`` marker on disk and the heading it owns.

    ``heading`` is ``None`` for a marker that owns none — see
    :func:`scan_clip_marks` for when that happens.
    """

    collection_id: str
    block_ids: tuple[str, ...]
    heading: str | None


@dataclass(frozen=True, slots=True)
class SourceCitationIndex:
    notes: dict[str, list[CitingNote]]
    marks: dict[str, list[CitationMark]]


def is_citing_wiki_page(relative: str) -> bool:
    """True when *relative* is a note page that may list ``sources``."""
    path = relative.replace("\\", "/").lstrip("./")
    if not path.startswith("wiki/") or not path.endswith(".md"):
        return False
    if path in _SKIP_WIKI:
        return False
    if path.startswith(_SUMMARY_PREFIX):
        return False
    return True


def _page_title(relative: str, page: ParsedPage) -> str:
    if page.has_frontmatter:
        title = unquote_scalar(page.fields.get("title", "")).strip()
        if title:
            return title
    stem = relative.rsplit("/", 1)[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return stem or relative


def _parse_marked_blocks(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = [item.strip() for item in raw.split(",")]
    return unique_ids([item.lower() for item in parts if item])


def scan_clip_marks(body: str) -> tuple[ClipMark, ...]:
    """
    Every ``na-clip`` marker in *body*, in the order they appear.

    A marker owns the heading directly below it: the next line with content has
    to be an ``h2``–``h6`` heading. Blank lines may sit between the two; nothing
    else may. A marker whose next such line is prose, a list, another marker, a
    comment or the page title owns nothing, and comes back with ``heading=None``
    — reading on to a later heading would credit that section to material it was
    not written from.
    """
    marks: list[ClipMark] = []
    lines = split_lines(body)
    index = 0
    while index < len(lines):
        match = CLIP_MARK_LINE.fullmatch(lines[index])
        index += 1
        if match is None:
            continue
        collection_id = (match.group(1) or "").strip()
        if not collection_id:
            continue
        heading: str | None = None
        below = next_content_line(lines, index)
        if below is not None:
            heading = heading_text(lines[below])
        marks.append(
            ClipMark(
                collection_id=collection_id,
                block_ids=_parse_marked_blocks(match.group(2)),
                heading=heading,
            )
        )
    return tuple(marks)


def parse_clip_sections(body: str) -> list[ClipSection]:
    """The markers that own a heading, as the docs page binds them."""
    sections: list[ClipSection] = []
    for mark in scan_clip_marks(body):
        if mark.heading is None:
            continue
        sections.append(
            ClipSection(
                heading=mark.heading,
                collection_id=mark.collection_id,
                block_ids=mark.block_ids,
            )
        )
    return sections


def _locators_by_source(locators: list[str]) -> dict[str, list[str]]:
    by_source: dict[str, list[str]] = {}
    for locator in locators:
        raw_path, block_id = split_source_locator(locator)
        source = raw_source_path(raw_path)
        if source is None:
            continue
        ids = by_source.setdefault(source, [])
        if block_id and block_id not in ids:
            ids.append(block_id)
    return by_source


def _block_sort_key(block_ids: tuple[str, ...]) -> tuple[int, int, str]:
    if not block_ids:
        return (1, 0, "")
    first = block_ids[0]
    match = _BLOCK_INDEX.match(first)
    if match:
        return (0, int(match.group(1)), first)
    return (0, 0, first)


def compiled_clip_collections(layout: ProjectLayout) -> tuple[ClipCollection, ...]:
    """Collections written into wiki/sources clip summaries (survive a new clip session)."""
    wiki = layout.wiki
    folder = wiki / "sources"
    if not folder.is_dir():
        return ()
    found: dict[str, ClipCollection] = {}
    for path in sorted(
        folder.glob("*-clips.md"), key=lambda item: item.as_posix().casefold()
    ):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for collection in parse_clip_summary_collections(text):
            found[collection.id] = collection
    return tuple(found.values())


def review_clip_collections(layout: ProjectLayout) -> tuple[ClipCollection, ...]:
    """Wiki clip-summary catalog plus this round's raw collections.

    ``clips.json`` is the current session. Preview keys never replace a raw
    catalog row, and are omitted so docs / remainder scans survive a new round.
    """
    found: dict[str, ClipCollection] = {
        item.id: item for item in compiled_clip_collections(layout)
    }
    try:
        session = load_clip_store(layout).collections
    except (OSError, ValueError):
        session = ()
    for item in session:
        if raw_source_path(item.source_key) is None:
            continue
        found[item.id] = item
    return tuple(found.values())


def _mark_id(
    path: str,
    collection_id: str,
    heading: str,
    block_ids: tuple[str, ...],
    seen: set[str],
) -> str:
    base = f"{path}::{collection_id}::{heading}::{','.join(block_ids)}"
    ident = base
    n = 2
    while ident in seen:
        ident = f"{base}::{n}"
        n += 1
    seen.add(ident)
    return ident


def _append_mark(
    bucket: dict[str, list[CitationMark]],
    *,
    source: str,
    path: str,
    title: str,
    heading: str,
    collection_id: str,
    block_ids: tuple[str, ...],
    seen_ids: set[str],
) -> None:
    ident = _mark_id(path, collection_id, heading, block_ids, seen_ids)
    bucket.setdefault(source, []).append(
        CitationMark(
            id=ident,
            path=path,
            title=title,
            heading=heading,
            collection_id=collection_id,
            block_ids=block_ids,
        )
    )


def _marks_for_page(
    relative: str,
    title: str,
    body: str,
    locators: list[str],
    collections: tuple[ClipCollection, ...],
) -> dict[str, list[CitationMark]]:
    by_source = _locators_by_source(locators)
    if not by_source:
        return {}

    by_id = {item.id: item for item in collections}
    used_collections: set[str] = set()
    claimed: dict[str, set[str]] = {source: set() for source in by_source}
    out: dict[str, list[CitationMark]] = {}
    seen_ids: set[str] = set()

    for section in parse_clip_sections(body):
        collection = by_id.get(section.collection_id)
        source: str | None = None
        ids: tuple[str, ...] = ()
        if collection is not None:
            source = raw_source_path(collection.source_key)
            raw_ids = section.block_ids or collection.block_ids
            ids = unique_ids(raw_ids)
            used_collections.add(collection.id)
        elif section.block_ids:
            ids = section.block_ids
            hits = [
                path
                for path, page_ids in by_source.items()
                if page_ids and all(item in page_ids for item in ids)
            ]
            source = hits[0] if len(hits) == 1 else None
        if source is None or source not in by_source:
            continue
        _append_mark(
            out,
            source=source,
            path=relative,
            title=title,
            heading=section.heading,
            collection_id=section.collection_id,
            block_ids=ids,
            seen_ids=seen_ids,
        )
        claimed[source].update(ids)

    for collection in collections:
        if collection.id in used_collections:
            continue
        source = raw_source_path(collection.source_key)
        if source is None or source not in by_source:
            continue
        page_ids = by_source[source]
        if page_ids:
            overlap = tuple(
                item for item in collection.block_ids if item in page_ids
            )
            if not overlap:
                continue
            ids = overlap
        else:
            ids = collection.block_ids
        _append_mark(
            out,
            source=source,
            path=relative,
            title=title,
            heading="",
            collection_id=collection.id,
            block_ids=ids,
            seen_ids=seen_ids,
        )
        claimed[source].update(ids)

    for source, page_ids in by_source.items():
        leftover = tuple(item for item in page_ids if item not in claimed[source])
        if leftover:
            _append_mark(
                out,
                source=source,
                path=relative,
                title=title,
                heading="",
                collection_id="",
                block_ids=leftover,
                seen_ids=seen_ids,
            )
            continue
        if source in out:
            continue
        _append_mark(
            out,
            source=source,
            path=relative,
            title=title,
            heading="",
            collection_id="",
            block_ids=(),
            seen_ids=seen_ids,
        )
    return out


def list_source_citations(layout: ProjectLayout) -> SourceCitationIndex:
    """
    Map each cited ``raw/...`` file to unique notes and per-citation marks.

    List / badge counts unique wiki paths. Marks are one per ``na-clip`` section
    (or clip collection / leftover locators) so adjacent quotes stay separate.
    """
    wiki = layout.wiki
    if not wiki.is_dir():
        return SourceCitationIndex(notes={}, marks={})

    collections = review_clip_collections(layout)
    marks_by_source: dict[str, list[CitationMark]] = {}
    for path in sorted(
        wiki.rglob("*.md"), key=lambda item: item.as_posix().casefold()
    ):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(layout.root).as_posix()
        except ValueError:
            continue
        if not is_citing_wiki_page(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        locators = page_sources(text)
        if not locators:
            continue
        parsed = parse_page(text)
        title = _page_title(relative, parsed)
        page_marks = _marks_for_page(
            relative,
            title,
            parsed.body,
            locators,
            collections,
        )
        for source, marks in page_marks.items():
            marks_by_source.setdefault(source, []).extend(marks)

    notes: dict[str, list[CitingNote]] = {}
    ordered_marks: dict[str, list[CitationMark]] = {}
    for source, marks in marks_by_source.items():
        ordered = sorted(
            marks,
            key=lambda item: (
                _block_sort_key(item.block_ids),
                item.heading.casefold(),
                item.path,
                item.id,
            ),
        )
        ordered_marks[source] = ordered
        seen: dict[str, CitingNote] = {}
        for mark in ordered:
            if mark.path not in seen:
                seen[mark.path] = CitingNote(path=mark.path, title=mark.title)
        notes[source] = sorted(
            seen.values(),
            key=lambda item: (item.title.casefold(), item.path),
        )
    return SourceCitationIndex(notes=notes, marks=ordered_marks)


def source_citations_payload(layout: ProjectLayout) -> dict[str, object]:
    """JSON-ready map for the docs page."""
    index = list_source_citations(layout)
    sources: dict[str, dict[str, object]] = {}
    for path, notes in index.notes.items():
        marks = index.marks.get(path, [])
        sources[path] = {
            "notes": [{"path": note.path, "title": note.title} for note in notes],
            "marks": [
                {
                    "id": mark.id,
                    "path": mark.path,
                    "title": mark.title,
                    "heading": mark.heading,
                    "collectionId": mark.collection_id,
                    "blockIds": list(mark.block_ids),
                }
                for mark in marks
            ],
        }
    collections = []
    for item in review_clip_collections(layout):
        source = raw_source_path(item.source_key)
        if source is None:
            continue
        collections.append(
            {
                "id": item.id,
                "sourceKey": source,
                "sourceLabel": item.source_label,
                "blockIds": list(item.block_ids),
            }
        )
    return {"sources": sources, "collections": collections}
