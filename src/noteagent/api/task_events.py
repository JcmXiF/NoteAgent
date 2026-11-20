"""The writing task's change stream, as server-sent events.

A Task write publishes a revision; this stream forwards it. The event carries
``{rev}`` and nothing else: it is an invalidation, and the client answers it by
reading the one ``overview`` snapshot. The stream starts with the revision as it
stands, so a client that just connected already has a snapshot to read; it keeps
nothing back, so there is no replay and no ``Last-Event-ID``.

The stream observes the task; it does not own the write. A client that goes away
is unsubscribed, and the next connection starts from the task as it stands.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Callable, Iterator
from typing import Any

from noteagent.api.sse import KEEPALIVE_SECONDS, sse_error_payload
from noteagent.task.events import TaskEvents


def iter_task_events(
    events: TaskEvents,
    project_key: str,
    current_rev: Callable[[], int],
) -> Iterator[bytes]:
    """The task's revisions, from the current one until the client leaves.

    Subscribing before reading the current revision closes the gap a write could
    fall into: anything saved after the subscription is queued after the initial
    event, so a client never hears about an old revision last.
    """
    subscription = events.subscribe(project_key)
    try:
        try:
            rev = current_rev()
        except Exception as err:  # noqa: BLE001 - the stream names its refusal
            yield _frame("error", sse_error_payload(err))
            return
        yield _task_frame(rev)
        while True:
            try:
                rev = subscription.get(KEEPALIVE_SECONDS)
            except queue.Empty:
                yield b": keepalive\n\n"
                continue
            yield _task_frame(rev)
    finally:
        subscription.close()


def _task_frame(rev: int) -> bytes:
    return _frame("task", {"rev": rev})


def _frame(kind: str, data: dict[str, Any]) -> bytes:
    blob = json.dumps(data, ensure_ascii=False)
    return f"event: {kind}\ndata: {blob}\n\n".encode("utf-8")


__all__ = ["iter_task_events"]
