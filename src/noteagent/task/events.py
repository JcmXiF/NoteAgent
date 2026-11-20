"""The writing task's change stream: an in-process fan-out of revisions.

A Task write is an invalidation, not a second copy of the task. The writer
publishes the revision it saved; a subscriber (the SSE route) forwards it; the
client reads the one ``overview`` snapshot again. Nothing about the task's
content travels here, and nothing is replayed: a client that was away reads the
task once when it connects and is current.

The fan-out is per project, in this process only. The core is single-process and
local; a second process would need a durable stream, which this is deliberately
not.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field


class TaskEvents:
    """The in-process broker behind ``GET /api/v1/task/events``.

    Each subscriber gets its own bounded queue. A subscriber that falls behind
    keeps its oldest slot for the newest revision: the only correct answer to an
    invalidation is the task as it stands, so intermediate revisions may be
    coalesced without losing the fact that the task moved.
    """

    def __init__(self, *, max_pending: int = 8) -> None:
        if max_pending < 1:
            raise ValueError("a subscriber queue holds at least one revision")
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[TaskSubscription]] = {}

    def subscribe(self, project_key: str) -> "TaskSubscription":
        """Listen for this project's revisions until the subscription closes."""
        subscription = TaskSubscription(self, project_key, self._max_pending)
        with self._lock:
            self._subscribers.setdefault(project_key, set()).add(subscription)
        return subscription

    def publish(self, project_key: str, rev: int) -> None:
        """Tell every subscriber that the task is now at *rev*."""
        with self._lock:
            subscribers = tuple(self._subscribers.get(project_key, ()))
        for subscription in subscribers:
            subscription.offer(rev)

    def subscriber_count(self, project_key: str) -> int:
        """How many streams are listening; a disconnected one is removed."""
        with self._lock:
            return len(self._subscribers.get(project_key, ()))

    def _remove(self, subscription: "TaskSubscription") -> None:
        with self._lock:
            listeners = self._subscribers.get(subscription.project_key)
            if listeners is None:
                return
            listeners.discard(subscription)
            if not listeners:
                self._subscribers.pop(subscription.project_key, None)


@dataclass(slots=True, eq=False)
class TaskSubscription:
    """One listener's bounded queue of revisions.

    Identity, not fields, is what the broker keys its subscribers by: two
    subscriptions with the same project are still two streams.
    """

    events: TaskEvents
    project_key: str
    max_pending: int = 8
    _queue: queue.Queue[int] = field(default_factory=queue.Queue)
    _closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        # The dataclass default cannot see ``max_pending``; size the queue here.
        self._queue = queue.Queue(maxsize=self.max_pending)

    def get(self, timeout: float) -> int:
        """The next revision, or raise ``queue.Empty`` after *timeout*."""
        return self._queue.get(timeout=timeout)

    def offer(self, rev: int) -> None:
        """Add a revision, keeping the newest one when the queue is full."""
        with self._lock:
            if self._closed:
                return
        try:
            self._queue.put_nowait(rev)
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(rev)
            except queue.Full:
                # Another consumer took the slot; the newest revision is still
                # what the next read observes from the task itself.
                pass

    def close(self) -> None:
        """Unregister the listener; a closed queue is never offered again."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.events._remove(self)


__all__ = ["TaskEvents", "TaskSubscription"]
