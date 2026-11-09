"""What a caller asks a runtime to do, and the four calls it may make.

A run is one user input or one resume, executed eagerly until it ends. The
runtime is the kernel's external seam: the API, the job watcher and the tests
all cross it the same way, with the same request and the same sink.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from noteagent.agent.events import RunEvent
from noteagent.agent.wait import WaitState


class OnBusy(StrEnum):
    """What to do when the session already has a run."""

    REJECT = "reject"
    QUEUE = "queue"


def busy_message(session_id: str) -> str:
    """The sentence a session that already has a run says to either caller."""
    return f"session {session_id} already has a run"


class BusyError(RuntimeError):
    """The session already has a run, and this request may not start another."""


class RunCancelled(RuntimeError):
    """The run stopped because it was asked to."""


@dataclass(frozen=True, slots=True)
class UserInput:
    """A person asked something in an existing session."""

    session_id: str
    content: str
    on_busy: OnBusy = OnBusy.REJECT


@dataclass(frozen=True, slots=True)
class Resume:
    """A waited-on job came back, and the session continues from it."""

    session_id: str
    wait_id: str
    on_busy: OnBusy = OnBusy.QUEUE


@dataclass(frozen=True, slots=True)
class Retry:
    """The last run failed and the caller is asking for it again."""

    session_id: str
    on_busy: OnBusy = OnBusy.REJECT
    reason: str = ""


RunRequest: TypeAlias = UserInput | Resume | Retry

RunStatus: TypeAlias = Literal["completed", "waiting", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class RunResult:
    """How a run ended, for the caller that asked for it."""

    session_id: str
    run_id: str
    status: RunStatus
    wait_id: str = ""
    error: str = ""


#: Where a run reports while it runs. The API maps it onto an SSE queue; a test
#: passes ``list.append``; a background resume passes a sink that drops.
EventSink: TypeAlias = Callable[[RunEvent], None]


@runtime_checkable
class AgentRuntime(Protocol):
    """The kernel's external seam (ADR-0037).

    ``execute`` blocks until the run ends. It reports through ``sink`` and
    returns the result; the last event it sends is ``done`` or ``error``.
    """

    def execute(self, request: RunRequest, sink: EventSink) -> RunResult:
        """Run one request to its end, reporting as it goes."""
        ...

    def cancel(self, session_id: str) -> None:
        """Stop the session's current run, if there is one."""
        ...

    def wait_state(self, session_id: str) -> WaitState | None:
        """The session's current wait, or ``None`` when it has none."""
        ...

    def is_busy(self, session_id: str) -> bool:
        """Whether the session has a run in progress."""
        ...
