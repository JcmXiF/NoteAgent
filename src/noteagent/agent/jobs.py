"""The job seam: what a waited-on job came back with.

A conversation never starts a job: the human starts it in the interface that
owns it. The runtime only looks one up (to reconcile a missed completion) and
asks it to stop.
"""

from __future__ import annotations

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
