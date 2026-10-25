"""Persistent revision inbox for source-change notices.

Serve-side scan (via fingerprints) writes `.noteagent/revision-inbox.json`.
This module never writes wiki, never calls ingest, and never starts an LLM.
Restored pending rows stay pending until a human ignores, acks, or compiles.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noteagent.ingest.fingerprint import (
    KIND_RAW_DELETED,
    STALE_KINDS,
    StaleItem,
    adopt_cited_sources,
    scan_stale,
)
from noteagent.project.layout import ProjectLayout
from noteagent.util.run_log import RunLogger

INBOX_VERSION = 1
DEBOUNCE_S = 0.4
STATUS_OPEN = "open"
STATUS_IGNORED = "ignored"
STATUS_ACKNOWLEDGED = "acknowledged"

_STATUSES = frozenset({STATUS_OPEN, STATUS_IGNORED, STATUS_ACKNOWLEDGED})
_KINDS = frozenset(STALE_KINDS)

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_last_sync: dict[str, tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True, slots=True)
class InboxItem:
    """One source-change notice. Not a compile job."""

    identity: str
    kind: str
    token: str
    status: str
    files_written: tuple[str, ...]
    locators: tuple[str, ...]
    updated_at: str


def inbox_file_path(layout: ProjectLayout) -> Path:
    return layout.revision_inbox


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


def _now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(value: str) -> str:
    return value.replace("\\", "/").strip()


def _item_key(identity: str, kind: str) -> tuple[str, str]:
    return (_norm(identity), kind.strip())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.replace("\\", "/").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return tuple(out)


def _load_items(layout: ProjectLayout) -> list[InboxItem]:
    path = inbox_file_path(layout)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    items: list[InboxItem] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _norm(str(row.get("identity") or ""))
        kind = str(row.get("kind") or "").strip()
        status = str(row.get("status") or "").strip()
        token = str(row.get("token") or "").strip()
        if not identity or kind not in _KINDS or status not in _STATUSES:
            continue
        key = (identity, kind)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            InboxItem(
                identity=identity,
                kind=kind,
                token=token,
                status=status,
                files_written=_strings(row.get("filesWritten")),
                locators=_strings(row.get("locators")),
                updated_at=str(row.get("updatedAt") or ""),
            )
        )
    return items


def _save_items(layout: ProjectLayout, items: list[InboxItem]) -> None:
    payload = {
        "version": INBOX_VERSION,
        "items": [_item_to_row(item) for item in items],
    }
    _atomic_write(
        inbox_file_path(layout),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def _item_to_row(item: InboxItem) -> dict[str, Any]:
    return {
        "identity": item.identity,
        "kind": item.kind,
        "token": item.token,
        "status": item.status,
        "filesWritten": list(item.files_written),
        "locators": list(item.locators),
        "updatedAt": item.updated_at,
    }


def _open_payload(items: list[InboxItem]) -> dict[str, Any]:
    open_items = [item for item in items if item.status == STATUS_OPEN]
    return {
        "openCount": len(open_items),
        "items": [_item_to_row(item) for item in open_items],
    }


def _cache_payload(layout: ProjectLayout, payload: dict[str, Any]) -> dict[str, Any]:
    _last_sync[_root_key(layout)] = (time.monotonic(), payload)
    return payload


def _from_scan(scan: StaleItem, previous: InboxItem | None) -> InboxItem:
    stamp = _now_stamp()
    if previous is None:
        return InboxItem(
            identity=scan.identity,
            kind=scan.kind,
            token=scan.token,
            status=STATUS_OPEN,
            files_written=scan.files_written,
            locators=scan.locators,
            updated_at=stamp,
        )
    keep_quiet = previous.token == scan.token and previous.status in {
        STATUS_IGNORED,
        STATUS_ACKNOWLEDGED,
    }
    if keep_quiet and previous.kind == KIND_RAW_DELETED:
        status = (
            STATUS_ACKNOWLEDGED
            if previous.status == STATUS_ACKNOWLEDGED
            else STATUS_IGNORED
        )
    elif keep_quiet and previous.status == STATUS_IGNORED:
        status = STATUS_IGNORED
    else:
        status = STATUS_OPEN
    changed = (
        previous.token != scan.token
        or previous.status != status
        or previous.files_written != scan.files_written
        or previous.locators != scan.locators
    )
    return InboxItem(
        identity=scan.identity,
        kind=scan.kind,
        token=scan.token,
        status=status,
        files_written=scan.files_written,
        locators=scan.locators,
        updated_at=stamp if changed else (previous.updated_at or stamp),
    )


def _sync_unlocked(layout: ProjectLayout) -> dict[str, Any]:
    # Adopt before comparing. A cited source with no fingerprint is not new, and
    # adopting it is what makes a later change to it visible. This writes, so it
    # lives here rather than in the scan; reading every note to see what cites
    # what is worth doing once per refresh.
    adopted = adopt_cited_sources(layout)
    if adopted:
        RunLogger.for_layout(layout).append(
            "fingerprints.adopted",
            sources=len(adopted),
            note=(
                "sources with no fingerprint that the notes already cite; taken as "
                "what the notes were built from, so they are not reported as new"
            ),
        )
    scan_items = scan_stale(layout)
    previous = {
        _item_key(item.identity, item.kind): item for item in _load_items(layout)
    }
    merged: list[InboxItem] = []
    for scan in scan_items:
        key = _item_key(scan.identity, scan.kind)
        merged.append(_from_scan(scan, previous.get(key)))
    kind_rank = {kind: index for index, kind in enumerate(STALE_KINDS)}
    merged.sort(
        key=lambda item: (
            item.identity,
            kind_rank.get(item.kind, 99),
            item.kind,
        )
    )
    _save_items(layout, merged)
    return _cache_payload(layout, _open_payload(merged))


def sync_revision_inbox(
    layout: ProjectLayout, *, force: bool = False
) -> dict[str, Any]:
    """Refresh the inbox from a stale scan. Does not write wiki."""
    key = _root_key(layout)
    if not force:
        cached = _last_sync.get(key)
        if cached and time.monotonic() - cached[0] < DEBOUNCE_S:
            return cached[1]
    with _lock_for(layout):
        if not force:
            cached = _last_sync.get(key)
            if cached and time.monotonic() - cached[0] < DEBOUNCE_S:
                return cached[1]
        return _sync_unlocked(layout)


def note_source_change(layout: ProjectLayout) -> None:
    """Best-effort inbox refresh after raw/clips/wiki-adjacent disk changes."""
    try:
        sync_revision_inbox(layout, force=True)
    except OSError:
        return


def poll_active_inbox() -> None:
    """Serve-side detector tick. Never starts ingest."""
    try:
        from noteagent.api.projects import get_active_layout
        from noteagent.project.config import ProjectNotConfiguredError
    except ImportError:
        return
    try:
        layout = get_active_layout()
    except (FileNotFoundError, OSError, ProjectNotConfiguredError):
        return
    try:
        sync_revision_inbox(layout)
    except OSError:
        return


def ignore_inbox_item(layout: ProjectLayout, identity: str, kind: str) -> dict[str, Any]:
    """Persist ignore until the fingerprint token changes."""
    wanted = _item_key(identity, kind)
    if wanted[1] not in _KINDS:
        raise KeyError(f"unknown inbox kind: {kind}")
    with _lock_for(layout):
        _sync_unlocked(layout)
        items = _load_items(layout)
        found = False
        next_items: list[InboxItem] = []
        stamp = _now_stamp()
        for item in items:
            if _item_key(item.identity, item.kind) != wanted:
                next_items.append(item)
                continue
            found = True
            status = (
                STATUS_ACKNOWLEDGED
                if item.kind == KIND_RAW_DELETED
                else STATUS_IGNORED
            )
            next_items.append(replace(item, status=status, updated_at=stamp))
        if not found:
            raise KeyError(f"inbox item not found: {identity} {kind}")
        _save_items(layout, next_items)
        return _cache_payload(layout, _open_payload(next_items))


def acknowledge_deleted(layout: ProjectLayout, identity: str) -> dict[str, Any]:
    """Human-only dismiss for a missing raw file. Does not delete wiki pages."""
    return ignore_inbox_item(layout, identity, KIND_RAW_DELETED)


def revision_inbox_payload(layout: ProjectLayout) -> dict[str, Any]:
    return sync_revision_inbox(layout)


__all__ = [
    "DEBOUNCE_S",
    "INBOX_VERSION",
    "InboxItem",
    "STATUS_ACKNOWLEDGED",
    "STATUS_IGNORED",
    "STATUS_OPEN",
    "acknowledge_deleted",
    "ignore_inbox_item",
    "inbox_file_path",
    "note_source_change",
    "poll_active_inbox",
    "revision_inbox_payload",
    "sync_revision_inbox",
]
