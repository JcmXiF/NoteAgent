"""Continue a session when its task executor crosses an op boundary.

A tool gives its op a bounded wait and then hands the turn back. When the
executor later finishes that op, this resumer brings the conversation back with
the op's bounded delta: it claims the session's one run slot, sets the op's
once-only ``resume_started`` marker under the task's writer lock, and starts a
``TaskOpResume`` run on the same session id.

Two rules shape the timing:

* a session may only have one run. If the original turn is still finishing, the
  resumer queues behind it and tries again when the slot is free. The marker is
  not set while it waits, so a restart still sees the completion as pending;
* the marker is set before the run, never after. A duplicate completion, a
  second app instance or a reconnect therefore starts nothing new. If the run
  cannot start at all (the session was deleted), the op records why and is not
  retried.
"""

from __future__ import annotations

import logging
import threading
import time

from noteagent.agent.runtime import BusyError, TaskOpResume
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_runtime
from noteagent.api.task import task_service_for
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.task.service import OpResumeNotice, TaskOpResumeFn

logger = logging.getLogger(__name__)

#: How often a queued completion looks for the session's free run slot. It is a
#: boundary between two local events, not a network retry.
DEFAULT_RESUME_POLL_S = 0.02


class TaskOpResumer:
    """The production op-completion callback: claim a session, continue it."""

    def __init__(
        self,
        deps: AppDeps,
        layout: ProjectLayout,
        *,
        poll_s: float = DEFAULT_RESUME_POLL_S,
    ) -> None:
        self._deps = deps
        self._layout = layout
        self._poll_s = max(0.001, poll_s)

    def __call__(self, notice: OpResumeNotice) -> None:
        if not notice.session_id:
            # A person's pick has no conversation to resume; the panel reads
            # the task by itself.
            return
        self._try_start(notice)

    def _try_start(self, notice: OpResumeNotice) -> None:
        try:
            claim = self._deps.runs.claim(notice.session_id)
        except BusyError:
            # The turn that handed the op back is still running. Wait for its
            # slot rather than starting a second loop beside it.
            self._queue(notice)
            return
        self._start_claimed(notice, claim)

    def _start_claimed(self, notice: OpResumeNotice, claim: threading.Event) -> None:
        """Set the once-only marker, then start the continuation."""
        try:
            claimed = self._service().claim_resume(notice.op_id)
        except Exception as err:  # noqa: BLE001 - recorded on the op
            self._deps.runs.release(notice.session_id)
            self._record_error(notice.op_id, err)
            return
        if claimed is None:
            # Already resumed, cancelled, or no longer asking: nothing to start.
            self._deps.runs.release(notice.session_id)
            return
        try:
            self._spawn(lambda: self._run(claimed, claim))
        except Exception as err:  # noqa: BLE001 - the run never started
            self._deps.runs.release(claimed.session_id)
            self._record_error(claimed.op_id, err)

    def _run(self, notice: OpResumeNotice, claim: threading.Event) -> None:
        """Run the continuation on the session; the kernel releases the slot."""
        try:
            runtime = session_runtime(self._deps, self._store(), self._layout)
            runtime.execute(
                TaskOpResume(
                    session_id=notice.session_id,
                    op_id=notice.op_id,
                    kind=notice.kind,
                    status=notice.status,
                    rev=notice.rev,
                    result=notice.result,
                    claim=claim,
                ),
                lambda _event: None,
            )
        except Exception as err:  # noqa: BLE001 - named on the op, not raised
            # ``release`` is idempotent: the kernel has already released unless
            # it failed before it could use the slot.
            self._deps.runs.release(notice.session_id)
            logger.exception(
                "the resume run for op %s failed", notice.op_id
            )
            self._record_error(notice.op_id, err)

    def _queue(self, notice: OpResumeNotice) -> None:
        """Wait for the session's current run to end, off every caller thread."""
        threading.Thread(
            target=self._wait_for_slot,
            args=(notice,),
            name="task-op-resume",
            daemon=True,
        ).start()

    def _wait_for_slot(self, notice: OpResumeNotice) -> None:
        while True:
            try:
                if not self._service().resume_pending(notice.op_id):
                    # Finished, cancelled or already claimed elsewhere.
                    return
            except Exception:  # noqa: BLE001 - a task that cannot be read
                logger.exception(
                    "the pending resume for op %s cannot be read",
                    notice.op_id,
                )
                return
            try:
                claim = self._deps.runs.claim(notice.session_id)
            except BusyError:
                time.sleep(self._poll_s)
                continue
            self._start_claimed(notice, claim)
            return

    def _spawn(self, work) -> None:
        """Start the run; a test can make this synchronous."""
        spawn = self._deps.task_spawn
        if spawn is not None:
            spawn(work)
            return
        threading.Thread(
            target=work, name="task-op-resume-run", daemon=True
        ).start()

    def _service(self):
        # Built per call, like every other per-project service: the active
        # project can change, and the service is a thin view over its file.
        return task_service_for(self._deps, self._layout, on_resume=None)

    def _store(self):
        if self._deps.sessions is not None:
            return self._deps.sessions
        return JsonlSessionStore(
            self._layout, clock=self._deps.clock, ids=self._deps.ids
        )

    def _record_error(self, op_id: str, err: Exception) -> None:
        message = f"{type(err).__name__}: {err}"
        try:
            self._service().record_resume_error(op_id, message)
        except Exception:  # noqa: BLE001 - the error is already logged
            logger.exception(
                "the resume failure for op %s could not be recorded", op_id
            )


def task_op_resume_for(
    deps: AppDeps, layout: ProjectLayout
) -> TaskOpResumeFn:
    """The op-completion callback one project's task service calls."""
    return TaskOpResumer(deps, layout, poll_s=deps.task_resume_poll_s)


__all__ = [
    "DEFAULT_RESUME_POLL_S",
    "TaskOpResumer",
    "task_op_resume_for",
]
