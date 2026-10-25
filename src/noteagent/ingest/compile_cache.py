"""What each source compiled to, and whether a compile can be skipped.

``.noteagent/compile-cache.json`` holds one record per raw source:

- ``digest``: SHA-256 of the file's bytes, as the compile found them.
- ``recorded_at``: when the row was last written.
- ``wrote``: the files that compile produced.
- ``clips`` / ``locators`` / ``pages``: what the gated compile saw, so a later
  scan can say which of those changed.

A row is evidence, not a promise: a lookup only skips work when the digest still
matches *and* every file the row says was written is still on disk. Deleting the
file is always safe — the next compile just writes every row again.

The whole file is rewritten on each record. That is fine at the size a wiki
reaches (one row per source) and keeps the format trivially inspectable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noteagent.io.atomic import write_text_atomic
from noteagent.project.layout import ProjectLayout

#: Bumped when a row's shape changes in a way an older row cannot answer.
FORMAT = 2

#: Identities that stand for a clip session rather than a file. They never get a
#: row: there is no file to watch, and no bytes to hash.
SESSION_IDENTITIES = frozenset({"raw/clip-session", "clip-session"})


@dataclass(frozen=True, slots=True)
class CompileRecord:
    """One source's last successful compile."""

    digest: str
    recorded_at: str
    wrote: tuple[str, ...] = ()
    #: Fingerprint of the clip ids selected from this source.
    clips: str = ""
    #: ``locator -> digest of the passage text`` at the time of the compile.
    locators: tuple[tuple[str, str], ...] = ()
    #: ``page path -> digest of its body`` at the time of the compile.
    pages: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class LookupAnswer:
    """Whether a source may be skipped, and why it may not."""

    hit: bool
    why: str
    record: CompileRecord | None = None


def text_digest(text: str) -> str:
    """SHA-256 of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    """SHA-256 of a file's raw bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pairs(raw: object) -> tuple[tuple[str, str], ...]:
    """Read a JSON object of path → digest, sorted and path-normalised."""
    if not isinstance(raw, dict):
        return ()
    out: list[tuple[str, str]] = []
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        path = key.replace("\\", "/").strip()
        digest = value.strip()
        if path and digest:
            out.append((path, digest))
    return tuple(sorted(out))


def read_records(layout: ProjectLayout) -> dict[str, CompileRecord]:
    """Every usable row. A missing, unreadable or half-written file reads empty."""
    path = layout.compile_cache
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    # A file written by a newer build may key its rows differently; guessing
    # would skip an ingest that should have run, so it reads as empty instead.
    version = raw.get("format")
    if isinstance(version, int) and version > FORMAT:
        return {}
    sources = raw.get("sources")
    if not isinstance(sources, dict):
        return {}

    records: dict[str, CompileRecord] = {}
    for identity, row in sources.items():
        if not isinstance(identity, str) or not isinstance(row, dict):
            continue
        digest = row.get("digest")
        recorded_at = row.get("recorded_at")
        wrote = row.get("wrote")
        if not isinstance(digest, str) or not isinstance(recorded_at, str):
            continue
        if not isinstance(wrote, list) or not all(isinstance(item, str) for item in wrote):
            continue
        clips = row.get("clips")
        records[identity] = CompileRecord(
            digest=digest,
            recorded_at=recorded_at,
            wrote=tuple(item.replace("\\", "/") for item in wrote),
            clips=clips if isinstance(clips, str) else "",
            locators=_pairs(row.get("locators")),
            pages=_pairs(row.get("pages")),
        )
    return records


def write_records(layout: ProjectLayout, records: dict[str, CompileRecord]) -> None:
    """Replace the file with *records*, one key per identity."""
    layout.meta.mkdir(parents=True, exist_ok=True)
    rows: dict[str, Any] = {}
    for identity, record in sorted(records.items()):
        row: dict[str, Any] = {
            "digest": record.digest,
            "recorded_at": record.recorded_at,
            "wrote": list(record.wrote),
        }
        if record.clips:
            row["clips"] = record.clips
        if record.locators:
            row["locators"] = dict(record.locators)
        if record.pages:
            row["pages"] = dict(record.pages)
        rows[identity] = row
    payload: dict[str, Any] = {"format": FORMAT, "sources": rows}
    write_text_atomic(
        layout.compile_cache,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def outputs_present(layout: ProjectLayout, wrote: tuple[str, ...]) -> bool:
    """Whether every file a record says was written is still there."""
    for relative in wrote:
        if not (layout.root / relative.replace("\\", "/")).resolve().is_file():
            return False
    return True


def lookup(
    layout: ProjectLayout,
    identity: str,
    text: str,
    *,
    digest: str | None = None,
) -> LookupAnswer:
    """Ask whether *identity* can be skipped, given its current text or digest.

    Both halves have to hold: the bytes are the ones that were compiled, and the
    compile's outputs are still on disk. A row with no outputs recorded cannot
    prove anything was written, so it does not skip either.
    """
    expected = digest or text_digest(text)
    record = read_records(layout).get(identity)
    if record is None:
        return LookupAnswer(hit=False, why="no record for this source")
    if record.digest != expected:
        return LookupAnswer(hit=False, why="the bytes changed", record=record)
    if not record.wrote:
        return LookupAnswer(hit=False, why="the record names no output", record=record)
    if not outputs_present(layout, record.wrote):
        return LookupAnswer(hit=False, why="a recorded output is gone", record=record)
    return LookupAnswer(hit=True, why="compiled from these bytes", record=record)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _both_paths(
    before: tuple[str, ...], after: list[str] | tuple[str, ...]
) -> tuple[str, ...]:
    """Union two path lists, normalised, first spelling kept."""
    seen: set[str] = set()
    out: list[str] = []
    for item in (*before, *after):
        path = item.replace("\\", "/").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(path)
    return tuple(out)


def record(
    layout: ProjectLayout,
    identity: str,
    *,
    digest: str,
    wrote: list[str] | tuple[str, ...],
    clips: str = "",
    locators: dict[str, str] | None = None,
    pages: dict[str, str] | None = None,
    union: bool = False,
) -> CompileRecord:
    """Write one source's row.

    ``union`` keeps what a previous row already knew and adds to it, which is what
    a gated compile needs: it writes some of the outputs, not all of them. A plain
    ingest overwrites, because it is the whole story for that source.
    """
    key = identity.replace("\\", "/").strip()
    if not key:
        raise ValueError("refusing an empty source identity")
    if key in SESSION_IDENTITIES:
        raise ValueError(f"refusing a clip-session identity: {identity!r}")

    records = read_records(layout)
    previous = records.get(key) if union else None
    locator_map = dict(previous.locators) if previous is not None else {}
    locator_map.update(
        {
            path.replace("\\", "/"): value
            for path, value in (locators or {}).items()
            if path.replace("\\", "/").strip() and value
        }
    )
    page_map = dict(previous.pages) if previous is not None else {}
    page_map.update(
        {
            path.replace("\\", "/"): value
            for path, value in (pages or {}).items()
            if path.replace("\\", "/").strip() and value
        }
    )
    entry = CompileRecord(
        digest=digest,
        recorded_at=_now(),
        wrote=_both_paths(previous.wrote if previous is not None else (), wrote),
        clips=clips or (previous.clips if previous is not None else ""),
        locators=tuple(sorted(locator_map.items())),
        pages=tuple(sorted(page_map.items())),
    )
    records[key] = entry
    write_records(layout, records)
    return entry


def record_ingest(
    layout: ProjectLayout,
    identity: str,
    content: str,
    wrote: list[str] | tuple[str, ...],
    *,
    digest: str | None = None,
) -> CompileRecord:
    """Write the row for a source that was just compiled end to end."""
    return record(
        layout,
        identity,
        digest=digest or text_digest(content),
        wrote=wrote,
        union=False,
    )
