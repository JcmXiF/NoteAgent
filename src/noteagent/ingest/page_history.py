"""A page's own history: the versions its writers replaced.

A wiki page is compiled by the pipeline, and each write replaces bytes that are
then gone unless something kept them. This keeps the replaced version in the
project's own store, so a page as it stood can be read back and compared with
what it says now.

The store is content-addressed: one blob per distinct text, however many pages and
writes hold it, and an append-only log of the writes says which page held which
text, when, and as part of which write. Text is kept folded to LF — the shape a
write puts on disk — so a version read back is what a reader would have seen.

It lives with the app's own state (``.noteagent/``), never inside the wiki: what
it records is what the app's writers did, and a second copy of a page inside the
wiki would be a second page. Nothing here writes a page on its own; a writer keeps
the version it is about to replace as part of the write it is making.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from noteagent.ingest.compile_cache import text_digest
from noteagent.io.atomic import write_text_atomic
from noteagent.io.text_file import to_lf, write_text
from noteagent.project.layout import ProjectLayout

#: The part of the project a version belongs to. Only wiki pages are versioned.
AREA_WIKI = "wiki"

#: Who a write came from: the pipeline compiling a page.
WRITTEN_BY_AGENT = "agent"

#: How many writes of one page a reader is handed. The log keeps them all; this is
#: only what a page's history asks for.
HISTORY_LIMIT = 200

#: Versions of wiki pages are kept as markdown, so a person can open one directly.
BLOB_SUFFIX = ".md"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """One write to one page, with the text it replaced."""

    at: str
    area: str
    path: str
    by: str
    mode: str
    before: str
    after: str
    bytes_written: int


def _root_key(layout: ProjectLayout) -> str:
    return str(layout.root.resolve())


def _lock_for(layout: ProjectLayout) -> threading.Lock:
    key = _root_key(layout)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clean_path(relative_path: str) -> str:
    return relative_path.replace("\\", "/").lstrip("./").strip()


def blob_path(layout: ProjectLayout, digest: str) -> Path:
    """Where one text is kept. Raises ValueError for anything but a digest."""
    name = digest.strip().lower()
    if not _DIGEST_RE.fullmatch(name):
        raise ValueError(f"not a content digest: {digest!r}")
    return layout.snapshots / f"{name}{BLOB_SUFFIX}"


def blob_digest(text: str) -> str:
    """The name a text is kept under: its own bytes, folded to LF."""
    return text_digest(to_lf(text))


def keep_before(layout: ProjectLayout, text: str) -> str:
    """
    Put *text* in the store, and answer the name it is kept under.

    Called before a write, with the text the write is about to replace, so the
    version is on disk before the page is no longer it. The same text is only ever
    kept once: pages written again and again hold mostly the same bytes, and those
    bytes are one blob, not one per write.
    """
    digest = blob_digest(text)
    path = blob_path(layout, digest)
    if path.is_file():
        return digest
    layout.snapshots.mkdir(parents=True, exist_ok=True)
    # Written beside itself and moved into place, so a reader never opens a half
    # written version: a truncated blob would be a version that never existed.
    pending = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    write_text(pending, text)
    pending.replace(path)
    return digest


def _row(entry: SnapshotEntry) -> dict[str, object]:
    return {
        "at": entry.at,
        "area": entry.area,
        "path": entry.path,
        "by": entry.by,
        "mode": entry.mode,
        "before": entry.before,
        "after": entry.after,
        "bytes": entry.bytes_written,
    }


def _entry(raw: object) -> SnapshotEntry | None:
    if not isinstance(raw, dict):
        return None
    at = str(raw.get("at") or "").strip()
    path = str(raw.get("path") or "").strip()
    after = str(raw.get("after") or "").strip()
    if not at or not path or not after:
        return None
    size = raw.get("bytes")
    return SnapshotEntry(
        at=at,
        area=str(raw.get("area") or AREA_WIKI).strip(),
        path=path,
        by=str(raw.get("by") or "").strip(),
        mode=str(raw.get("mode") or "").strip(),
        before=str(raw.get("before") or "").strip(),
        after=after,
        bytes_written=size if isinstance(size, int) else 0,
    )


def _append(layout: ProjectLayout, entry: SnapshotEntry) -> None:
    path = layout.snapshot_log
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_row(entry), ensure_ascii=False) + "\n")


def _read_log(layout: ProjectLayout) -> list[SnapshotEntry]:
    path = layout.snapshot_log
    if not path.is_file():
        return []
    entries: list[SnapshotEntry] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    # A line that cannot be read is not a reason to lose the rest:
                    # the log is append-only, so one bad line stays where it is.
                    continue
                item = _entry(raw)
                if item is not None:
                    entries.append(item)
    except OSError:
        return []
    return entries


def record_write(
    layout: ProjectLayout,
    relative_path: str,
    *,
    before: str,
    after_text: str,
    mode: str,
    by: str,
    at: str | None = None,
) -> SnapshotEntry | None:
    """
    Note one write to a page, and keep the text it lands.

    ``before`` is the name the replaced text was kept under (from
    :func:`keep_before`), or nothing when the page did not exist yet. Answers with
    the entry, or with nothing when the write left the page's text as it was —
    a write that changed nothing is not a version of the page.
    """
    after = keep_before(layout, after_text)
    if before == after:
        return None
    entry = SnapshotEntry(
        at=at or _now(),
        area=AREA_WIKI,
        path=_clean_path(relative_path),
        by=by,
        mode=mode,
        before=before,
        after=after,
        bytes_written=len(to_lf(after_text).encode("utf-8")),
    )
    with _lock_for(layout):
        _append(layout, entry)
    return entry


def note_agent_write(
    layout: ProjectLayout,
    relative_path: str,
    *,
    before: str,
    after_text: str,
    mode: str,
) -> SnapshotEntry | None:
    """Note a write the pipeline made while compiling a page."""
    return record_write(
        layout,
        relative_path,
        before=before,
        after_text=after_text,
        mode=mode,
        by=WRITTEN_BY_AGENT,
    )


def page_snapshots(
    layout: ProjectLayout,
    relative_path: str,
    *,
    limit: int = HISTORY_LIMIT,
) -> tuple[SnapshotEntry, ...]:
    """The writes to one page, newest first."""
    path = _clean_path(relative_path)
    entries = [item for item in _read_log(layout) if item.path == path]
    entries.reverse()
    return tuple(entries[: max(0, limit)])


def rename_entries(layout: ProjectLayout, relative_path: str, new_path: str) -> int:
    """File a page's versions under the name the page has now.

    A renamed page is the same page: what it said before is still its own history,
    and leaving those entries under the old name would make them unreadable from
    the page that holds them. Answers with how many entries were refiled.
    """
    old = _clean_path(relative_path)
    new = _clean_path(new_path)
    if old == new:
        return 0
    path = layout.snapshot_log
    if not path.is_file():
        return 0
    changed = 0
    with _lock_for(layout):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0
        out: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            if not isinstance(raw, dict) or _clean_path(str(raw.get("path") or "")) != old:
                out.append(line)
                continue
            raw["path"] = new
            out.append(json.dumps(raw, ensure_ascii=False))
            changed += 1
        if changed:
            write_text_atomic(path, "\n".join(out) + "\n")
    return changed


__all__ = [
    "AREA_WIKI",
    "HISTORY_LIMIT",
    "SnapshotEntry",
    "WRITTEN_BY_AGENT",
    "blob_digest",
    "blob_path",
    "keep_before",
    "note_agent_write",
    "page_snapshots",
    "record_write",
    "rename_entries",
]
