"""Sessions as append-only JSONL under ``.noteagent/sessions/``.

A session is one file: a header line, then one committed entry per line. Appending
is the only write, so a crash can lose the line being written but never rewrites
what came before it. A trailing line without its newline is the one a crash left
behind; it is dropped when reading and repaired before the next append.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds, iso_timestamp
from noteagent.agent.store import (
    SessionSnapshot,
    SessionSummary,
    last_timestamp,
    summary_dict,
)
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.common import (
    RESERVED_SESSION_IDS,
    SESSION_ID_RE,
    SESSION_LOCK,
    session_line,
)
from noteagent.sessions.legacy import MigrationReport, migrate_legacy_chats

logger = logging.getLogger(__name__)

SESSION_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})

#: The derived list cache. It is never the truth: a missing, corrupt or stale
#: index is rebuilt from the JSONL files.
INDEX_VERSION = 1
INDEX_NAME = "index.json"

#: A name taken from the first question is a row in a list, so it is clipped.
TITLE_CHARS = 42

#: The entry types the schema knows. An unknown one is kept and warned about,
#: because a newer writer's line must not make an older reader lose the session.
_KNOWN_TYPES = frozenset(
    {
        "compaction",
        "context_edit",
        "message",
        "model_change",
        "reset",
        "resume_started",
        "session",
        "session_info",
        "system_section",
        "thinking_level_change",
        "usage",
        "wait_cancelled",
        "wait_completed",
        "wait_entered",
        "wait_proposed",
    }
)

class SessionFormatError(ValueError):
    """The bytes cannot be read as a session."""


class JsonlSessionStore:
    """One project's sessions, as the kernel's ``SessionStore``."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        clock: Clock | None = None,
        ids: Ids | None = None,
    ) -> None:
        self._layout = layout
        self._clock = clock or SystemClock()
        self._ids = ids or UuidIds()
        #: The one migration pass, so a route that asks for its report and then
        #: lists does not run the marker check twice.
        self._migration: MigrationReport | None = None

    def create(self, session_id: str, title: str = "") -> SessionSnapshot:
        ident = _require_id(session_id)
        path = self._layout.session_path(ident)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "v": SESSION_VERSION,
            "type": "session",
            "id": ident,
            "createdAt": self._now(),
        }
        with SESSION_LOCK:
            try:
                with path.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(session_line(header))
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                raise FileExistsError(f"session already exists: {ident}") from None
        if title.strip():
            self.rename(ident, title)
        else:
            self._refresh_index()
        return self.load(ident)

    def load(self, session_id: str) -> SessionSnapshot:
        ident = _require_id(session_id)
        path = self._layout.session_path(ident)
        if not path.is_file():
            raise FileNotFoundError(f"session not found: {ident}")
        records = _read_records(path)
        header = _checked_header(records, ident)
        entries = records[1:]
        for entry in entries:
            _checked_entry(entry, ident)
        title = _title(header, entries)
        return SessionSnapshot(ident, title, tuple(entries), header["v"])

    def append(self, session_id: str, entry: Mapping[str, object]) -> None:
        ident = _require_id(session_id)
        path = self._layout.session_path(ident)
        if not path.is_file():
            raise FileNotFoundError(f"session not found: {ident}")
        with SESSION_LOCK:
            _repair_tail(path)
            records = _read_records(path)
            header = _checked_header(records, ident)
            entries = records[1:]
            for item in entries:
                _checked_entry(item, ident)
            parent = entries[-1].get("id") if entries else header["id"]
            payload = dict(entry)
            payload.setdefault("v", SESSION_VERSION)
            if not isinstance(payload.get("id"), str) or not payload["id"]:
                payload["id"] = self._ids.new_id("e")
            if not payload.get("parentId"):
                payload["parentId"] = parent
            if not isinstance(payload.get("ts"), str) or not payload["ts"]:
                payload["ts"] = self._now()
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(session_line(payload))
                handle.flush()
                os.fsync(handle.fileno())
            self._refresh_index()

    def list(self) -> tuple[SessionSummary, ...]:
        if self._migration is None:
            self.ensure_migrated()
        paths = self._session_paths()
        if not paths:
            return ()
        indexed = self._read_index()
        if indexed is not None and {item.id for item in indexed} == set(paths):
            return tuple(
                sorted(indexed, key=lambda item: item.updated_at, reverse=True)
            )
        items = self._scan(paths)
        self._write_index(items)
        return items

    def _session_paths(self) -> dict[str, Path]:
        directory = self._layout.sessions_dir
        if not directory.is_dir():
            return {}
        paths: dict[str, Path] = {}
        for path in directory.iterdir():
            if not path.is_file() or path.suffix != ".jsonl":
                continue
            if path.name.endswith(".events.jsonl"):
                continue
            ident = path.stem
            if not SESSION_ID_RE.fullmatch(ident) or ident in RESERVED_SESSION_IDS:
                continue
            paths[ident] = path
        return paths

    def _scan(self, paths: Mapping[str, Path]) -> tuple[SessionSummary, ...]:
        items: list[SessionSummary] = []
        for ident in paths:
            try:
                snapshot = self.load(ident)
            except (OSError, ValueError) as err:
                logger.warning("session %s cannot be listed: %s", ident, err)
                continue
            items.append(
                SessionSummary(
                    id=ident,
                    title=snapshot.title,
                    updated_at=last_timestamp(snapshot.entries),
                    message_count=sum(
                        1
                        for entry in snapshot.entries
                        if entry.get("type") == "message"
                        and entry.get("role") in ("user", "assistant")
                    ),
                )
            )
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(items)

    def _read_index(self) -> tuple[SessionSummary, ...] | None:
        path = self._layout.sessions_dir / INDEX_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or raw.get("v") != INDEX_VERSION:
            return None
        items = raw.get("sessions")
        if not isinstance(items, list):
            return None
        summaries: list[SessionSummary] = []
        for item in items:
            if not isinstance(item, dict):
                return None
            ident = item.get("id")
            title = item.get("title", "")
            updated_at = item.get("updatedAt", "")
            count = item.get("messageCount", 0)
            if (
                not isinstance(ident, str)
                or not isinstance(title, str)
                or not isinstance(updated_at, str)
                or not isinstance(count, int)
            ):
                return None
            summaries.append(
                SessionSummary(ident, title, updated_at, count)
            )
        return tuple(summaries)

    def _write_index(self, items: Sequence[SessionSummary]) -> None:
        directory = self._layout.sessions_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "v": INDEX_VERSION,
                "sessions": [summary_dict(item) for item in items],
            }
            path = directory / INDEX_NAME
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as err:
            # The index is a cache; a project that cannot write it still reads.
            logger.warning("session index cannot be written: %s", err)

    def _refresh_index(self) -> None:
        self._write_index(self._scan(self._session_paths()))

    def rename(self, session_id: str, title: str) -> None:
        ident = _require_id(session_id)
        self.load(ident)
        self.append(ident, {"type": "session_info", "title": title.strip()})

    def delete(self, session_id: str) -> None:
        ident = _require_id(session_id)
        path = self._layout.session_path(ident)
        if not path.is_file():
            raise FileNotFoundError(f"session not found: {ident}")
        with SESSION_LOCK:
            path.unlink()
            events = self._layout.session_events_path(ident)
            if events.is_file():
                events.unlink()
            self._refresh_index()

    def ensure_migrated(self) -> MigrationReport:
        """Bring the old ``chats/*.json`` files across, once per store."""
        if self._migration is not None:
            return self._migration
        report = migrate_legacy_chats(self._layout, clock=self._clock)
        if report.migrated:
            self._refresh_index()
        self._migration = report
        return report

    def _now(self) -> str:
        return iso_timestamp(self._clock)


def _require_id(value: str) -> str:
    ident = value.strip()
    if not SESSION_ID_RE.fullmatch(ident) or ident in RESERVED_SESSION_IDS:
        raise ValueError("session id must be 1–80 letters, digits, _ or -")
    return ident


def _read_records(path: Path) -> list[dict[str, object]]:
    """The complete lines of a session, with a trailing partial line dropped."""
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise SessionFormatError(f"{path.name} cannot be read: {err}") from err

    if raw and not raw.endswith(b"\n"):
        # The last line was being written when the process stopped; it has no
        # newline, so it was never committed. Drop its bytes before decoding:
        # a half-written multi-byte character is not valid UTF-8, and the point
        # of the rule is that the reader survives exactly that line.
        cut = raw.rfind(b"\n")
        raw = raw[: cut + 1] if cut >= 0 else b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise SessionFormatError(f"{path.name} is not valid UTF-8: {err}") from err

    lines = text.split("\n")
    records: list[dict[str, object]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise SessionFormatError(
                f"{path.name}:{number} is not JSON: {err}"
            ) from err
        if not isinstance(value, dict):
            raise SessionFormatError(f"{path.name}:{number} is not an object")
        records.append(value)
    if not records:
        raise SessionFormatError(f"{path.name} has no header")
    return records


def _checked_header(
    records: list[dict[str, object]], session_id: str
) -> dict[str, object]:
    header = records[0]
    if header.get("type") != "session":
        raise SessionFormatError("the first line is not a session header")
    version = header.get("v")
    if not isinstance(version, int) or version not in SUPPORTED_VERSIONS:
        raise SessionFormatError(f"unsupported session version: {version!r}")
    if header.get("id") != session_id:
        raise SessionFormatError(
            f"header names {header.get('id')!r}, not {session_id!r}"
        )
    created = header.get("createdAt")
    if created is not None and not isinstance(created, str):
        raise SessionFormatError("session createdAt must be a string")
    return header


def _checked_entry(entry: Mapping[str, object], session_id: str) -> None:
    kind = entry.get("type")
    if not isinstance(kind, str) or not kind:
        raise SessionFormatError(f"session {session_id} has an entry without a type")
    ident = entry.get("id")
    if ident is not None and (not isinstance(ident, str) or not ident):
        raise SessionFormatError(f"session {session_id} has an entry with a bad id")
    timestamp = entry.get("ts")
    if timestamp is not None and not isinstance(timestamp, str):
        raise SessionFormatError(f"session {session_id} has an entry with a bad ts")
    parent = entry.get("parentId")
    if parent is not None and not isinstance(parent, str):
        raise SessionFormatError(
            f"session {session_id} has an entry with a bad parentId"
        )
    if kind not in _KNOWN_TYPES:
        logger.warning("session %s has unknown entry type %s", session_id, kind)


def _title(
    header: Mapping[str, object], entries: list[Mapping[str, object]]
) -> str:
    """The last name given, or the first question, in that order."""
    raw = header.get("title")
    title = raw if isinstance(raw, str) else ""
    for entry in entries:
        if entry.get("type") != "session_info":
            continue
        value = entry.get("title")
        if isinstance(value, str):
            title = value
    if title:
        return title
    for entry in entries:
        if entry.get("type") != "message" or entry.get("role") != "user":
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            return _clip_title(" ".join(content.split()))
    return ""


def _clip_title(text: str) -> str:
    if len(text) <= TITLE_CHARS:
        return text
    return text[: TITLE_CHARS - 1] + "…"


def _repair_tail(path: Path) -> None:
    """Drop an uncommitted last line before appending after it."""
    raw = path.read_bytes()
    if not raw or raw.endswith(b"\n"):
        return
    cut = raw.rfind(b"\n")
    with path.open("r+b") as handle:
        handle.truncate(cut + 1 if cut >= 0 else 0)
