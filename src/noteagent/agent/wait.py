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


@dataclass(frozen=True, slots=True)
class WaitState:
    """The current wait, derived from the wait events committed to the session."""

    wait_id: str
    job: JobKind
    interface: JobInterface
    status: WaitStatus
    proposed_at: str
    result_summary: str = ""
