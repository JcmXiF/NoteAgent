"""What a caller asks a runtime to do, and the four calls it may make.

A run is one user input (or a crash resume), executed eagerly until it ends on
its final message. The runtime is the kernel's external seam: the API and the
tests all cross it the same way, with the same request and the same sink.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Event
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from noteagent.agent.events import RunEvent


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


class RunPaused(RuntimeError):
    """The run stopped at a boundary because its reader went away."""


@dataclass(frozen=True, slots=True)
class UserInput:
    """A person asked something in an existing session."""

    session_id: str
    content: str
    on_busy: OnBusy = OnBusy.REJECT
    #: A caller may name the run before the kernel starts it, so a wrapper can
    #: release the writing lease it took; empty means the kernel generates one.
    run_id: str = ""
    #: Set by the stream that owns this run when its reader disconnects. The
    #: kernel stops at the next committed boundary and leaves an interrupted
    #: marker; a stream that never owns the run never reads it.
    pause: Event | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RunResume:
    """A crashed run continues from its last committed turn.

    There is no job to wait for, only a run marker and a transcript whose
    unfinished tool calls become ``outcome unknown``.
    """

    session_id: str
    run_id: str
    #: The continuation's own identity; empty means the kernel generates one.
    new_run_id: str = ""
    pause: Event | None = field(default=None, repr=False, compare=False)
    #: A run slot the route already claimed; the kernel must not claim twice.
    claim: Event | None = field(default=None, repr=False, compare=False)


RunRequest: TypeAlias = UserInput | RunResume

RunStatus: TypeAlias = Literal[
    "completed", "failed", "cancelled", "interrupted"
]


@dataclass(frozen=True, slots=True)
class RunResult:
    """How a run ended, for the caller that asked for it."""

    session_id: str
    run_id: str
    status: RunStatus
    error: str = ""


@dataclass(frozen=True, slots=True)
class ResumeCheck:
    """What an interrupted run's environment comparison found.

    ``refusal`` means the run cannot continue against the current project,
    model, toolset or prompt config. ``note`` is what the model reads: a plain
    resume sentence, plus a warning when the wiki or sources moved.
    """

    note: str = ""
    refusal: str = ""


#: Where a run reports while it runs. The API maps it onto an SSE queue; a test
#: passes ``list.append``; a background resume passes a sink that drops.
EventSink: TypeAlias = Callable[[RunEvent], None]


@dataclass(frozen=True, slots=True)
class RunRef:
    """Which run, in which session: the identity a trace records against."""

    session_id: str
    run_id: str


@runtime_checkable
class TraceSink(Protocol):
    """Where a run's steps are recorded for support, separately from its content.

    The sink sees the same events as the interface, and the implementation
    decides what may be written down. It is a collaborator of the runtime, not
    one of its four calls: it never changes what the run does, and a failure in
    it must not end the run.
    """

    def started(self, run: RunRef) -> None:
        """A run claimed its session and is about to begin."""
        ...

    def record(self, run: RunRef, event: RunEvent) -> None:
        """One event of the run, to be reduced to its recordable fields."""
        ...


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

    def is_busy(self, session_id: str) -> bool:
        """Whether the session has a run in progress."""
        ...

    def compact(self, session_id: str, instructions: str = "") -> bool:
        """Summarize older history now; say whether anything was cut.

        It is a runtime call because it may ask the model for a summary, and it
        claims the session like a run while it does.
        """
        ...
