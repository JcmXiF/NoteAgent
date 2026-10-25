"""One server-sent-event reader for every long job.

Every streaming endpoint hands its work to this module: a queue carries what the
job reports, a thread runs the job, a keep-alive goes out while nothing is
happening, and the errors a job may raise become an ``error`` event rather than a
failed response.

A job runs in a thread because the work is synchronous and the response has to
keep flushing: the generator is read by the server, so a job that blocked the
generator would stop the keep-alives too. When the client goes away the generator
is closed, which sets the cancel flag the job checks between rounds.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from noteagent.llm.client import LlmError
from noteagent.llm.config import LlmConfigError
from noteagent.util.cancel import JobCancelled
from noteagent.util.refusal import CodedError

#: Errors a job may raise that the reader is expected to understand, turned into an
#: ``error`` event rather than a failed request. Anything else is a defect and is
#: left to surface as one.
JOB_ERRORS: tuple[type[BaseException], ...] = (
    JobCancelled,
    LlmConfigError,
    FileNotFoundError,
    ValueError,
    LlmError,
    RuntimeError,
    OSError,
    CodedError,
)

#: How long the reader waits for something to say before reassuring the connection.
KEEPALIVE_SECONDS = 15

StatusFn = Callable[..., None]
EmitFn = Callable[[str, dict[str, Any]], None]
WorkerFn = Callable[[StatusFn, threading.Event, EmitFn], dict[str, Any]]


def sse_status_payload(message: str, step_id: str | None = None) -> dict[str, object]:
    """The body of a ``status`` event. A step id is only sent when there is one."""
    payload: dict[str, object] = {"message": message}
    if step_id:
        payload["stepId"] = step_id
    return payload


def sse_error_payload(err: BaseException) -> dict[str, object]:
    """The body of an ``error`` event.

    The message is what the log and the command line show; when the core named the
    situation, the code and its figures travel beside it so the interface can say
    the same thing in the reader's language.
    """
    payload: dict[str, object] = {"detail": str(err)}
    code = getattr(err, "code", "")
    if code:
        payload["code"] = code
        params = getattr(err, "params", None)
        if params:
            payload["params"] = dict(params)
    return payload


def iter_job_sse(
    worker: WorkerFn,
    *,
    default_step_id: str | None = None,
) -> Iterator[bytes]:
    """
    Runs ``worker`` and streams what it reports.

    The worker is called with a status reporter, a cancel flag and an emitter for
    events of its own — a chat answer sends its tokens through the third, which is
    the one thing the shape does not already cover.

    Emits ``status`` while it runs, then ``done`` with the payload or ``error``
    with the reason.
    """
    events: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
    cancel = threading.Event()

    def on_status(message: str, step_id: str | None = None, **_extra: object) -> None:
        events.put(("status", sse_status_payload(message, step_id or default_step_id)))

    def emit(kind: str, data: dict[str, Any]) -> None:
        events.put((kind, data))

    def run() -> None:
        try:
            events.put(("done", worker(on_status, cancel, emit)))
        except JOB_ERRORS as err:
            events.put(("error", sse_error_payload(err)))
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()
    try:
        while True:
            try:
                item = events.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                yield b": keepalive\n\n"
                continue
            if item is None:
                break
            kind, data = item
            blob = json.dumps(data, ensure_ascii=False)
            yield f"event: {kind}\ndata: {blob}\n\n".encode("utf-8")
    finally:
        # The client has gone, or the job is over. Either way, a job still running
        # is told to stop before whatever it does next.
        cancel.set()


__all__ = [
    "iter_job_sse",
    "sse_error_payload",
    "sse_status_payload",
    "JOB_ERRORS",
    "KEEPALIVE_SECONDS",
]
