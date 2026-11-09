"""A session's one wait, read back from the events the log committed."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.jobs import JobArtifact, JobResult
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


class SessionWaitStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.sessions.create("s1")
        self.waits = SessionWaitStore(self.sessions)

    def propose(
        self,
        wait_id: str = "w1",
        job: str = "plan",
        interface: str = "source",
    ) -> WaitState:
        self.waits.propose(
            "s1",
            WaitState(
                wait_id=wait_id,
                job=job,  # type: ignore[arg-type]
                interface=interface,  # type: ignore[arg-type]
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion=f"start_{job}",
                intent="plan entropy",
            ),
        )
        current = self.waits.current("s1")
        assert current is not None
        return current

    def test_a_proposal_is_the_current_wait(self) -> None:
        wait = self.propose()

        self.assertEqual(wait.wait_id, "w1")
        self.assertEqual(wait.job, "plan")
        self.assertEqual(wait.interface, "source")
        self.assertEqual(wait.status, "proposed")
        self.assertEqual(wait.completion, "start_plan")
        self.assertEqual(wait.intent, "plan entropy")
        self.assertEqual(wait.proposed_at, "2026-11-10T09:00:00Z")

    def test_entering_keeps_what_was_proposed(self) -> None:
        self.propose()

        self.waits.enter("s1", "w1", "job-9")

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "entered")
        self.assertEqual(wait.job, "plan")
        self.assertEqual(wait.interface, "source")
        self.assertEqual(wait.intent, "plan entropy")

    def test_cancelling_leaves_the_last_wait_cancelled(self) -> None:
        self.propose()

        self.waits.cancel("s1", "w1", "stopped from the chat")

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "cancelled")

    def test_completing_keeps_the_summary(self) -> None:
        self.propose()

        self.waits.complete(
            "s1",
            "w1",
            JobResult(
                job="plan",
                status="succeeded",
                summary="planned 3 pages",
                artifacts=(JobArtifact("plan", "concepts/entropy", "Entropy plan"),),
            ),
        )

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "completed")
        self.assertEqual(wait.result_summary, "planned 3 pages")

    def test_a_failed_job_is_a_failed_wait(self) -> None:
        self.propose()

        self.waits.complete(
            "s1",
            "w1",
            JobResult(job="plan", status="failed", summary="", error="out of budget"),
        )

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "failed")

    def test_a_new_proposal_supersedes_the_previous_wait(self) -> None:
        self.propose("w1")
        self.waits.cancel("s1", "w1")

        wait = self.propose("w2", "compile", "plan_board")

        self.assertEqual(wait.wait_id, "w2")
        self.assertEqual(wait.job, "compile")
        self.assertEqual(wait.status, "proposed")

    def test_an_event_for_an_older_wait_does_not_move_the_current_one(self) -> None:
        self.propose("w1")
        self.waits.cancel("s1", "w1")
        self.propose("w2", "compile", "plan_board")

        self.waits.enter("s1", "w1", "job-old")
        self.waits.cancel("s1", "w1")

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.wait_id, "w2")
        self.assertEqual(wait.status, "proposed")
        self.assertEqual(wait.job, "compile")

    def test_the_wait_events_are_entries_the_log_keeps(self) -> None:
        self.propose()

        entries = self.sessions.load("s1").entries

        self.assertEqual(entries[-1]["type"], "wait_proposed")
        self.assertEqual(entries[-1]["id"], "w1")
        self.assertEqual(entries[-1]["completion"], "start_plan")

    def test_a_session_without_wait_events_has_no_wait(self) -> None:
        self.assertIsNone(self.waits.current("s1"))


if __name__ == "__main__":
    unittest.main()
