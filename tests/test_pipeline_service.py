"""The service around a run: liveness at restart and the stages it accepts."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.pipeline.lock import ProjectLock
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.service import PipelineService
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.init import init_project


def _clock(hour: int = 9) -> FixedClock:
    return FixedClock(datetime(2026, 11, 10, hour, tzinfo=timezone.utc))


def _service(layout, *, alive, stages=()) -> PipelineService:
    return PipelineService(
        layout,
        clock=_clock(),
        ids=SequentialIds(),
        stages=stages,
        lock=ProjectLock(layout.project_lock, clock=_clock(9), alive=alive),
        spawn=lambda work: work(),
    )


def _active_run() -> PipelineRun:
    return PipelineRun(
        id="pr-1",
        intent="上次没跑完",
        started_by="human",
        started_at="2026-11-10T09:00:00Z",
        updated_at="2026-11-10T09:00:00Z",
        status="running",
        stage="frame",
    )


class _Stage:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, context) -> None:
        return None


class InterruptionAtRestartTests(unittest.TestCase):
    def _held_lock(self, layout) -> None:
        ProjectLock(layout.project_lock, clock=_clock()).acquire("pr-9")

    def test_a_run_is_closed_when_the_lock_owner_is_gone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_active_run())
            self._held_lock(layout)

            closed = _service(layout, alive=lambda pid: False).mark_interrupted()

            self.assertEqual(closed, 1)
            self.assertEqual(store.get("pr-1").status, "interrupted")

    def test_a_run_is_left_alone_when_the_lock_owner_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            store.create(_active_run())
            self._held_lock(layout)

            closed = _service(layout, alive=lambda pid: True).mark_interrupted()

            self.assertEqual(closed, 0)
            self.assertEqual(store.get("pr-1").status, "running")


class StageValidationTests(unittest.TestCase):
    def test_a_stage_the_run_cannot_read_back_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            with self.assertRaises(ValueError):
                _service(layout, alive=lambda pid: True, stages=[_Stage("bogus")])


if __name__ == "__main__":
    unittest.main()
