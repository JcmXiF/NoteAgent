"""The timeout/completion race at the task executor's boundary.

The API seam covers each order on its own: a wait that expires first, and a
node that finishes before the tool looks. The narrow window between the wait's
deadline and the settle's writer lock is not schedulable there, so this test
orders the two writers explicitly: the tool's settle is made to wait behind the
executor's finishing write, exactly as a real lost race would.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.project.init import init_project
from noteagent.task.model import SearchCard
from noteagent.task.service import TaskRegistry, TaskService
from noteagent.task.store import TaskStore


class _RaceLock:
    """A writer lock that lets the executor finish before the tool settles.

    The tool's first acquisition is its op request; its second is the settle
    after the bounded wait expired. That second one waits for the executor's
    second acquisition (its finishing write), which is the race the settle is
    designed for.
    """

    def __init__(self) -> None:
        self._inner = threading.Lock()
        # Counts of acquisitions, not holders: the tool's second acquisition is
        # its settle, the executor's second is its finish.
        self._tool_entries = 0
        self._executor_entries = 0
        self.settling = threading.Event()
        self.executor_finished = threading.Event()

    def __enter__(self):
        if threading.current_thread().name == "task-executor":
            self._executor_entries += 1
            self._inner.acquire()
            return self
        self._tool_entries += 1
        if self._tool_entries == 2:
            self.settling.set()
            self.executor_finished.wait(5)
        self._inner.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        if threading.current_thread().name == "task-executor":
            finished = self._executor_entries == 2
            self._inner.release()
            if finished:
                self.executor_finished.set()
            return False
        self._inner.release()
        return False


class _RaceRegistry(TaskRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.writer_lock = _RaceLock()

    def writer(self, project_key: str) -> _RaceLock:
        return self.writer_lock


class _GatedSearch:
    def __init__(self) -> None:
        self.gate = threading.Event()
        self.called = threading.Event()

    def __call__(self, query: str):
        self.called.set()
        self.gate.wait(5)
        return [
            SearchCard(
                title="Entropy",
                url="https://example.com/entropy",
                snippet="熵衡量不确定性。",
                site="example.com",
            )
        ]


class OpTimeoutRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "projects" / "demo")

    def test_a_completion_that_wins_the_race_returns_its_result_not_a_wait(
        self,
    ) -> None:
        registry = _RaceRegistry()
        search = _GatedSearch()
        notices: list = []

        def spawn(work) -> None:
            threading.Thread(target=work, name="task-executor").start()

        service = TaskService(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
            search=search,
            fetch=lambda url: (_ for _ in ()).throw(AssertionError("unused")),
            stage=lambda capture, run_id: (_ for _ in ()).throw(
                AssertionError("unused")
            ),
            registry=registry,
            spawn=spawn,
            wait_s=0.001,
            poll_s=0.001,
            on_resume=notices.append,
        )
        outcome: dict[str, object] = {}

        def run_search() -> None:
            outcome["result"] = service.search("entropy", session_id="s-1")

        tool = threading.Thread(target=run_search, name="tool-thread")
        tool.start()
        try:
            # The wait has expired and the tool is about to settle; the search
            # node is still held open.
            self.assertTrue(registry.writer_lock.settling.wait(5))
            self.assertTrue(search.called.wait(5))
            search.gate.set()
            tool.join(5)
        finally:
            search.gate.set()
            tool.join(5)

        result = outcome["result"]
        self.assertEqual(result.status, "done")
        self.assertEqual(result.rev, 1)
        self.assertEqual([card.title for card in result.cards], ["Entropy"])
        # A completed op never asks for a resume: the tool returned the real
        # answer, so the conversation has nothing to catch up on.
        task = TaskStore(self.layout).load()
        self.assertEqual(task.ops[0].status, "done")
        self.assertFalse(task.ops[0].resume_requested)
        self.assertEqual(notices, [])


if __name__ == "__main__":
    unittest.main()
