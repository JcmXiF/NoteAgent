"""Runs on disk: one file each, read back, and closed when a core restarted."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.init import init_project


def _run(**overrides: object) -> PipelineRun:
    values: dict[str, object] = {
        "id": "pr-1",
        "intent": "写一篇熵的笔记",
        "started_by": "human",
        "started_at": "2026-11-10T09:00:00Z",
        "updated_at": "2026-11-10T09:00:00Z",
    }
    values.update(overrides)
    return PipelineRun(**values)  # type: ignore[arg-type]


class PipelineRunStoreTests(unittest.TestCase):
    def test_a_run_round_trips_through_its_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            run = _run()

            store.create(run)

            self.assertEqual(store.get("pr-1"), run)
            self.assertEqual([item.id for item in store.list()], ["pr-1"])

    def test_two_runs_with_the_same_id_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_run())

            with self.assertRaises(FileExistsError):
                store.create(_run())

    def test_mark_interrupted_closes_running_runs_and_leaves_a_wait_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_run(id="pr-1", status="running", stage="frame"))
            store.create(_run(id="pr-2", status="awaiting_confirmation", stage="confirm"))
            store.create(_run(id="pr-3", status="succeeded", stage="done"))
            store.create(_run(id="pr-4", status="compiling", stage="compile"))

            closed = store.mark_interrupted(now="2026-11-10T10:00:00Z")

            self.assertEqual(closed, 2)
            self.assertEqual(store.get("pr-1").status, "interrupted")
            self.assertEqual(store.get("pr-4").status, "interrupted")
            # A run waiting for its human was paused, not running: the plan it
            # shows is still the plan that was made.
            self.assertEqual(store.get("pr-2").status, "awaiting_confirmation")
            self.assertEqual(store.get("pr-3").status, "succeeded")
            self.assertEqual(store.get("pr-1").updated_at, "2026-11-10T10:00:00Z")

    def test_a_saved_run_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_run())

            leftovers = [
                path.name
                for path in layout.pipeline_runs.iterdir()
                if path.name.endswith(".tmp")
            ]
            self.assertEqual(leftovers, [])

    def test_a_second_run_reads_back_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_run(id="pr-1", started_at="2026-11-10T09:00:00Z"))
            store.create(_run(id="pr-2", started_at="2026-11-10T09:01:00Z"))

            self.assertEqual([item.id for item in store.list()], ["pr-2", "pr-1"])


if __name__ == "__main__":
    unittest.main()
