"""The job seam: what a waited-on job came back with.

A conversation never starts a job: the human starts it in the interface that
owns it. The runtime only looks one up (to reconcile a missed completion) and
asks it to stop.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias, runtime_checkable

JobStatus: TypeAlias = Literal["succeeded", "failed", "cancelled"]

#: The jobs a collaboration node may wait on.
JobKind: TypeAlias = Literal["plan", "compile"]

#: The interface where the human finishes the job by pressing the original button.
JobInterface: TypeAlias = Literal["source", "plan_board", "compile_board"]


@dataclass(frozen=True, slots=True)
class JobArtifact:
    """A bounded reference to something a job produced."""

    kind: str
    id: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class JobResult:
    """What the chat records when a job finishes."""

    job: str
    status: JobStatus
    summary: str
    artifacts: tuple[JobArtifact, ...] = ()
    error: str = ""
    usage: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class JobClient(Protocol):
    def get(self, job_id: str) -> JobResult | None:
        """The job's result, or ``None`` while it is still running."""
        ...

    def cancel(self, job_id: str) -> None:
        """Ask the job to stop; the original interface still owns the outcome."""
        ...


class JobBusyError(RuntimeError):
    """A collaboration job is already running."""


class JobGate:
    """One collaboration job at a time, across every session.

    A plan and a compile share the gate: two readers working at once would
    write to the same project and each would believe its own result was the
    project's state. The gate is process-wide by default; a test can hold it to
    prove the refusal.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner = ""

    def claim(self, owner: str) -> None:
        """Take the gate for *owner*, or raise ``JobBusyError``."""
        with self._lock:
            if self._owner:
                raise JobBusyError(
                    f"a collaboration job is already running: {self._owner}"
                )
            self._owner = owner

    def release(self, owner: str) -> None:
        """Let go only if *owner* still holds it."""
        with self._lock:
            if self._owner == owner:
                self._owner = ""

    def is_busy(self) -> bool:
        with self._lock:
            return bool(self._owner)
