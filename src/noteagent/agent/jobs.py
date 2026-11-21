"""What a job produced, as the chat records it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

JobStatus: TypeAlias = Literal["succeeded", "failed", "cancelled"]

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
