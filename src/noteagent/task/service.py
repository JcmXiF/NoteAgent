"""The task's single writer, and the bounded wait a tool puts around it.

A tool never applies a node's answer to the task itself. It writes a typed op,
the executor claims it, calls the pure node outside the task lock, and writes
the answer back. The tool then waits a bounded time for that boundary: a fast
search answers with its cards, a slow one answers ``requested``/``running`` and
the executor finishes it after the conversation has moved on.

The session-aware part is small on purpose. Each tool result carries the task
revision the run has seen; the next mutation names that revision, so a change
made in between is a ``conflict`` rather than a silent overwrite.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from threading import Event, Lock
from typing import Iterator

from noteagent.agent.clock import Clock, Ids, iso_timestamp
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import ToolError
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import (
    ACTIVE_OP_STATUSES,
    Card,
    Op,
    SearchCard,
    SearchFn,
    Task,
    task_view,
)
from noteagent.task.store import TaskStore

#: How long a tool waits for its op before it says requested/running.
DEFAULT_WAIT_S = 15.0

#: How often the wait looks for the executor's write. It is a local file, so
#: this is a poll between two boundaries, not a network retry.
DEFAULT_POLL_S = 0.02


class TaskToolError(ToolError):
    """A task refusal the model is told about through the tool error channel."""


class TaskConflict(TaskToolError):
    """The caller's revision is not the task's current revision."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict")


class TaskOpFailed(TaskToolError):
    """The node behind an op failed; the task itself is still readable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="op_failed")


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """What a tool gets back from a search: cards, or where the op stands."""

    op_id: str
    query: str
    status: str
    rev: int
    cards: tuple[Card, ...] = ()


class TaskRegistry:
    """The task's process-wide single writer and what each session has seen.

    A service is built per request, like the pipeline's, so anything that must
    survive between two calls lives here rather than on the service. The writer
    lock serialises every read-modify-write of the task file in this process;
    the task's own lock file covers the processes.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._writers: dict[str, Lock] = {}
        self._observed: dict[tuple[str, str], int] = {}

    def writer(self, project_key: str) -> Lock:
        with self._lock:
            return self._writers.setdefault(project_key, Lock())

    def observe(self, project_key: str, session_id: str, rev: int) -> None:
        """Remember the revision a session's last tool result carried."""
        if not session_id:
            return
        with self._lock:
            self._observed[(project_key, session_id)] = rev

    def observed(self, project_key: str, session_id: str) -> int | None:
        with self._lock:
            return self._observed.get((project_key, session_id))


class TaskService:
    """One project's task: inspect it, request a search, and wait at the edge."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        clock: Clock,
        ids: Ids,
        search: SearchFn,
        registry: TaskRegistry | None = None,
        lock: ProjectLock | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        wait_s: float = DEFAULT_WAIT_S,
        poll_s: float = DEFAULT_POLL_S,
    ) -> None:
        if wait_s < 0:
            raise ValueError("a bounded wait cannot be negative")
        self._layout = layout
        self._clock = clock
        self._ids = ids
        self._search = search
        self._registry = registry or TaskRegistry()
        self._lock = lock or ProjectLock(layout.task_lock, clock=clock)
        self._spawn = spawn or _spawn_thread
        self._wait_s = wait_s
        self._poll_s = poll_s
        self._project_key = str(layout.root.resolve())
        self._owner = f"task-{id(self)}"
        self._store = TaskStore(layout)

    @property
    def wait_s(self) -> float:
        """How long a tool waits before it reports the op as still running."""
        return self._wait_s

    def inspect(
        self, view: str, *, session_id: str = ""
    ) -> tuple[dict[str, object], int]:
        """A bounded view and its rev; a missing task is made on first read."""
        if view not in ("summary", "cards"):
            raise ValueError(f"unknown task view: {view!r}")
        task = self._store.load()
        if task is None:
            with self._writing():
                task = self._store.load()
                if task is None:
                    task = self._create_locked()
        self._registry.observe(self._project_key, session_id, task.rev)
        return task_view(task, view), task.rev

    def search(
        self,
        query: str,
        *,
        session_id: str = "",
        cancel: Event | None = None,
    ) -> SearchOutcome:
        """Write a ``search`` op, run the node behind it, and wait at most N s.

        The op is accepted only at the revision this session last saw. A stale
        observation is refused before anything is written.
        """
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ValueError("a search needs a query")
        expected = self._registry.observed(self._project_key, session_id)
        op_id = self._ids.new_id("op")
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._store.load() or self._create_locked()
            expected = task.rev if expected is None else expected
            if expected != task.rev:
                raise TaskConflict(
                    "the writing task changed since this session last saw it "
                    f"(expected rev {expected}, now rev {task.rev}). "
                    "Do not retry blindly; call inspect and decide again."
                )
            op = Op(
                id=op_id,
                kind="search",
                actor="agent",
                status="requested",
                input={"query": cleaned},
                expected_rev=expected,
                created_at=now,
                updated_at=now,
            )
            self._store.save(
                replace(task, ops=task.ops + (op,), updated_at=now)
            )
            self._registry.observe(self._project_key, session_id, expected)
        self._spawn(lambda: self._run_search(op_id, cleaned))
        outcome = self._wait(op_id, cleaned, cancel)
        # A finished search moved the task; the session now knows that revision.
        self._registry.observe(self._project_key, session_id, outcome.rev)
        return outcome

    def _run_search(self, op_id: str, query: str) -> None:
        """The executor's side: claim, call the node, write the answer back."""
        try:
            if not self._claim(op_id):
                return
            try:
                cards = list(self._search(query))
            except Exception as err:  # noqa: BLE001 - the op records the failure
                self._finish_failed(op_id, f"{type(err).__name__}: {err}")
                return
            self._finish_done(op_id, cards)
        except Exception as err:  # noqa: BLE001 - a writer error must not vanish
            try:
                self._finish_failed(op_id, f"{type(err).__name__}: {err}")
            except Exception:
                pass

    def _claim(self, op_id: str) -> bool:
        """Move the op to running; false when it is no longer claimable."""
        with self._writing():
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return False
            now = iso_timestamp(self._clock)
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(
                        task, replace(op, status="running", updated_at=now)
                    ),
                    updated_at=now,
                )
            )
            return True

    def _finish_done(self, op_id: str, cards: list[SearchCard]) -> None:
        with self._writing():
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return
            now = iso_timestamp(self._clock)
            new_cards = tuple(
                Card(
                    id=self._ids.new_id("card"),
                    title=_text(card.title) or card.url,
                    url=_text(card.url),
                    snippet=_text(card.snippet),
                    site=_text(card.site),
                )
                for card in cards
                if _text(card.url)
            )
            done = replace(
                op,
                status="done",
                result={
                    "hits": len(new_cards),
                    "cardIds": [card.id for card in new_cards],
                },
                updated_at=now,
            )
            self._store.save(
                replace(
                    task,
                    cards=task.cards + new_cards,
                    ops=_replace_op(task, done),
                    rev=task.rev + 1,
                    updated_at=now,
                )
            )

    def _finish_failed(self, op_id: str, error: str) -> None:
        with self._writing():
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return
            now = iso_timestamp(self._clock)
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(
                        task,
                        replace(
                            op,
                            status="failed",
                            error=error[:1000],
                            updated_at=now,
                        ),
                    ),
                    updated_at=now,
                )
            )

    def _wait(
        self, op_id: str, query: str, cancel: Event | None
    ) -> SearchOutcome:
        deadline = time.monotonic() + self._wait_s
        status = "requested"
        rev = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise RunCancelled("the run was cancelled")
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is not None:
                rev = task.rev
            if op is not None:
                status = op.status
            if op is not None and op.status == "failed":
                raise TaskOpFailed(
                    "the web search could not finish: "
                    f"{op.error or 'the search node failed'}. "
                    "Do not retry blindly; call inspect before deciding "
                    "whether to search again."
                )
            if op is not None and op.status == "done":
                ids = op.result.get("cardIds")
                wanted = (
                    {item for item in ids if isinstance(item, str)}
                    if isinstance(ids, list)
                    else set()
                )
                cards = (
                    tuple(card for card in task.cards if card.id in wanted)
                    if task is not None
                    else ()
                )
                return SearchOutcome(op_id, query, "done", rev, cards)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return SearchOutcome(op_id, query, status, rev)
            time.sleep(min(self._poll_s, remaining))

    def _create_locked(self) -> Task:
        now = iso_timestamp(self._clock)
        task = Task(
            id=self._ids.new_id("task"),
            created_at=now,
            updated_at=now,
        )
        self._store.save(task)
        return task

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """One writer for the task file, in this process and across processes."""
        writer = self._registry.writer(self._project_key)
        with writer:
            try:
                self._lock.acquire(self._owner)
            except ProjectBusy as err:
                raise TaskConflict(
                    "another writer is changing this project; do not retry "
                    "blindly, try again after it finishes"
                ) from err
            try:
                yield
            finally:
                self._lock.release(self._owner)


def _replace_op(task: Task, op: Op) -> tuple[Op, ...]:
    return tuple(op if item.id == op.id else item for item in task.ops)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


__all__ = [
    "DEFAULT_POLL_S",
    "DEFAULT_WAIT_S",
    "SearchOutcome",
    "TaskConflict",
    "TaskOpFailed",
    "TaskRegistry",
    "TaskService",
    "TaskToolError",
]
