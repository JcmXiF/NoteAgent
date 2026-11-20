"""The task file's read path: missing, corrupt, and briefly locked.

On Windows an atomic replace can deny a reader the target for an instant. That
is a lock, not a corrupt task, so a read retries it; a file that really cannot
be read is still surfaced and left untouched.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.project.init import init_project
from noteagent.task.model import Task
from noteagent.task.store import TaskStore, TaskUnreadableError


class _Layout:
    """A layout whose task file is the given path-like object."""

    def __init__(self, task_file: object) -> None:
        self._task_file = task_file

    @property
    def task_file(self) -> object:
        return self._task_file


class _FlakyPath:
    """A path whose first *failures* reads fail with one OS error."""

    def __init__(
        self,
        inner: Path,
        *,
        failures: int,
        error: OSError | None = None,
    ) -> None:
        self.inner = inner
        self.failures = failures
        self.error = error or PermissionError(13, "Permission denied")
        self.reads = 0

    def read_text(self, encoding: str = "utf-8") -> str:
        self.reads += 1
        if self.reads <= self.failures:
            raise self.error
        return self.inner.read_text(encoding=encoding)

    def __str__(self) -> str:
        return str(self.inner)


class TaskStoreCompatibilityTests(unittest.TestCase):
    """An older task file loads with defaults and is not rewritten on read."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        self.layout.task_file.parent.mkdir(parents=True, exist_ok=True)

    def test_a_file_without_the_newer_bindings_loads_with_defaults(self) -> None:
        legacy = {
            "version": 1,
            "id": "task-1",
            "kind": "writing",
            "intent": "entropy",
            "status": "idle",
            "rev": 2,
            "createdAt": "2026-11-10T09:00:00Z",
            "updatedAt": "2026-11-10T09:05:00Z",
            "cards": [],
            "materials": [],
            "frames": [],
            "plan": [],
            "approval": {
                "frameId": "frame-1",
                "spec": {
                    "slug": "entropy",
                    "title": "Entropy",
                    "action": "create",
                    "sections": [{"title": "What", "blocks": ["b1"]}],
                },
                "rev": 2,
                "by": "human",
                "at": "2026-11-10T09:05:00Z",
            },
            "ops": [
                {
                    "id": "op-1",
                    "kind": "search",
                    "actor": "agent",
                    "status": "done",
                    "input": {"query": "entropy"},
                    "expectedRev": 0,
                    "createdAt": "2026-11-10T09:00:00Z",
                    "updatedAt": "2026-11-10T09:00:00Z",
                    "result": {},
                }
            ],
        }
        text = json.dumps(legacy, ensure_ascii=False, indent=2) + "\n"
        self.layout.task_file.write_text(text, encoding="utf-8")

        task = TaskStore(self.layout).load()

        self.assertEqual(task.rev, 2)
        self.assertEqual(task.approval.page_digest, "")
        self.assertEqual(task.ops[0].resume_rev, 0)
        self.assertEqual(task.ops[0].resume_attempt, 0)
        # Reading never migrates or rewrites the person's file.
        self.assertEqual(self.layout.task_file.read_text(encoding="utf-8"), text)

    def test_an_old_resume_started_flag_loads_as_delivered(self) -> None:
        raw = {
            "version": 1,
            "id": "task-1",
            "createdAt": "2026-11-10T09:00:00Z",
            "updatedAt": "2026-11-10T09:05:00Z",
            "rev": 1,
            "ops": [
                {
                    "id": "op-1",
                    "kind": "search",
                    "actor": "agent",
                    "status": "done",
                    "input": {},
                    "expectedRev": 0,
                    "createdAt": "2026-11-10T09:00:00Z",
                    "updatedAt": "2026-11-10T09:00:00Z",
                    "resumeRequested": True,
                    "resumeStarted": True,
                }
            ],
        }
        self.layout.task_file.write_text(
            json.dumps(raw), encoding="utf-8"
        )

        task = TaskStore(self.layout).load()

        self.assertTrue(task.ops[0].resume_started)
        self.assertEqual(task.ops[0].resume_attempt, 0)


class TaskStoreReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=2,
            )
        )

    def test_a_transient_sharing_error_is_retried(self) -> None:
        flaky = _FlakyPath(self.layout.task_file, failures=2)

        task = TaskStore(_Layout(flaky)).load()  # type: ignore[arg-type]

        self.assertIsNotNone(task)
        self.assertEqual(task.id, "task-1")
        self.assertEqual(task.rev, 2)
        self.assertEqual(flaky.reads, 3)

    def test_a_persistent_sharing_error_is_unreadable_not_missing(self) -> None:
        flaky = _FlakyPath(self.layout.task_file, failures=1000)

        with self.assertRaises(TaskUnreadableError):
            TaskStore(_Layout(flaky)).load()  # type: ignore[arg-type]

        # It retried rather than calling the task absent, and the real file is
        # still readable and untouched for the next caller.
        self.assertGreater(flaky.reads, 1)
        self.assertEqual(TaskStore(self.layout).load().id, "task-1")

    def test_a_corrupt_file_is_not_retried(self) -> None:
        self.layout.task_file.write_text("{not json", encoding="utf-8")
        flaky = _FlakyPath(self.layout.task_file, failures=0)

        with self.assertRaises(TaskUnreadableError) as caught:
            TaskStore(_Layout(flaky)).load()  # type: ignore[arg-type]

        self.assertIn("not valid JSON", str(caught.exception))
        self.assertEqual(flaky.reads, 1)
        self.assertEqual(
            self.layout.task_file.read_text(encoding="utf-8"), "{not json"
        )

    def test_a_missing_file_is_none_and_not_retried(self) -> None:
        self.layout.task_file.unlink()
        flaky = _FlakyPath(self.layout.task_file, failures=0)

        self.assertIsNone(
            TaskStore(_Layout(flaky)).load()  # type: ignore[arg-type]
        )
        self.assertEqual(flaky.reads, 1)


if __name__ == "__main__":
    unittest.main()
