"""The one writing task of a project, stored under its ``.noteagent``.

The file is written whole and atomically: a reader never sees half a task, and
two operations on one task never rewrite it from a snapshot the other did not
see, because the executor is the only writer and it holds the task's lock.
"""

from __future__ import annotations

import errno
import json
import time
from collections.abc import Mapping
from pathlib import Path

from noteagent.io.atomic import write_text_atomic
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import Task, is_task_id, task_from_dict, task_to_dict

#: How many times a read retries a transient sharing error, and the base pause
#: between tries. On Windows an atomic replace can deny a reader the old file
#: for an instant; that is a lock, not a corrupt task. Five tries with a 5 ms
#: step cover the replace window without hiding a genuinely locked file.
_READ_ATTEMPTS = 5
_READ_RETRY_S = 0.005


class TaskUnreadableError(RuntimeError):
    """The task file exists but cannot be read as a task.

    A missing file means "no task was made yet"; an unreadable one must never
    look the same, or the next read would create a fresh task over the person's
    state. Callers surface it and leave the file untouched.
    """


def _transient_read_error(err: OSError) -> bool:
    """Whether an OS read error is the instant a replace holds the file.

    A Windows sharing violation arrives as ``PermissionError`` (EACCES) or a
    winerror 32/33; ``EBUSY`` is the POSIX form. Anything else (a bad path, a
    decode failure) is not retried: it will not get better.
    """
    if isinstance(err, PermissionError):
        return True
    if err.errno in (errno.EACCES, errno.EBUSY):
        return True
    return getattr(err, "winerror", None) in (32, 33)


class TaskStore:
    """The task file, with its id checked before it can name a path."""

    def __init__(self, layout: ProjectLayout) -> None:
        self._path = layout.task_file

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Task | None:
        """The task, or ``None`` when none was made yet.

        An existing file that cannot be read raises ``TaskUnreadableError``: it
        is not an absent task, and creating one over it would destroy whatever
        the person had. The file is never rewritten by a read. A transient
        sharing error (an atomic replace in flight) is retried first, so a
        reader racing the writer does not see a corrupt-looking task.
        """
        text = self._read_text()
        if text is None:
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as err:
            raise TaskUnreadableError(
                f"the writing task file {self._path} is not valid JSON: {err}"
            ) from err
        if not isinstance(raw, Mapping):
            raise TaskUnreadableError(
                f"the writing task file {self._path} does not hold an object"
            )
        try:
            return task_from_dict(raw)
        except ValueError as err:
            raise TaskUnreadableError(
                f"the writing task file {self._path} is not a task: {err}"
            ) from err

    def _read_text(self) -> str | None:
        """The file's text, or ``None`` when it does not exist.

        A sharing/lock error is retried briefly: the writer replaces the file
        atomically, so the old name can be denied for a few milliseconds. Only
        a lock that outlives the retries is reported as unreadable.
        """
        last: OSError | None = None
        for attempt in range(_READ_ATTEMPTS):
            try:
                return self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except UnicodeDecodeError as err:
                raise TaskUnreadableError(
                    f"the writing task file {self._path} cannot be decoded: {err}"
                ) from err
            except OSError as err:
                if not _transient_read_error(err):
                    raise TaskUnreadableError(
                        f"the writing task file {self._path} cannot be read: {err}"
                    ) from err
                last = err
                if attempt + 1 < _READ_ATTEMPTS:
                    time.sleep(_READ_RETRY_S * (attempt + 1))
        raise TaskUnreadableError(
            f"the writing task file {self._path} stays locked: {last}"
        ) from last

    def save(self, task: Task) -> None:
        if not is_task_id(task.id):
            raise ValueError(f"not a task id: {task.id!r}")
        write_text_atomic(
            self._path,
            json.dumps(task_to_dict(task), ensure_ascii=False, indent=2) + "\n",
        )

__all__ = ["TaskStore", "TaskUnreadableError"]
