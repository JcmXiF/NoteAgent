"""The project's effect journal: what a run changed, one line at a time.

A step that changes something outside the transcript gets a row here: a wiki page
write, a raw file added or deleted, a note, a setting, a shell command. A
read-only step does not. The journal is project-level and append-only, so a chat
run and a pipeline run write to one audit trail, and a later restore can ask what
a particular run did.

Rows hold provider refs, never page text or secrets: a ref names a version the
snapshot store already keeps.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds, iso_timestamp
from noteagent.project.layout import ProjectLayout

logger = logging.getLogger(__name__)

EFFECT_VERSION = 1
EFFECT_TYPE = "effect"

WIKI_PAGE_WRITE = "wiki_page_write"
RAW_ADD = "raw_add"
RAW_DELETE = "raw_delete"
NOTE_WRITE = "note_write"
CONFIG_WRITE = "config_write"
SHELL = "shell"
EXTERNAL_WRITE = "external_write"
UNDO = "undo"

#: Only side effects belong in the journal; reads are in the transcript/trace.
KINDS = frozenset(
    {
        CONFIG_WRITE,
        EXTERNAL_WRITE,
        NOTE_WRITE,
        RAW_ADD,
        RAW_DELETE,
        SHELL,
        UNDO,
        WIKI_PAGE_WRITE,
    }
)

COMMITTED = "committed"
UNDONE = "undone"
FAILED = "failed"

STATUSES = frozenset({COMMITTED, FAILED, UNDONE})

#: How a wiki page write can be undone, and by whom.
REVERSIBLE_SNAPSHOT = "snapshot"
UNDO_PAGE_HISTORY = "page_history"
PROVIDER_PAGE_HISTORY = "page_history"

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class Effect:
    """One side effect, with refs to the states around it.

    ``ident`` and ``at`` are filled by the journal when the caller leaves them
    empty. ``before`` and ``after`` are provider refs (content digests), not
    text; ``undo`` names the provider that can put ``before`` back.
    """

    kind: str
    target: str = ""
    ident: str = ""
    at: str = ""
    run_id: str = ""
    session_id: str = ""
    actor: str = ""
    before: str = ""
    after: str = ""
    reversible: str = ""
    undo: str = ""
    provider: str = ""
    status: str = COMMITTED
    error: str = ""


class EffectJournal:
    """One project's ``.noteagent/effects.jsonl``, shared by every run."""

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

    def new_id(self) -> str:
        """Reserve an effect id before the write it will later describe.

        A writer needs the id for the page-history row it commits first; the
        effect itself follows once the page write has succeeded.
        """
        return self._ids.new_id("eff")

    def append(self, effect: Effect) -> Effect:
        """Write one effect and return it with its id and timestamp filled."""
        if effect.kind not in KINDS:
            raise ValueError(f"not an effect kind: {effect.kind!r}")
        if effect.status not in STATUSES:
            raise ValueError(f"not an effect status: {effect.status!r}")
        path = self._layout.effects
        with _lock_for(self._layout):
            path.parent.mkdir(parents=True, exist_ok=True)
            with _file_lock(path):
                written = replace(
                    effect,
                    ident=effect.ident or self.new_id(),
                    at=effect.at or iso_timestamp(self._clock),
                )
                line = (
                    json.dumps(
                        _row(written),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                _finish_partial_line(path)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
        return written

    def query(
        self,
        *,
        run_id: str = "",
        session_id: str = "",
        kind: str = "",
        status: str = "",
    ) -> tuple[Effect, ...]:
        """The matching effects, ordered by ``at`` and then by line."""
        matches = [
            (index, effect)
            for index, effect in enumerate(self._read())
            if _matches(effect, run_id, session_id, kind, status)
        ]
        matches.sort(key=lambda pair: (pair[1].at, pair[0]))
        return tuple(effect for _index, effect in matches)

    def latest(
        self,
        *,
        run_id: str = "",
        session_id: str = "",
        kind: str = "",
        status: str = "",
    ) -> Effect | None:
        """The last matching effect, or ``None``."""
        matches = self.query(
            run_id=run_id,
            session_id=session_id,
            kind=kind,
            status=status,
        )
        return matches[-1] if matches else None

    def _read(self) -> list[Effect]:
        path = self._layout.effects
        if not path.is_file():
            return []
        effects: list[Effect] = []
        try:
            with _lock_for(self._layout), _file_lock(path):
                with path.open("rb") as handle:
                    for number, raw_line in enumerate(handle, start=1):
                        if not raw_line.strip():
                            continue
                        try:
                            line = raw_line.decode("utf-8").strip()
                        except UnicodeDecodeError:
                            logger.warning(
                                "effect journal line %d is not UTF-8; skipped",
                                number,
                            )
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning(
                                "effect journal line %d is not JSON; skipped",
                                number,
                            )
                            continue
                        effect = _effect(raw)
                        if effect is None:
                            logger.warning(
                                "effect journal line %d is not a usable "
                                "effect; skipped",
                                number,
                            )
                            continue
                        effects.append(effect)
        except OSError as err:
            logger.warning("the effect journal cannot be read: %s", err)
            return []
        return effects


def _finish_partial_line(path: Path) -> None:
    """End a line a crash left unfinished, so the next effect has its own.

    Called under the journal's file lock. A partial line is already a bad line
    the reader skips; appending directly onto it would join the two and lose the
    effect being written now. A full row that only lost its newline stays a
    valid effect, which is the safer audit answer for an append-only journal.
    """
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return
    with path.open("ab") as handle:
        handle.write(b"\n")


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """A lock file beside the journal, so two processes append one at a time."""
    lock_path = path.with_name(f"{path.name}.lock")
    handle = open(lock_path, "a+b")
    try:
        _lock_handle(handle)
        yield
    finally:
        try:
            _unlock_handle(handle)
        except OSError:
            pass
        handle.close()


def _lock_handle(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        while True:
            try:
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.02)
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: Any) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def effect_payload(effect: Effect) -> dict[str, object]:
    """The public shape of one effect, for the project effects API."""
    return {
        "id": effect.ident,
        "at": effect.at,
        "runId": effect.run_id,
        "sessionId": effect.session_id,
        "actor": effect.actor,
        "kind": effect.kind,
        "target": effect.target,
        "before": effect.before,
        "after": effect.after,
        "reversible": effect.reversible,
        "undo": effect.undo,
        "provider": effect.provider,
        "status": effect.status,
        "error": effect.error,
    }


def _row(effect: Effect) -> dict[str, object]:
    return {
        "v": EFFECT_VERSION,
        "type": EFFECT_TYPE,
        **effect_payload(effect),
    }


def _effect(raw: object) -> Effect | None:
    if not isinstance(raw, dict):
        return None
    version = raw.get("v")
    if (
        raw.get("type") != EFFECT_TYPE
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != EFFECT_VERSION
    ):
        return None
    kind = _text(raw.get("kind"))
    status = _text(raw.get("status"))
    ident = _text(raw.get("id"))
    at = _text(raw.get("at"))
    if kind not in KINDS or status not in STATUSES or not ident or not at:
        return None
    return Effect(
        kind=kind,
        target=_text(raw.get("target")),
        ident=ident,
        at=at,
        run_id=_text(raw.get("runId")),
        session_id=_text(raw.get("sessionId")),
        actor=_text(raw.get("actor")),
        before=_text(raw.get("before")),
        after=_text(raw.get("after")),
        reversible=_text(raw.get("reversible")),
        undo=_text(raw.get("undo")),
        provider=_text(raw.get("provider")),
        status=status,
        error=_text(raw.get("error")),
    )


def _matches(
    effect: Effect,
    run_id: str,
    session_id: str,
    kind: str,
    status: str,
) -> bool:
    return (
        (not run_id or effect.run_id == run_id)
        and (not session_id or effect.session_id == session_id)
        and (not kind or effect.kind == kind)
        and (not status or effect.status == status)
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _lock_for(layout: ProjectLayout) -> threading.Lock:
    key = str(Path(layout.root).resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


__all__ = [
    "COMMITTED",
    "CONFIG_WRITE",
    "EFFECT_TYPE",
    "EFFECT_VERSION",
    "EXTERNAL_WRITE",
    "Effect",
    "EffectJournal",
    "FAILED",
    "effect_payload",
    "KINDS",
    "NOTE_WRITE",
    "PROVIDER_PAGE_HISTORY",
    "RAW_ADD",
    "RAW_DELETE",
    "REVERSIBLE_SNAPSHOT",
    "SHELL",
    "STATUSES",
    "UNDO",
    "UNDO_PAGE_HISTORY",
    "UNDONE",
    "WIKI_PAGE_WRITE",
]
