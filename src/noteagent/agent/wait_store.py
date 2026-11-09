"""A session's wait, kept as events in the same log as its turns.

The wait is not a second file and not a paused run: it is what the wait events
say when read in order. That means a reload, a restart, or a reader that lost
the SSE stream all see the same wait the runtime does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from noteagent.agent.jobs import JobResult
from noteagent.agent.store import Entry, SessionStore
from noteagent.agent.wait import (
    JOB_INTERFACES,
    JOB_KINDS,
    WaitState,
    WaitStatus,
    completion_for,
    proposed_entry,
)


class SessionWaitStore:
    """The wait events a session committed, read as its current wait."""

    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    def current(self, session_id: str) -> WaitState | None:
        return current_wait(self._sessions.load(session_id).entries)

    def propose(self, session_id: str, wait: WaitState) -> None:
        self._sessions.append(
            session_id, {"type": "wait_proposed", **proposed_entry(wait)}
        )

    def enter(self, session_id: str, wait_id: str, job_id: str) -> None:
        self._sessions.append(
            session_id,
            {"type": "wait_entered", "waitId": wait_id, "jobId": job_id},
        )

    def complete(self, session_id: str, wait_id: str, result: JobResult) -> None:
        status = {
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(result.status, "completed")
        entry: dict[str, object] = {
            "type": "wait_completed",
            "waitId": wait_id,
            "job": result.job,
            "status": status,
            "summary": result.summary,
        }
        if result.error:
            entry["error"] = result.error
        if result.artifacts:
            entry["artifacts"] = [
                {
                    "kind": artifact.kind,
                    "id": artifact.id,
                    "label": artifact.label,
                }
                for artifact in result.artifacts
            ]
        if result.usage:
            entry["usage"] = dict(result.usage)
        self._sessions.append(session_id, entry)

    def cancel(self, session_id: str, wait_id: str, reason: str = "") -> None:
        entry: dict[str, object] = {
            "type": "wait_cancelled",
            "waitId": wait_id,
        }
        if reason:
            entry["reason"] = reason
        self._sessions.append(session_id, entry)


def current_wait(entries: Sequence[Entry]) -> WaitState | None:
    """The latest wait and what the later events did to it, or ``None``.

    A proposal replaces whatever came before it. Events naming an older wait are
    ignored, so a late completion or cancel cannot move a newer proposal.
    """
    wait: WaitState | None = None
    for entry in entries:
        kind = entry.get("type")
        if kind == "wait_proposed":
            wait = _proposed(entry)
            continue
        if wait is None or entry.get("waitId") != wait.wait_id:
            continue
        if kind == "wait_entered":
            wait = replace(wait, status="entered")
        elif kind == "wait_completed":
            wait = replace(
                wait,
                status=_completed_status(entry.get("status")),
                result_summary=_text(entry.get("summary")),
            )
        elif kind == "wait_cancelled":
            wait = replace(wait, status="cancelled")
    return wait


def _proposed(entry: Mapping[str, object]) -> WaitState | None:
    job = entry.get("job")
    interface = entry.get("interface")
    if job not in JOB_KINDS or interface not in JOB_INTERFACES:
        return None
    wait_id = entry.get("waitId") or entry.get("id")
    if not isinstance(wait_id, str) or not wait_id:
        return None
    proposed_at = entry.get("proposedAt") or entry.get("ts")
    completion = entry.get("completion")
    return WaitState(
        wait_id=wait_id,
        job=job,  # type: ignore[arg-type]
        interface=interface,  # type: ignore[arg-type]
        status="proposed",
        proposed_at=proposed_at if isinstance(proposed_at, str) else "",
        completion=(
            completion
            if isinstance(completion, str) and completion
            else completion_for(job)
        ),
        intent=_text(entry.get("intent")),
        reason=_text(entry.get("reason")),
    )


def _completed_status(value: object) -> WaitStatus:
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    return "completed"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
