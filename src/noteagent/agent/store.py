"""Where a transcript lives, and where the one wait on a session lives.

The store is append-only from the runtime's point of view: an entry is committed
at a boundary and never rewritten. How the bytes are laid out is the storage
adapter's business; this contract only names what the loop may ask for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from noteagent.agent.jobs import JobResult
from noteagent.agent.wait import WaitState

#: One committed line of a transcript. The typed variants arrive with the
#: projection, which is the only thing that interprets them.
Entry: TypeAlias = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SessionSummary:
    """One row of the session list."""

    id: str
    title: str
    updated_at: str = ""
    message_count: int = 0


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """A session's committed entries, in order, after its header.

    The header is metadata: its version travels here, and its id is ``id``. The
    projection never sees it, so the first entry is the first committed turn.
    """

    id: str
    title: str
    entries: tuple[Entry, ...] = ()
    version: int = 1


def summary_dict(item: "SessionSummary") -> dict[str, object]:
    """One list row as the index and the API both carry it."""
    return {
        "id": item.id,
        "title": item.title,
        "updatedAt": item.updated_at,
        "messageCount": item.message_count,
    }


def last_timestamp(entries: Sequence[Entry]) -> str:
    """The time of the last committed entry, or an empty string."""
    for entry in reversed(entries):
        value = entry.get("ts")
        if isinstance(value, str) and value:
            return value
    return ""


@runtime_checkable
class SessionStore(Protocol):
    def create(self, session_id: str, title: str = "") -> SessionSnapshot:
        ...

    def load(self, session_id: str) -> SessionSnapshot:
        ...

    def append(self, session_id: str, entry: Entry) -> None:
        ...

    def list(self) -> Sequence[SessionSummary]:
        ...

    def rename(self, session_id: str, title: str) -> None:
        ...

    def delete(self, session_id: str) -> None:
        ...


@runtime_checkable
class WaitStore(Protocol):
    """The wait events a session has committed, read as its current wait."""

    def current(self, session_id: str) -> WaitState | None:
        ...

    def propose(self, session_id: str, wait: WaitState) -> None:
        ...

    def enter(self, session_id: str, wait_id: str, job_id: str) -> None:
        ...

    def finish(self, session_id: str, wait_id: str, result: JobResult) -> None:
        ...

    def cancel(self, session_id: str, wait_id: str, reason: str = "") -> None:
        ...
