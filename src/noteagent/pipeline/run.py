"""The one pipeline run a project may have at a time.

A run is the object a person starts and a conversation can refer to later. It
records who started it, what it was asked to write, how far it got, and what it
came back with. It is persisted before any stage runs, so a restart can say the
run was interrupted rather than lose it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

RunStatus: TypeAlias = Literal[
    "running",
    "awaiting_confirmation",
    "compiling",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
]

#: A run still owns its project: a second one is refused rather than queued.
ACTIVE_STATUSES = frozenset({"running", "awaiting_confirmation", "compiling"})

#: What a restart closes. A run waiting for its human is paused, not running:
#: the plan it shows is still the plan that was made, so it survives the restart.
INTERRUPTIBLE_STATUSES = frozenset({"running", "compiling"})

#: Who asked for the run. A conversation names its session beside this.
StartedBy: TypeAlias = Literal["human", "session"]

#: How the search query is produced.
QueryMode: TypeAlias = Literal["precise", "model"]

#: Whether the person picks the cards or the run takes the top of the list.
MaterialMode: TypeAlias = Literal["manual", "auto"]

RUN_STATUSES: tuple[RunStatus, ...] = (
    "running",
    "awaiting_confirmation",
    "compiling",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
)
STARTED_BY = ("human", "session")
QUERY_MODES = ("precise", "model")
MATERIAL_MODES = ("manual", "auto")

#: The stage names the worker walks through. Ticket 01 only needs the ends; the
#: stages between are filled by the tickets that build them.
STAGES: tuple[str, ...] = ("starting", "acquire", "frame", "plan", "confirm", "compile", "done")

_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")


@dataclass(frozen=True, slots=True)
class PipelineRun:
    """One run, as it is stored and as the interface reads it."""

    id: str
    intent: str
    started_by: StartedBy
    started_at: str
    updated_at: str
    session_id: str = ""
    query_mode: QueryMode = "precise"
    material_mode: MaterialMode = "auto"
    status: RunStatus = "running"
    stage: str = "starting"
    plan_revision: int = 0
    error: str = ""
    #: What the stages have done so far: what was acquired, what was skipped and
    #: why. It is the run's own record, not a second log.
    detail: Mapping[str, object] = field(default_factory=dict)
    #: What the compile did, once a person approved: the written, skipped and
    #: failed pages. Empty until then.
    result: Mapping[str, object] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        """Whether the run still owns its project."""
        return self.status in ACTIVE_STATUSES


def run_to_dict(run: PipelineRun) -> dict[str, object]:
    """The run as one shape, used by both the run file and the wire."""
    return {
        "id": run.id,
        "intent": run.intent,
        "startedBy": run.started_by,
        "sessionId": run.session_id,
        "queryMode": run.query_mode,
        "materialMode": run.material_mode,
        "status": run.status,
        "stage": run.stage,
        "startedAt": run.started_at,
        "updatedAt": run.updated_at,
        "planRevision": run.plan_revision,
        "error": run.error,
        "detail": dict(run.detail),
        "result": dict(run.result),
    }


def is_run_id(value: str) -> bool:
    """Whether *value* can name a run, and so be used as its file name."""
    return _ID.fullmatch((value or "").strip()) is not None


def run_from_dict(raw: Mapping[str, object]) -> PipelineRun:
    """Read a run back, refusing a shape that would mean something else."""
    if not isinstance(raw, Mapping):
        raise ValueError("a pipeline run must be an object")
    run_id = _required(raw, "id")
    if _ID.fullmatch(run_id) is None:
        raise ValueError(f"not a run id: {run_id!r}")
    intent = _required(raw, "intent")
    started_by = _choice(raw, "startedBy", STARTED_BY, "human")
    query_mode = _choice(raw, "queryMode", QUERY_MODES, "precise")
    material_mode = _choice(raw, "materialMode", MATERIAL_MODES, "auto")
    status = _choice(raw, "status", RUN_STATUSES, "running")
    stage = _text(raw.get("stage")) or "starting"
    if stage not in STAGES:
        raise ValueError(f"unknown pipeline stage: {stage!r}")
    raw_detail = raw.get("detail")
    detail = dict(raw_detail) if isinstance(raw_detail, Mapping) else {}
    raw_result = raw.get("result")
    result = dict(raw_result) if isinstance(raw_result, Mapping) else {}
    return PipelineRun(
        id=run_id,
        intent=intent,
        started_by=started_by,  # type: ignore[arg-type]
        started_at=_text(raw.get("startedAt")),
        updated_at=_text(raw.get("updatedAt")),
        session_id=_text(raw.get("sessionId")),
        query_mode=query_mode,  # type: ignore[arg-type]
        material_mode=material_mode,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        stage=stage,
        plan_revision=_count(raw.get("planRevision")),
        error=_text(raw.get("error")),
        detail=detail,
        result=result,
    )


def _required(raw: Mapping[str, object], key: str) -> str:
    value = _text(raw.get(key))
    if not value:
        raise ValueError(f"a pipeline run needs {key}")
    return value


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _choice(
    raw: Mapping[str, object],
    key: str,
    allowed: tuple[str, ...],
    fallback: str,
) -> str:
    value = _text(raw.get(key)) or fallback
    if value not in allowed:
        raise ValueError(f"unknown {key}: {value!r}")
    return value


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = [
    "ACTIVE_STATUSES",
    "INTERRUPTIBLE_STATUSES",
    "MATERIAL_MODES",
    "PipelineRun",
    "QUERY_MODES",
    "RUN_STATUSES",
    "STAGES",
    "STARTED_BY",
    "is_run_id",
    "run_from_dict",
    "run_to_dict",
]
