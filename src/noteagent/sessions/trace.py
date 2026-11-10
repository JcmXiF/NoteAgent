"""What a run did, written beside the session, never what it said.

The transcript is the conversation: prompts, answers, tool arguments and tool
output are its content, and it is meant to be read. The trace answers a
different question — which steps ran, which tools were touched, where a run
stopped — for support, evals and a future trace viewer. Keeping the two apart is
what lets the trace be copied into a bug report: it carries nothing a person
wrote or the model generated.

The safe fields are chosen here, one event type at a time, rather than by
removing keys from a payload. A new field on an event then cannot reach the
trace by accident.

The vocabulary is the runtime-state document's, reduced to what an event sink
can see without reaching into the loop: ``run_started``, ``status``,
``tool_result``, ``wait_*``, ``job_result``, ``run_finished``, ``run_failed``
and ``run_cancelled``. Turn and model-call events need taps inside the loop and
are left for the trace viewer work.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping

from noteagent.agent.clock import Clock, SystemClock, iso_timestamp
from noteagent.agent.events import (
    DoneEvent,
    ErrorEvent,
    JobResultEvent,
    ObservationEvent,
    RunEvent,
    StatusEvent,
    WaitEvent,
)
from noteagent.agent.runtime import RunRef
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.common import SESSION_LOCK
from noteagent.util.redact import environment_secrets, redact_value

logger = logging.getLogger(__name__)

TRACE_VERSION = 1

#: A tool name is model output until the registry proves otherwise. Only a name
#: that could be a registered tool is written down; anything else is "unknown".
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: The wait statuses that name a lifecycle event in the trace.
_WAIT_STATUSES = frozenset({"proposed", "entered", "completed", "cancelled"})


class SessionTrace:
    """A session's ``.events.jsonl``: append-only, best effort, content-free."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._layout = layout
        self._clock = clock or SystemClock()

    def started(self, run: RunRef) -> None:
        self._append(
            run.session_id, {"type": "run_started", "runId": run.run_id}
        )

    def record(self, run: RunRef, event: RunEvent) -> None:
        record = _safe_record(event)
        if record is None:
            # Token deltas are ephemeral, and an event this module does not know
            # has no vetted fields; neither may be written blind.
            return
        record["runId"] = run.run_id
        self._append(run.session_id, record)

    def _append(self, session_id: str, record: dict[str, object]) -> None:
        line = {
            "v": TRACE_VERSION,
            "ts": iso_timestamp(self._clock),
            "sessionId": session_id,
            **record,
        }
        # Nothing here is meant to hold a secret; redaction is the second lock,
        # for a future field that accidentally does.
        line = redact_value(line, environment_secrets())
        path = self._layout.session_events_path(session_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with SESSION_LOCK:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(
                        json.dumps(
                            line, ensure_ascii=False, separators=(",", ":")
                        )
                        + "\n"
                    )
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as err:
            # A trace is a diagnostic, not a condition of the run.
            logger.warning("session trace cannot be written: %s", err)


def _safe_record(event: RunEvent) -> dict[str, object] | None:
    """The fields of *event* that may be written down, or ``None`` for none."""
    if isinstance(event, StatusEvent):
        return {"type": "status", "step": event.step_id or ""}
    if isinstance(event, ObservationEvent):
        return _tool_record(event.observation)
    if isinstance(event, WaitEvent):
        status = event.wait.status
        kind = f"wait_{status}" if status in _WAIT_STATUSES else "wait"
        return {
            "type": kind,
            "waitId": event.wait.wait_id,
            "job": event.wait.job,
            "status": status,
        }
    if isinstance(event, JobResultEvent):
        return {
            "type": "job_result",
            "waitId": event.wait_id,
            "status": event.result.status,
            "artifacts": len(event.result.artifacts),
        }
    if isinstance(event, ErrorEvent):
        # An error ends the run in this kernel, so it is also the run's terminal
        # record. Only the code is written; the sentence can quote a provider.
        failed = event.code != "cancelled"
        return {
            "type": "run_failed" if failed else "run_cancelled",
            "code": event.code,
        }
    if isinstance(event, DoneEvent):
        run = event.run if isinstance(event.run, Mapping) else {}
        return {"type": "run_finished", "status": str(run.get("status") or "")}
    return None


def _tool_record(observation: Mapping[str, object]) -> dict[str, object]:
    """A tool call's card, reduced to what touched the project and why.

    The card the interface shows also carries the page title, slug, URL or query
    it was about. Those are the reader's material, so the trace keeps the tool's
    name, its diagnostic codes, and whether the call failed — no more. The name
    itself came from the model, so it is written only when it could name a tool.
    """
    name = str(observation.get("tool") or "")
    record: dict[str, object] = {
        "type": "tool_result",
        "tool": name if _TOOL_NAME.fullmatch(name) else "unknown",
    }
    # A diagnostic code names a condition (``truncated``), not the material it
    # happened to. A handler that put text there is not written down either.
    codes = [
        str(item["code"])
        for item in observation.get("diagnostics") or ()
        if isinstance(item, Mapping)
        and isinstance(item.get("code"), str)
        and _TOOL_NAME.fullmatch(item["code"])
    ]
    if codes:
        record["codes"] = codes
    for flag in ("missing", "timeout", "error"):
        if observation.get(flag):
            record[flag] = True
    return record


__all__ = ["SessionTrace", "TRACE_VERSION"]
