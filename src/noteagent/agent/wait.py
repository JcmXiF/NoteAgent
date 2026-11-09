"""The one collaboration node a session may be waiting on.

A wait is session state, not a paused run: the job belongs to the interface that
started it, and the chat only records that it is waiting, on what, and where the
human will finish it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from noteagent.agent.jobs import JobInterface, JobKind

WaitStatus: TypeAlias = Literal[
    "proposed", "entered", "completed", "failed", "cancelled"
]

#: A wait still worth respecting: the human has not finished it yet, so the
#: session may not start a second one.
ACTIVE_STATUSES = frozenset({"proposed", "entered"})

#: The kinds a proposal may name, and where each one can be started. A job
#: proposed from an interface that cannot start it would point the reader at a
#: button that does nothing.
JOB_KINDS = ("plan", "compile")
JOB_INTERFACES = ("source", "plan_board", "compile_board")
JOB_SOURCES: dict[str, tuple[str, ...]] = {
    "plan": ("source", "plan_board"),
    "compile": ("plan_board", "compile_board"),
}


@dataclass(frozen=True, slots=True)
class WaitState:
    """The current wait, derived from the wait events committed to the session."""

    wait_id: str
    job: JobKind
    interface: JobInterface
    status: WaitStatus
    proposed_at: str
    result_summary: str = ""
    #: The stable name of the action the human takes in the original interface.
    #: The core cannot know the reader's language, so the interface words it.
    completion: str = ""
    intent: str = ""
    reason: str = ""


def is_active(wait: WaitState | None) -> bool:
    """Whether the session still has to respect *wait*."""
    return wait is not None and wait.status in ACTIVE_STATUSES


def completion_for(job: str) -> str:
    """The action a human takes to start *job*, as a code the interface words."""
    return f"start_{job}" if job in JOB_KINDS else "start"


def wait_payload(wait: WaitState) -> dict[str, object]:
    """The wait as both the SSE event and the session view carry it."""
    body: dict[str, object] = {
        "waitId": wait.wait_id,
        **_wait_fields(wait),
        "status": wait.status,
    }
    if wait.result_summary:
        body["resultSummary"] = wait.result_summary
    return body


def proposed_entry(wait: WaitState) -> dict[str, object]:
    """The log entry that starts *wait*.

    The entry's own id is the wait id: that is what the interface carries back
    when the human presses the original button. The wire payload and the entry
    share these fields, so they cannot drift into two shapes.
    """
    return {
        "id": wait.wait_id,
        "waitId": wait.wait_id,
        **_wait_fields(wait),
    }


def _wait_fields(wait: WaitState) -> dict[str, object]:
    body: dict[str, object] = {
        "job": wait.job,
        "interface": wait.interface,
        "completion": wait.completion or completion_for(wait.job),
        "proposedAt": wait.proposed_at,
    }
    if wait.intent:
        body["intent"] = wait.intent
    if wait.reason:
        body["reason"] = wait.reason
    return body
