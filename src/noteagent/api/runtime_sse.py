"""A run as server-sent events: the kernel's events, forwarded as they happen.

The run is synchronous and the response must keep flushing, so it runs in a
thread and its sink writes into a bounded queue. The reader forwards each event
under its own name with a sequence number; ``done`` is filled with the session
the API can show, because the runtime only names the session it finished.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator

from noteagent.agent.events import DoneEvent, ErrorEvent, RunEvent, is_terminal
from noteagent.agent.runtime import (
    AgentRuntime,
    BusyError,
    RunCancelled,
    RunRequest,
)
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.api.sessions import session_payload
from noteagent.api.sse import KEEPALIVE_SECONDS

#: How many events may wait for the reader before the model is made to wait.
#: The reader is local and fast, so this is backpressure, not a loss policy.
QUEUE_SIZE = 512


def iter_run_sse(
    runtime: AgentRuntime,
    request: RunRequest,
    *,
    store: SessionStore,
    waits: WaitStore | None = None,
) -> Iterator[bytes]:
    """Run *request*, forwarding status/token/observation/done/error."""
    events: queue.Queue[RunEvent | None] = queue.Queue(maxsize=QUEUE_SIZE)
    stop = threading.Event()
    #: Set once the run behind this stream has reported something. Only a run
    #: that claimed the session reaches the caller's sink, so this is the
    #: evidence that a disconnect may cancel it without reaching someone else.
    owns_run = threading.Event()
    terminated = False

    def put(item: RunEvent | None) -> None:
        # A reader that has gone away must not leave the producer blocked on a
        # full queue; the stop event is the way out.
        while not stop.is_set():
            try:
                events.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def sink(event: RunEvent) -> None:
        nonlocal terminated
        owns_run.set()
        if is_terminal(event):
            terminated = True
        put(event)

    def run() -> None:
        nonlocal terminated
        try:
            runtime.execute(request, sink)
            if not terminated:
                sink(ErrorEvent("internal", "the run ended without a terminal event"))
        except BusyError as err:
            # The refusal did not come from the session's run, so this stream
            # must not claim its ownership by reporting through the sink.
            terminated = True
            put(ErrorEvent("busy", str(err), retryable=True))
        except RunCancelled:
            put(ErrorEvent("cancelled", "the run was cancelled", retryable=True))
        except Exception as err:
            put(ErrorEvent("internal", str(err)))
        finally:
            put(None)

    threading.Thread(target=run, daemon=True).start()
    seq = 0
    try:
        while True:
            try:
                item = events.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                yield b": keepalive\n\n"
                continue
            if item is None:
                break
            if isinstance(item, DoneEvent):
                try:
                    session = session_payload(
                        store, request.session_id, waits=waits
                    )
                except Exception as err:
                    seq += 1
                    yield _block(
                        ErrorEvent(
                            "internal",
                            f"the session could not be read after the run: {err}",
                        ),
                        seq,
                    )
                    break
                seq += 1
                yield _block(DoneEvent(session, item.run), seq)
                break
            seq += 1
            yield _block(item, seq)
            if isinstance(item, ErrorEvent):
                break
    finally:
        # A reader that went away leaves a run still writing: tell it to stop
        # before its next model call. A stream that never owned the session (a
        # busy refusal, or one that ended on a terminal event) must not cancel
        # the run that does own it, nor the one that may own it next.
        stop.set()
        if owns_run.is_set() and not terminated:
            runtime.cancel(request.session_id)


def _block(event: RunEvent, seq: int) -> bytes:
    payload = event.payload()
    payload["seq"] = seq
    blob = json.dumps(payload, ensure_ascii=False)
    return f"event: {event.kind}\ndata: {blob}\n\n".encode("utf-8")
