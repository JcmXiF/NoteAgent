"""The one place the writing task is replaced and saved.

Every mutation path goes through this seam, so the revision and the person's
gate cannot drift between call sites:

* :meth:`TaskWriter.commit_content` is a change to what the task *is*: cards,
  materials, frames, the approval or pending request, the result. It moves
  ``rev`` exactly once and clears the old approval and request in one place; a
  caller that writes a new gate (an approval, a pending request) passes it as
  part of the same change and overrides the clearing.
* :meth:`TaskWriter.commit_log` is a change to the record around that content:
  op status, the resume attempt/delivery markers, an interruption or a cancel.
  It moves neither ``rev`` nor the gate.

The task file's own lock and atomic replace stay in the store; this seam is
what gives every writer the same revision and invalidation semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from noteagent.agent.clock import Clock, iso_timestamp
from noteagent.task.model import Task
from noteagent.task.store import TaskStore


class TaskWriter:
    """The task's single write seam: content commits bump, log commits do not."""

    def __init__(self, store: TaskStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def create(self, task: Task) -> Task:
        """Write a task that did not exist yet; there is no revision to move."""
        self._store.save(task)
        return task

    def next_rev(self, task: Task) -> int:
        """The revision a content commit of *task* will write.

        This is the one place the next content revision is derived. A commit
        uses it, and so does anything that must carry the revision it is bound
        to in the same way (a pending request, an approval, a completion's
        ``resume_rev``).
        """
        return task.rev + 1

    def commit_content(
        self, task: Task, changes: Mapping[str, object]
    ) -> Task:
        """Apply a content change: one revision bump, gate invalidated.

        The approval and pending write request are bound to the revision the
        content had. Any content change makes them stale, so they are cleared
        here rather than at each call site. A caller that is writing a new gate
        in this same commit supplies it in *changes* and keeps it.
        """
        values = dict(changes)
        values.setdefault("approval", None)
        values.setdefault("request", None)
        if task.status == "needs_approval":
            # Clearing the request ends the waiting-for-approval state unless
            # the caller is writing the next thing to wait on.
            values.setdefault("status", "idle")
        values["rev"] = self.next_rev(task)
        values["updated_at"] = iso_timestamp(self._clock)
        return self._save(task, values)

    def commit_log(self, task: Task, changes: Mapping[str, object]) -> Task:
        """Apply a record-only change: no revision bump, no gate change."""
        values = dict(changes)
        values["updated_at"] = iso_timestamp(self._clock)
        return self._save(task, values)

    def _save(
        self, task: Task, changes: Mapping[str, object]
    ) -> Task:
        saved = replace(task, **dict(changes))
        self._store.save(saved)
        return saved


__all__ = ["TaskWriter"]
