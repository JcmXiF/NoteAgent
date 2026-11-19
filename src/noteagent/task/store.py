"""The one writing task of a project, stored under its ``.noteagent``.

The file is written whole and atomically: a reader never sees half a task, and
two operations on one task never rewrite it from a snapshot the other did not
see, because the executor is the only writer and it holds the task's lock.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from noteagent.io.atomic import write_text_atomic
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import Task, is_task_id, task_from_dict, task_to_dict


class TaskStore:
    """The task file, with its id checked before it can name a path."""

    def __init__(self, layout: ProjectLayout) -> None:
        self._path = layout.task_file

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Task | None:
        """The task, or ``None`` when none was made yet or cannot be read."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, Mapping):
            return None
        try:
            return task_from_dict(raw)
        except ValueError:
            return None

    def save(self, task: Task) -> None:
        if not is_task_id(task.id):
            raise ValueError(f"not a task id: {task.id!r}")
        write_text_atomic(
            self._path,
            json.dumps(task_to_dict(task), ensure_ascii=False, indent=2) + "\n",
        )

    def create(self, task: Task) -> None:
        if self._path.exists():
            raise FileExistsError(
                f"the project already has a writing task: {task.id}"
            )
        self.save(task)


__all__ = ["TaskStore"]
