"""A job a chat session is waiting on, wrapped in its wait lifecycle.

The job belongs to the interface that started it; this module only records the
result the chat needs, turns the job's payload into a typed ``JobResult``, and
starts the resume run once. The work itself stays in the endpoint that owns it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from noteagent.agent.events import JobResultEvent
from noteagent.agent.jobs import JobResult
from noteagent.agent.store import WaitStore
from noteagent.api.sse import EmitFn, WorkerFn, iter_job_sse
from noteagent.util.cancel import JobCancelled

logger = logging.getLogger(__name__)

ResultFromPayload = Callable[[dict[str, Any]], JobResult]
Finished = Callable[[], None]


def iter_waited_job_sse(
    worker: WorkerFn,
    *,
    waits: WaitStore,
    session_id: str,
    wait_id: str,
    job: str,
    result_from_payload: ResultFromPayload,
    on_finished: Finished | None = None,
    default_step_id: str | None = None,
) -> Iterator[bytes]:
    """Run a job and record its outcome as the wait's completion.

    A failed job is still a completion: the chat resumes with the failure rather
    than leaving the wait entered forever. A client that disconnects cancels the
    job, which is recorded as cancelled, not as a silent success.
    """

    def wrapped(
        on_status, cancel, emit: EmitFn
    ) -> dict[str, Any]:
        try:
            payload = worker(on_status, cancel, emit)
        except Exception as err:
            _settle(waits, session_id, wait_id, _failed(job, err), emit)
            _finished(on_finished)
            raise
        try:
            result = result_from_payload(payload)
        except Exception as err:
            _settle(waits, session_id, wait_id, _failed(job, err), emit)
            _finished(on_finished)
            raise
        _settle(waits, session_id, wait_id, result, emit)
        _finished(on_finished)
        return payload

    return iter_job_sse(wrapped, default_step_id=default_step_id)


def _settle(
    waits: WaitStore,
    session_id: str,
    wait_id: str,
    result: JobResult,
    emit: EmitFn,
) -> None:
    """Record and announce a result without letting bookkeeping fail the job.

    The job belongs to its own interface. If the session was deleted while it
    ran, or its log cannot be written, the job still finished and its stream
    must still say so; the lost continuation is the smaller failure.
    """
    try:
        waits.finish(session_id, wait_id, result)
    except Exception:
        logger.exception(
            "the job for wait %s finished but could not be recorded", wait_id
        )
    try:
        emit("job_result", JobResultEvent(wait_id, result).payload())
    except Exception:
        logger.exception("the result of wait %s could not be announced", wait_id)


def _failed(job: str, err: Exception) -> JobResult:
    return JobResult(
        job=job,
        status="cancelled" if isinstance(err, JobCancelled) else "failed",
        summary="",
        # A job error is a sentence for the log and the model; it is bounded
        # because a traceback or a provider body must not grow the transcript
        # without limit.
        error=(str(err) or type(err).__name__)[:500],
    )


def _finished(callback: Finished | None) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception:
        # The result is already committed; a failure to start the continuation
        # is reported in the log and can be reconciled when the session opens.
        logger.exception("the job completed but its resume could not be started")
