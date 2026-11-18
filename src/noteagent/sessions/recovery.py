"""Find the run a crash left unfinished, and say what waits on the reader.

The scan reads the markers a run committed. A latest marker still ``running``
means the process died before the run could write its terminal state; the
transcript shape then says whether the run actually finished, stopped at a tool
call, or went to a wait. The scan writes the recovered terminal marker once, so
the next start reads a settled state instead of inferring again.

Only the session's **latest run** is reconciled: once a newer run starts, an
older ``running`` marker is history. Only sessions that carry a marker are
considered, so a session from before run markers existed is never invented as
interrupted.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

from noteagent.agent.clock import Clock, SystemClock, iso_timestamp
from noteagent.agent.run_marker import (
    ABANDONED,
    INTERRUPTED,
    RUNNING,
    SETTLED_STATES,
    WAITING,
    RunMarker,
    infer_terminal,
    interrupted_marker,
    last_marker,
    marker_entry,
    run_steps,
)
from noteagent.agent.store import SessionStore, SessionSummary
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore

logger = logging.getLogger(__name__)

__all__ = [
    "InterruptedRun",
    "abandon_run",
    "interrupted_payload",
    "interrupted_run",
    "interrupted_runs",
    "interrupted_runs_for_layout",
    "recover_store",
]


@dataclass(frozen=True, slots=True)
class InterruptedRun:
    """One run a crash left unfinished, as the reader's panel needs it."""

    session_id: str
    title: str
    run_id: str
    reason: str
    at: str
    last_entry_id: str
    step: int = 0
    last_message: str = ""

    def payload(self) -> dict[str, object]:
        body = _marker_fields(
            self.run_id,
            self.reason,
            self.at,
            self.step,
            self.last_entry_id,
        )
        body.update(
            {
                "sessionId": self.session_id,
                "title": self.title,
                "lastMessage": self.last_message,
            }
        )
        return body


def recover_store(
    store: SessionStore,
    *,
    clock: Clock | None = None,
    mark: bool = True,
) -> tuple[InterruptedRun, ...]:
    """Recover the latest run of each session, and return the interrupted ones.

    A settled marker is left alone. A ``running`` marker is compared with the
    transcript: an unfinished shape becomes ``interrupted``; a final answer
    becomes ``finished`` (the process died before the marker). The recovered
    marker is written once when *mark* is true; a read-only caller passes false.
    """
    found: list[InterruptedRun] = []
    now = clock or SystemClock()
    for summary, entries in _loaded(store):
        marker = last_marker(entries)
        if marker is None:
            continue
        if marker.state == INTERRUPTED:
            found.append(_record(summary, marker, entries))
            continue
        if marker.state in SETTLED_STATES:
            if marker.state == WAITING:
                # The run is parked on a job, not interrupted; a question
                # typed meanwhile continues through the wait's own resume.
                continue
            pending = _pending_question(entries)
            if pending is not None:
                started, interrupted = _question_markers(pending, now)
                if mark:
                    _append(store, summary.id, started)
                    _append(store, summary.id, interrupted)
                found.append(_record(summary, interrupted, entries))
            continue
        inference = infer_terminal(entries)
        recovered = replace(
            marker,
            state=inference.state,
            last_entry_id=inference.last_entry_id or marker.last_entry_id,
            reason=inference.reason,
            note=(
                ""
                if inference.state == INTERRUPTED
                else "terminal state recovered at startup"
            ),
            created_at=iso_timestamp(now),
        )
        if mark:
            _append(store, summary.id, recovered)
        if inference.state == INTERRUPTED:
            found.append(_record(summary, recovered, entries))
    return tuple(found)


def interrupted_runs(store: SessionStore) -> tuple[InterruptedRun, ...]:
    """The sessions whose latest marker is ``interrupted``, without writing."""
    found: list[InterruptedRun] = []
    for summary, entries in _loaded(store):
        marker = last_marker(entries)
        if marker is not None and marker.state == INTERRUPTED:
            found.append(_record(summary, marker, entries))
    return tuple(found)


def interrupted_runs_for_layout(
    layout: ProjectLayout,
) -> tuple[InterruptedRun, ...]:
    """The interrupted runs of a project's sessions, without writing."""
    return interrupted_runs(JsonlSessionStore(layout))


def interrupted_payload(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """The session view's ``state.interrupted``, or ``None``."""
    marker = last_marker(entries)
    if marker is None or marker.state != INTERRUPTED:
        return None
    return _marker_fields(
        marker.run_id,
        _reason(marker),
        marker.created_at,
        run_steps(entries, marker.run_id),
        marker.last_entry_id,
    )


def interrupted_run(
    store: SessionStore, session_id: str, run_id: str
) -> RunMarker | None:
    """The marker an interrupted run still holds, or ``None``."""
    return interrupted_marker(
        store.load(session_id).entries, run_id
    )


def abandon_run(
    store: SessionStore,
    session_id: str,
    run_id: str,
    *,
    clock: Clock | None = None,
) -> tuple[RunMarker | None, bool]:
    """Stop waiting on an interrupted run; say whether a marker was written.

    A second abandon of the same run is a no-op: the latest marker is already
    ``abandoned``, so it answers with that marker and ``False``. A run that is
    not interrupted at all answers ``(None, False)`` and the route refuses it.
    """
    marker = last_marker(store.load(session_id).entries)
    if marker is None or marker.run_id != run_id:
        return None, False
    if marker.state == ABANDONED:
        return marker, False
    if marker.state != INTERRUPTED:
        return None, False
    abandoned = replace(
        marker,
        state=ABANDONED,
        note="",
        reason="abandoned by the reader",
        created_at=iso_timestamp(clock or SystemClock()),
    )
    store.append(session_id, marker_entry(abandoned))
    return abandoned, True


def _pending_question(
    entries: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    """The user turn committed after the latest marker, if there is one.

    ``infer_terminal`` owns the shape rules: a failed run that wrote its marker
    after the question is settled, not a question waiting for a run; only a
    question after the last settled marker is pending.
    """
    inference = infer_terminal(entries)
    if (
        inference.state != INTERRUPTED
        or inference.reason != "question without answer"
    ):
        return None
    last = entries[-1] if entries else None
    if (
        isinstance(last, Mapping)
        and last.get("type") == "message"
        and last.get("role") == "user"
    ):
        return last
    return None


def _question_markers(
    user: Mapping[str, object], now: Clock
) -> tuple[RunMarker, RunMarker]:
    """A start and interrupted marker for a question no run ever began.

    The process died after committing the user turn and before the kernel's
    start marker. A synthetic run makes the question resumable without
    pretending any tool call happened: the start marker is what scopes the
    transcript, so the resumed run has no missing result to invent.
    """
    entry_id = user.get("id")
    ident = entry_id if isinstance(entry_id, str) else ""
    at = iso_timestamp(now)
    run_id = f"recovered-{ident or at}"
    started = RunMarker(run_id, RUNNING, last_entry_id=ident, created_at=at)
    interrupted = RunMarker(
        run_id,
        INTERRUPTED,
        last_entry_id=ident,
        reason="question without answer",
        created_at=at,
    )
    return started, interrupted


def _loaded(
    store: SessionStore,
) -> Iterator[tuple[SessionSummary, Sequence[Mapping[str, object]]]]:
    """Each readable session, skipping the ones that cannot be listed or read."""
    try:
        summaries = tuple(store.list())
    except Exception:
        logger.exception("the sessions could not be listed for recovery")
        return
    for summary in summaries:
        try:
            entries = store.load(summary.id).entries
        except Exception:
            logger.warning(
                "session %s could not be read for recovery",
                summary.id,
                exc_info=True,
            )
            continue
        yield summary, entries


def _marker_fields(
    run_id: str,
    reason: str,
    at: str,
    step: int,
    last_entry_id: str,
) -> dict[str, object]:
    return {
        "runId": run_id,
        "reason": reason,
        "at": at,
        "step": step,
        "lastEntryId": last_entry_id,
    }


def _reason(marker: RunMarker) -> str:
    return marker.reason or marker.note or "interrupted"


def _record(
    summary: SessionSummary,
    marker: RunMarker,
    entries: Sequence[Mapping[str, object]],
) -> InterruptedRun:
    return InterruptedRun(
        session_id=summary.id,
        title=summary.title,
        run_id=marker.run_id,
        reason=_reason(marker),
        at=marker.created_at,
        last_entry_id=marker.last_entry_id,
        step=run_steps(entries, marker.run_id),
        last_message=_last_message(entries),
    )


def _last_message(
    entries: Sequence[Mapping[str, object]],
) -> str:
    for entry in reversed(entries):
        if entry.get("type") != "message":
            continue
        if entry.get("role") not in ("user", "assistant"):
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:200]
    return ""


def _append(store: SessionStore, session_id: str, marker: RunMarker) -> None:
    try:
        store.append(session_id, marker_entry(marker))
    except Exception:
        logger.exception(
            "the recovered marker for session %s could not be written",
            session_id,
        )
