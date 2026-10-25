"""Source-change fingerprints and a read-only stale scan.

Records the SHA of a raw file's bytes, plus the clip selection, locator texts and
page bodies a successful gated compile produced. Scanning lists overdue kinds; it
does not write the wiki and does not start an ingest.
"""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.acquire.adapters.pdf_source import (
    blocks_from_payload,
    load_derived_payload,
)
from noteagent.acquire.adapters.markdown_source import markdown_blocks
from noteagent.acquire.derive import extract_derived_blocks
from noteagent.ingest.clips import (
    ClipCollection,
    durable_source_file,
    load_clip_store,
    source_locator,
)
from noteagent.ingest.frontmatter import parse_page
from noteagent.ingest.compile_cache import (
    SESSION_IDENTITIES,
    CompileRecord,
    file_digest,
    read_records,
    record,
    text_digest,
)
from noteagent.ingest.page_merge import page_sources
from noteagent.ingest.source_citations import (
    is_citing_wiki_page,
    list_source_citations,
    split_source_locator,
)
from noteagent.project.layout import (
    RAW_DIR,
    WIKI_INDEX,
    WIKI_LOG,
    WIKI_OVERVIEW,
    ProjectLayout,
)
from noteagent.project.raw_kinds import WEB_KIND, raw_kind

KIND_RAW_MODIFIED = "raw_modified"
KIND_RAW_CREATED = "raw_created"
KIND_RAW_DELETED = "raw_deleted"
KIND_CLIPS_CHANGED = "clips_changed"
KIND_LOCATOR_BROKEN = "locator_broken"
KIND_OUTPUT_MISSING = "output_missing"

STALE_KINDS = (
    KIND_RAW_DELETED,
    KIND_LOCATOR_BROKEN,
    KIND_OUTPUT_MISSING,
    KIND_CLIPS_CHANGED,
    KIND_RAW_MODIFIED,
    KIND_RAW_CREATED,
)

_AGGREGATE_WIKI = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})
_HTML_SUFFIXES = frozenset({".html", ".htm"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})


@dataclass(frozen=True, slots=True)
class StaleItem:
    """One overdue source identity (scan payload; not a write job)."""

    identity: str
    kind: str
    files_written: tuple[str, ...]
    locators: tuple[str, ...] = ()
    token: str = ""


def clips_fingerprint(
    identity: str,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> str:
    """Hash of selected block ids for one raw file (selection set, not texts)."""
    key = identity.replace("\\", "/").strip()
    ids: list[str] = []
    seen: set[str] = set()
    for collection in collections:
        source = collection.source_key.replace("\\", "/").strip()
        if source != key:
            continue
        for block_id in collection.block_ids:
            if block_id in seen:
                continue
            seen.add(block_id)
            ids.append(block_id)
    if not ids:
        return ""
    return text_digest("\n".join(sorted(ids)))


def list_raw_identities(layout: ProjectLayout) -> list[str]:
    """Project-relative paths under ``raw/``, skipping dotfiles and ``.cache``."""
    root = layout.raw
    if not root.is_dir():
        return []
    project = layout.root.expanduser().resolve()
    out: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(project)
        except ValueError:
            continue
        parts = relative.parts
        if any(part.startswith(".") for part in parts):
            continue
        posix = relative.as_posix()
        if not posix.startswith(f"{RAW_DIR}/"):
            continue
        out.append(posix)
    return sorted(out)


def _clip_block_text(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
    locator: str,
) -> str | None:
    path, block_id = split_source_locator(locator)
    if not path or not block_id:
        return None
    for collection in collections:
        if collection.source_key.replace("\\", "/").strip() != path:
            continue
        if block_id not in collection.block_ids:
            continue
        for block in collection.blocks:
            if block.id == block_id:
                return block.text
    return None


def derived_block_texts(layout: ProjectLayout, identity: str) -> dict[str, str] | None:
    """
    Current ``block_id → text`` if this source can be enumerated.

    Sidecar wins. HTML without a sidecar is extracted. ``None`` means we cannot
    tell whether an id is gone (do not emit ``locator_broken``).
    """
    key = identity.replace("\\", "/").strip()
    sidecar = load_derived_payload(layout, key)
    blocks = blocks_from_payload(sidecar)
    if blocks is not None:
        return {block.id: block.text for block in blocks}

    path = durable_source_file(layout, key)
    if path is None:
        return None
    suffix = path.suffix.lower()
    web = raw_kind(key) == WEB_KIND
    if suffix in _MARKDOWN_SUFFIXES:
        # A markdown original is enumerated by its own adapter: the same ids the
        # source reader offers for selection, so a citation into it can be checked.
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        blocks = markdown_blocks(text)
        return {block.id: block.text for block in blocks} if blocks else None
    if suffix not in _HTML_SUFFIXES and not web:
        return None
    try:
        html = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    extracted = extract_derived_blocks(html)
    if not extracted:
        return None
    return {block.id: block.text for block in extracted}


def _union_unique(*groups: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for item in group:
            path = item.replace("\\", "/").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            out.append(path)
    return out


def _note_paths(files_written: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(
        path.replace("\\", "/")
        for path in files_written
        if path.replace("\\", "/") not in _AGGREGATE_WIKI
    )


def record_confirmed_fingerprints(
    layout: ProjectLayout,
    *,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
    written_paths: list[str] | tuple[str, ...],
) -> list[str]:
    """
    Update cache rows for every durable raw identity touched this compile.

    Does not skip a later gated compile: humans already confirmed. Returns the
    identities that were recorded.
    """
    locators_by_source: dict[str, dict[str, str]] = {}
    pages_by_source: dict[str, dict[str, str]] = {}
    files_by_source: dict[str, list[str]] = {}

    for relative in written_paths:
        path = relative.replace("\\", "/").strip()
        if not path or path in _AGGREGATE_WIKI:
            continue
        absolute = layout.root / path
        if not absolute.is_file():
            continue
        try:
            text = absolute.read_text(encoding="utf-8")
        except OSError:
            continue
        body_hash = text_digest(parse_page(text).body)
        locators = page_sources(text) if is_citing_wiki_page(path) else []
        if not locators:
            continue
        for locator in locators:
            source, block_id = split_source_locator(locator)
            if (
                not source.startswith(f"{RAW_DIR}/")
                or source in SESSION_IDENTITIES
            ):
                continue
            block_text = _clip_block_text(collections, locator)
            if block_text is None and block_id:
                derived = derived_block_texts(layout, source) or {}
                block_text = derived.get(block_id)
            digest = text_digest(block_text or "")
            locators_by_source.setdefault(source, {})[locator] = digest
            files_by_source.setdefault(source, []).append(path)
            pages_by_source.setdefault(source, {})[path] = body_hash

    for collection in collections:
        source = collection.source_key.replace("\\", "/").strip()
        if not source.startswith(f"{RAW_DIR}/"):
            continue
        if source in SESSION_IDENTITIES:
            continue
        if durable_source_file(layout, source) is None:
            continue
        files_by_source.setdefault(source, [])
        locators_by_source.setdefault(source, {})
        pages_by_source.setdefault(source, {})
        for block_id in collection.block_ids:
            locator = source_locator(source, block_id)
            if locator is None or locator in locators_by_source[source]:
                continue
            block_text = _clip_block_text(collections, locator) or ""
            locators_by_source[source][locator] = text_digest(block_text)

    summaries = [
        path.replace("\\", "/")
        for path in written_paths
        if path.replace("\\", "/").startswith("wiki/sources/")
    ]
    recorded: list[str] = []
    for source in sorted(files_by_source):
        absolute = durable_source_file(layout, source)
        if absolute is None:
            continue
        files = _union_unique(files_by_source[source], summaries)
        record(
            layout,
            source,
            digest=file_digest(absolute),
            wrote=files,
            clips=clips_fingerprint(source, collections),
            locators=locators_by_source.get(source) or None,
            pages=pages_by_source.get(source) or None,
            union=True,
        )
        recorded.append(source)
    return recorded


def adopt_cited_sources(layout: ProjectLayout) -> tuple[str, ...]:
    """Record a fingerprint for sources the notes already cite.

    A source with no cache row would otherwise be scanned as new, even when
    several notes cite it. Those citations are the evidence that it was written
    from, so the file as it stands now is recorded as what it was built from.

    The assumption is one-sided by design: a file that changed before this ran is
    taken as it is, because nothing recorded what it looked like then and a false
    "changed" is worse than a missed one. From this point on the file is watched
    like any other source, so a later change is caught.

    Answers with the identities it adopted, so a caller can say so in the log.
    """
    entries = read_records(layout)
    missing = [
        identity
        for identity in list_raw_identities(layout)
        if identity not in entries and identity not in SESSION_IDENTITIES
    ]
    if not missing:
        return ()

    cited = {
        key.replace("\\", "/").strip(): notes
        for key, notes in list_source_citations(layout).notes.items()
    }
    adopted: list[str] = []
    for identity in missing:
        notes = cited.get(identity.replace("\\", "/").strip())
        if not notes:
            # Nothing in the project says this was ever written from, so it is new.
            continue
        absolute = durable_source_file(layout, identity)
        if absolute is None:
            continue
        record(
            layout,
            identity,
            digest=file_digest(absolute),
            # The pages that cite it are what it was built into. Without them the
            # record would say nothing about where it went, and a page deleted later
            # would go unnoticed.
            wrote=[note.path for note in notes],
            union=True,
        )
        adopted.append(identity)
    return tuple(adopted)


def _current_fingerprints(
    layout: ProjectLayout,
    identity: str,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> tuple[str, str]:
    absolute = durable_source_file(layout, identity)
    digest = file_digest(absolute) if absolute is not None else ""
    return digest, clips_fingerprint(identity, collections)


def _stale_item(
    identity: str,
    kind: str,
    entry: CompileRecord | None,
    *,
    locators: tuple[str, ...] = (),
    digest: str = "",
    clips: str = "",
) -> StaleItem:
    files = _note_paths(entry.wrote) if entry is not None else ()
    token = text_digest(
        "\n".join(
            (
                kind,
                identity,
                digest,
                clips,
                ",".join(locators),
                ",".join(files),
            )
        )
    )
    return StaleItem(
        identity=identity,
        kind=kind,
        files_written=files,
        locators=locators,
        token=token,
    )


def scan_stale(layout: ProjectLayout) -> list[StaleItem]:
    """
    Compare disk + clips against recorded fingerprints. No watcher, no writes.
    """
    entries = read_records(layout)
    on_disk = set(list_raw_identities(layout))
    collections = load_clip_store(layout).collections
    items: list[StaleItem] = []

    for identity, entry in sorted(entries.items()):
        key = identity.replace("\\", "/").strip()
        if key in SESSION_IDENTITIES:
            continue
        now_digest, now_clips = _current_fingerprints(layout, key, collections)
        absolute = durable_source_file(layout, key)
        if key not in on_disk or absolute is None:
            items.append(
                _stale_item(
                    key,
                    KIND_RAW_DELETED,
                    entry,
                    digest=now_digest,
                    clips=now_clips,
                )
            )
            continue
        if file_digest(absolute) != entry.digest:
            items.append(
                _stale_item(
                    key,
                    KIND_RAW_MODIFIED,
                    entry,
                    digest=now_digest,
                    clips=now_clips,
                )
            )

        note_files = _note_paths(entry.wrote)
        missing = tuple(
            path
            for path in note_files
            if not (layout.root / path).is_file()
        )
        if missing:
            items.append(
                _stale_item(
                    key,
                    KIND_OUTPUT_MISSING,
                    entry,
                    locators=missing,
                    digest=now_digest,
                    clips=now_clips,
                )
            )

        if entry.clips or entry.locators:
            if now_clips != entry.clips:
                items.append(
                    _stale_item(
                        key,
                        KIND_CLIPS_CHANGED,
                        entry,
                        digest=now_digest,
                        clips=now_clips,
                    )
                )

        recorded_locs = [locator for locator, _digest in entry.locators]
        derived = derived_block_texts(layout, key)
        if derived is not None and recorded_locs:
            broken = tuple(
                locator
                for locator in recorded_locs
                if (split_source_locator(locator)[1] or "") not in derived
            )
            if broken:
                items.append(
                    _stale_item(
                        key,
                        KIND_LOCATOR_BROKEN,
                        entry,
                        locators=broken,
                        digest=now_digest,
                        clips=now_clips,
                    )
                )

    for identity in sorted(on_disk):
        if identity in entries or identity in SESSION_IDENTITIES:
            continue
        now_digest, now_clips = _current_fingerprints(
            layout, identity, collections
        )
        items.append(
            _stale_item(
                identity,
                KIND_RAW_CREATED,
                None,
                digest=now_digest,
                clips=now_clips,
            )
        )

    kind_rank = {kind: index for index, kind in enumerate(STALE_KINDS)}
    items.sort(key=lambda item: (item.identity, kind_rank.get(item.kind, 99), item.kind))
    return items


def durable_identities_from_collections(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> list[str]:
    """Stable raw identities used in this clip session (never clip-session)."""
    out: list[str] = []
    seen: set[str] = set()
    for collection in collections:
        key = collection.source_key.replace("\\", "/").strip()
        if (
            not key.startswith(f"{RAW_DIR}/")
            or key in SESSION_IDENTITIES
            or key in seen
        ):
            continue
        seen.add(key)
        out.append(key)
    return out
