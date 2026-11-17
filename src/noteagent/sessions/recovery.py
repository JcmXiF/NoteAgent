"""Find runs a crash left unfinished, and say what waits on the reader.

The scan reads the markers a run committed. A latest marker still ``running``
means the process died before the run could write its terminal state; the
transcript shape then says whether the run actually finished, stopped at a tool
call, or went to a wait. The scan writes the recovered terminal marker once, so
the next start reads a settled state instead of inferring again.

Only sessions that carry a marker are considered: a session from before run
markers existed is not an interrupted run, and the scan must not invent one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from noteagent.agent.clock import Clock, SystemClock, iso_timestamp
from noteagent.agent.run_marker import (
    ABANDONED,
    INTERRUPTED,
    SETTLED_STATES,
    RunMarker,
    infer_terminal,
    last_marker,
    marker_entry,
    run_steps,
)
from noteagent.agent.store import SessionStore, SessionSummary

logger = logging.getLogger(__name__)

__all__ = [
    "InterruptedRun",
    "abandon_run",
    "interrupted_payload",
    "interrupted_run",
    "interrupted_runs",
    "scan_store",
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

    def payload(self) -> dict[str, object]:
        return {
            "sessionId": self.session_id,
            "title": self.title,
            "runId": self.run_id,
            "reason": self.reason,
            "at": self.at,
            "step": self.step,
            "lastEntryId": self.last_entry_id,
        }


def scan_store(
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
    try:
        summaries = tuple(store.list())
    except Exception:
        logger.exception("the sessions could not be listed for recovery")
        return ()
    found: list[InterruptedRun] = []
    now = clock or SystemClock()
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
        marker = last_marker(entries)
        if marker is None:
            continue
        if marker.state == INTERRUPTED:
            found.append(_record(summary, marker, entries))
            continue
        if marker.state in SETTLED_STATES:
            continue
        inference = infer_terminal(entries)
        if inference.state not in SETTLED_STATES:
            continue
        recovered = replace(
            marker,
            state=inference.state,
            last_entry_id=inference.last_entry_id or marker.last_entry_id,
            note=(
                "the run was interrupted; resume or abandon it"
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
    try:
        summaries = tuple(store.list())
    except Exception:
        logger.exception("the sessions could not be listed for recovery")
        return ()
    found: list[InterruptedRun] = []
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
        marker = last_marker(entries)
        if marker is not None and marker.state == INTERRUPTED:
            found.append(_record(summary, marker, entries))
    return tuple(found)


def interrupted_payload(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """The session view's ``state.interrupted``, or ``None``."""
    marker = last_marker(entries)
    if marker is None or marker.state != INTERRUPTED:
        return None
    return {
        "runId": marker.run_id,
        "reason": marker.note or "interrupted",
        "at": marker.created_at,
        "step": run_steps(entries, marker.run_id),
        "lastEntryId": marker.last_entry_id,
    }


def interrupted_run(
    store: SessionStore, session_id: str, run_id: str
) -> RunMarker | None:
    """The marker an interrupted run still holds, or ``None``."""
    marker = last_marker(store.load(session_id).entries)
    if (
        marker is None
        or marker.state != INTERRUPTED
        or marker.run_id != run_id
    ):
        return None
    return marker


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
        note="abandoned by the reader",
        created_at=iso_timestamp(clock or SystemClock()),
    )
    store.append(session_id, marker_entry(abandoned))
    return abandoned, True


def _record(
    summary: SessionSummary,
    marker: RunMarker,
    entries: Sequence[Mapping[str, object]],
) -> InterruptedRun:
    return InterruptedRun(
        session_id=summary.id,
        title=summary.title,
        run_id=marker.run_id,
        reason=marker.note or "interrupted",
        at=marker.created_at,
        last_entry_id=marker.last_entry_id,
        step=run_steps(entries, marker.run_id),
    )


def _append(store: SessionStore, session_id: str, marker: RunMarker) -> None:
    try:
        store.append(session_id, marker_entry(marker))
    except Exception:
        logger.exception(
            "the recovered marker for session %s could not be written",
            session_id,
        )
