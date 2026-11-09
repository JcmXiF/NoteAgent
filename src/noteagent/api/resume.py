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

from noteagent.agent.runtime import BusyError, OnBusy, Resume
from noteagent.agent.store import SessionStore
from noteagent.agent.wait_store import has_resumed
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_runtime, session_waits
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
