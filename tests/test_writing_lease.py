"""The writing lease belongs to a run, not to a session."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds

from noteagent.agent.runtime import RunResult, RunResume, UserInput
from noteagent.api.session_access import WritingLeaseRuntime
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore


class _RecordingInner:
    def __init__(self, store: WritingStateStore, *, take: bool = True):
        self.store = store
        self.take = take
        self.requests = []

    def execute(self, request, sink):
        self.requests.append(request)
        lease_id = (
            request.new_run_id
            if isinstance(request, RunResume)
            else request.run_id
        )
        if self.take:
            self.store.ensure_writer("agent", lease_id)
        return RunResult(request.session_id, lease_id, "completed")


class WritingLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.store = WritingStateStore(
            self.layout, clock=FixedClock()
        )

    def test_the_wrapper_names_the_run_and_releases_its_lease(
        self,
    ) -> None:
        inner = _RecordingInner(self.store)
        runtime = WritingLeaseRuntime(
            inner, self.store, ids=SequentialIds()
        )

        result = runtime.execute(
            UserInput("s1", "问题"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertTrue(inner.requests[0].run_id)
        self.assertIsNone(self.store.load().writer)

    def test_a_resume_releases_the_interrupted_lease(self) -> None:
        self.store.ensure_writer("agent", "run-old")
        inner = _RecordingInner(self.store)
        runtime = WritingLeaseRuntime(
            inner, self.store, ids=SequentialIds()
        )

        runtime.execute(
            RunResume("s1", "run-old"), lambda _event: None
        )

        self.assertIsNone(self.store.load().writer)

    def test_an_older_run_does_not_clear_a_newer_lease(self) -> None:
        self.store.ensure_writer("agent", "run-new")
        inner = _RecordingInner(self.store, take=False)
        runtime = WritingLeaseRuntime(inner, self.store)

        runtime.execute(
            UserInput("s1", "问题", run_id="run-old"),
            lambda _event: None,
        )

        writer = self.store.load().writer
        self.assertEqual(writer["id"], "run-new")


if __name__ == "__main__":
    unittest.main()
