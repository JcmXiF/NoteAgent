"""Continuing a session after the job it waited on came back.

The completion endpoint records the result and calls ``start_resume``. The
continuation runs in the background: the interface that finished the job should
not wait for a model answer, and a person's question may already hold the
session's single run. When a completion was missed, ``reconcile_session`` asks
the job system what happened and closes the wait the same way.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from noteagent.agent.jobs import JobResult
from noteagent.agent.runtime import BusyError, OnBusy, Resume
from noteagent.agent.store import SessionStore
from noteagent.agent.wait_store import has_resumed
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import JobWait, session_runtime, session_waits
from noteagent.project.layout import ProjectLayout

logger = logging.getLogger(__name__)

#: How long a completion waits for the session's current run to end. A wait is
#: not a run, so there is normally nothing to wait for; this bounds the case
#: where a person asked something at the same moment.
RESUME_WAIT_SECONDS = 60.0

#: How often the continuation looks again while the session is busy.
RETRY_SECONDS = 0.05


def start_resume(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
    session_id: str,
    wait_id: str,
) -> None:
    """Start the session's continuation without blocking the caller."""
    try:
        runtime = session_runtime(deps, store, layout)
    except Exception:
        logger.exception(
            "the resume for session %s could not be started", session_id
        )
        return

    request = Resume(session_id, wait_id)

    def work() -> None:
        deadline = time.monotonic() + RESUME_WAIT_SECONDS
        while True:
            try:
                runtime.execute(request, lambda _event: None)
                return
            except BusyError:
                if request.on_busy is not OnBusy.QUEUE:
                    return
                if time.monotonic() >= deadline:
                    # The session stayed busy; opening it again reconciles the
                    # completed wait and starts the continuation then.
                    logger.warning(
                        "the resume for session %s waited too long",
                        session_id,
                    )
                    return
                time.sleep(RETRY_SECONDS)
            except Exception:
                logger.exception(
                    "the resume for session %s failed", session_id
                )
                return

    threading.Thread(
        target=work,
        name=f"resume:{session_id}",
        daemon=True,
    ).start()


def job_completion(
    deps: AppDeps,
    job_wait: JobWait,
    layout: ProjectLayout | None,
    job_id: str,
) -> Callable[[], None]:
    """What a finished waited-on job does: continue the chat, release the gate.

    The release is in a ``finally`` because a collaboration job that has ended
    must never keep the next one waiting, even if the continuation cannot start.
    """

    def finished() -> None:
        try:
            start_resume(
                deps,
                job_wait.store,
                layout,
                job_wait.session_id,
                job_wait.wait_id,
            )
        finally:
            deps.job_gate.release(job_id)

    return finished


def job_cleanup(
    deps: AppDeps,
    job_wait: JobWait | None,
    layout: ProjectLayout | None,
    job_id: str,
    job: str,
) -> None:
    """Release the gate when a job stream ends, however it ends.

    A normal completion already released it through ``job_completion``. This
    runs as the response's background task, so a reader that disconnects before
    the stream is read cannot leave the collaboration slot held. If the job
    never got to finish, the wait is closed as failed so the chat is not left
    waiting on a job that will never report.
    """
    try:
        if job_wait is not None:
            wait = job_wait.waits.current(job_wait.session_id)
            if (
                wait is not None
                and wait.wait_id == job_wait.wait_id
                and wait.status == "entered"
            ):
                job_wait.waits.finish(
                    job_wait.session_id,
                    job_wait.wait_id,
                    JobResult(
                        job=job,
                        status="failed",
                        summary="",
                        error="the job stream ended before it finished",
                    ),
                )
                start_resume(
                    deps,
                    job_wait.store,
                    layout,
                    job_wait.session_id,
                    job_wait.wait_id,
                )
    except Exception:
        logger.exception("the wait for job %s could not be closed", job_id)
    finally:
        deps.job_gate.release(job_id)


def reconcile_session(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
    session_id: str,
) -> bool:
    """Close a finished wait, and start the continuation if it was lost.

    A job that is still running, a wait that was never entered, or a wait the
    human stopped stays as it is. A completed or failed job whose continuation
    never started is resumed here: that is what recovers a resume that arrived
    while the session stayed busy, or one whose runtime could not be built.
    """
    waits = session_waits(deps, store)
    wait = waits.current(session_id)
    if wait is None:
        return False

    if wait.status == "entered" and wait.job_id:
        if deps.jobs is None:
            return False
        try:
            result = deps.jobs.get(wait.job_id)
        except Exception:
            logger.exception(
                "the job %s for session %s could not be read",
                wait.job_id,
                session_id,
            )
            return False
        if result is None:
            return False
        try:
            waits.finish(session_id, wait.wait_id, result)
        except Exception:
            logger.exception(
                "the job %s for session %s could not be recorded",
                wait.job_id,
                session_id,
            )
            return False
        wait = waits.current(session_id) or wait

    finished = wait.status in ("completed", "failed") or (
        wait.status == "cancelled" and bool(wait.job_id)
    )
    if not finished:
        return False
    if has_resumed(store.load(session_id).entries, wait.wait_id):
        return False
    start_resume(deps, store, layout, session_id, wait.wait_id)
    return True
