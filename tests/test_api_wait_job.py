"""A plan started from the original button, and the chat that continues it."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import (
    FakeJobClient,
    FixedClock,
    SequentialIds,
)
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.jobs import JobArtifact, JobResult
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.api.topic_gate import plan_job_result
from noteagent.project.init import init_project
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from test_api_session_query import _sse

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


PLAN_PAYLOAD = {
    "intent": "熵",
    "candidates": [
        {
            "title": "Entropy",
            "slug": "concepts/entropy",
            "coverage": "write",
        },
        {
            "title": "Information",
            "slug": "concepts/information",
            "coverage": "mention",
        },
        {
            "title": "History",
            "slug": "concepts/history",
            "coverage": "skip",
        },
    ],
    "primary": {
        "title": "Entropy",
        "slug": "concepts/entropy",
        "coverage": "write",
    },
    "alsoInSources": [],
    "notes": "",
}


@unittest.skipIf(TestClient is None, "fastapi not installed")
class WaitedPlanApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.layout = init_project(self.repo / "projects" / "demo")
        self.store = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        self.store.create("s1")
        self.waits = SessionWaitStore(self.store)
        self.model = FauxModelClient.text("规划完成，接下来可以编译。")
        self.deps = AppDeps(
            sessions=self.store,
            model=self.model,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        patcher = patch.object(projects_mod, "REPO_ROOT", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        projects_mod.use_project("demo")

    def propose(self, job: str = "plan", wait_id: str = "w1") -> None:
        self.waits.propose(
            "s1",
            WaitState(
                wait_id=wait_id,
                job=job,  # type: ignore[arg-type]
                interface="source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion=f"start_{job}",
            ),
        )

    def post_plan(self, **extra):
        body = {"intent": "熵", "sessionId": "s1", "waitId": "w1", **extra}
        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_topic_map_job",
                return_value=PLAN_PAYLOAD,
            ),
        ):
            return TestClient(create_app(self.deps)).post(
                "/api/projects/demo/topic-map", json=body
            )

    def wait_for(self, predicate, message: str) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def wait_idle(self) -> None:
        """Let a background continuation finish before the temp folder goes."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.deps.runs.is_busy("s1"):
                return
            time.sleep(0.02)
        self.fail("the chat continuation did not finish")

    def wait_for_continuation(self) -> None:
        self.wait_for(
            lambda: any(
                entry.get("type") == "message"
                and entry.get("role") == "assistant"
                for entry in self.entries()
            ),
            "the chat never continued after the job",
        )
        self.wait_idle()

    def entries(self):
        return self.store.load("s1").entries

    def test_the_plan_enters_the_wait_and_the_chat_continues_with_its_result(
        self,
    ) -> None:
        self.propose()

        response = self.post_plan()

        self.assertEqual(response.status_code, 200, response.text)
        events = _sse(response.text)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1], PLAN_PAYLOAD)
        job_results = [data for kind, data in events if kind == "job_result"]
        self.assertEqual(len(job_results), 1)
        self.assertEqual(job_results[0]["waitId"], "w1")
        self.assertEqual(job_results[0]["status"], "succeeded")

        entries = self.entries()
        entered = next(
            entry for entry in entries if entry["type"] == "wait_entered"
        )
        self.assertTrue(entered["jobId"])
        completed = next(
            entry for entry in entries if entry["type"] == "wait_completed"
        )
        self.assertEqual(completed["waitId"], "w1")
        self.assertEqual(completed["jobId"], entered["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            completed["summary"],
            "planned 3 page(s): 1 write, 1 mention, 1 skip",
        )
        self.assertEqual(
            completed["artifacts"][0],
            {"kind": "write", "id": "concepts/entropy", "label": "Entropy"},
        )

        self.wait_for(
            lambda: any(
                entry.get("type") == "message"
                and entry.get("role") == "assistant"
                and entry.get("content") == "规划完成，接下来可以编译。"
                for entry in self.entries()
            ),
            "the chat never continued after the plan",
        )
        self.wait_idle()
        self.assertTrue(
            any(entry["type"] == "resume_started" for entry in self.entries())
        )

    def test_the_same_wait_cannot_start_a_second_job(self) -> None:
        self.propose()
        first = self.post_plan()
        self.assertEqual(first.status_code, 200, first.text)
        self.wait_for_continuation()

        second = TestClient(create_app(self.deps)).post(
            "/api/projects/demo/topic-map",
            json={"intent": "熵", "sessionId": "s1", "waitId": "w1"},
        )

        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["code"], "wait.alreadyStarted")
        entered = [
            entry for entry in self.entries() if entry["type"] == "wait_entered"
        ]
        self.assertEqual(len(entered), 1)

    def test_a_plan_cannot_enter_a_wait_for_another_job(self) -> None:
        self.propose(job="compile")

        response = TestClient(create_app(self.deps)).post(
            "/api/projects/demo/topic-map",
            json={"intent": "熵", "sessionId": "s1", "waitId": "w1"},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "wait.wrongJob")
        self.assertFalse(
            any(entry["type"] == "wait_entered" for entry in self.entries())
        )

    def test_an_unknown_wait_is_not_found(self) -> None:
        response = TestClient(create_app(self.deps)).post(
            "/api/projects/demo/topic-map",
            json={"intent": "熵", "sessionId": "s1", "waitId": "missing"},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "wait.notFound")

    def test_a_wait_context_needs_both_ids(self) -> None:
        response = TestClient(create_app(self.deps)).post(
            "/api/projects/demo/topic-map",
            json={"intent": "熵", "waitId": "w1"},
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["code"], "wait.contextIncomplete")

    def test_a_failed_plan_is_still_a_completion_the_chat_reads(self) -> None:
        self.propose()
        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_topic_map_job",
                side_effect=RuntimeError("the model did not answer"),
            ),
        ):
            response = TestClient(create_app(self.deps)).post(
                "/api/projects/demo/topic-map",
                json={"intent": "熵", "sessionId": "s1", "waitId": "w1"},
            )

        events = _sse(response.text)
        self.assertEqual(events[-1][0], "error")
        job_results = [data for kind, data in events if kind == "job_result"]
        self.assertEqual(len(job_results), 1)
        self.assertEqual(job_results[0]["status"], "failed")
        completed = next(
            entry for entry in self.entries() if entry["type"] == "wait_completed"
        )
        self.assertEqual(completed["status"], "failed")
        self.assertIn("the model did not answer", completed["error"])

        self.wait_for_continuation()

    def test_a_wait_that_cannot_be_recorded_does_not_fail_the_job(self) -> None:
        self.propose()
        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_topic_map_job",
                return_value=PLAN_PAYLOAD,
            ),
            patch.object(
                SessionWaitStore,
                "finish",
                side_effect=FileNotFoundError("the session is gone"),
            ),
            patch("noteagent.api.waited_jobs.logger"),
            patch("noteagent.api.app.start_resume"),
        ):
            response = TestClient(create_app(self.deps)).post(
                "/api/projects/demo/topic-map",
                json={"intent": "熵", "sessionId": "s1", "waitId": "w1"},
            )

        events = _sse(response.text)
        self.assertEqual(events[-1][0], "done")
        self.assertNotIn("error", [kind for kind, _data in events])
        self.assertEqual(events[-1][1], PLAN_PAYLOAD)


class PlanResultTests(unittest.TestCase):
    def test_every_planned_page_is_kept(self) -> None:
        payload = {
            "candidates": [
                {
                    "title": f"Page {index}",
                    "slug": f"concepts/page-{index}",
                    "coverage": "write",
                }
                for index in range(30)
            ]
        }

        result = plan_job_result(payload)

        self.assertEqual(len(result.artifacts), 30)
        self.assertIn("30 page(s)", result.summary)
        self.assertEqual(result.artifacts[0].kind, "write")


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ReconcileTests(unittest.TestCase):
    def test_opening_a_session_completes_a_wait_whose_job_finished(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlSessionStore(
                ProjectLayout(Path(tmp)),
                clock=FixedClock(),
                ids=SequentialIds(),
            )
            store.create("s1")
            waits = SessionWaitStore(store)
            waits.propose(
                "s1",
                WaitState(
                    wait_id="w1",
                    job="plan",
                    interface="source",
                    status="proposed",
                    proposed_at="2026-11-10T09:00:00Z",
                    completion="start_plan",
                ),
            )
            waits.enter("s1", "w1", "job-7")
            jobs = FakeJobClient()
            jobs.results["job-7"] = JobResult(
                job="plan",
                status="succeeded",
                summary="planned 1 page",
                artifacts=(
                    JobArtifact("write", "concepts/entropy", "Entropy"),
                ),
            )
            deps = AppDeps(
                sessions=store,
                jobs=jobs,
                model=FauxModelClient.text("规划结果已收到。"),
                clock=FixedClock(),
                ids=SequentialIds(),
            )

            response = TestClient(create_app(deps)).get("/api/v1/sessions/s1")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["wait"]["status"], "completed")
            self.assertEqual(
                response.json()["wait"]["resultSummary"], "planned 1 page"
            )
            completed = next(
                entry
                for entry in store.load("s1").entries
                if entry["type"] == "wait_completed"
            )
            self.assertEqual(completed["jobId"], "job-7")

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if any(
                    entry.get("type") == "message"
                    and entry.get("role") == "assistant"
                    for entry in store.load("s1").entries
                ) and not deps.runs.is_busy("s1"):
                    break
                time.sleep(0.02)
            else:
                self.fail("the chat never continued after reconciliation")

    def test_opening_a_session_continues_a_completed_wait_whose_resume_was_lost(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlSessionStore(
                ProjectLayout(Path(tmp)),
                clock=FixedClock(),
                ids=SequentialIds(),
            )
            store.create("s1")
            waits = SessionWaitStore(store)
            waits.propose(
                "s1",
                WaitState(
                    wait_id="w1",
                    job="plan",
                    interface="source",
                    status="proposed",
                    proposed_at="2026-11-10T09:00:00Z",
                    completion="start_plan",
                ),
            )
            waits.enter("s1", "w1", "job-7")
            waits.finish(
                "s1",
                "w1",
                JobResult(
                    job="plan",
                    status="succeeded",
                    summary="planned 1 page",
                ),
            )
            # The first continuation was lost: the wait is completed but no
            # resume ever started.
            deps = AppDeps(
                sessions=store,
                model=FauxModelClient.text("继续写。"),
                clock=FixedClock(),
                ids=SequentialIds(),
            )

            response = TestClient(create_app(deps)).get("/api/v1/sessions/s1")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["wait"]["status"], "completed")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                entries = store.load("s1").entries
                if (
                    any(
                        entry.get("type") == "message"
                        and entry.get("role") == "assistant"
                        for entry in entries
                    )
                    and any(
                        entry.get("type") == "resume_started"
                        for entry in entries
                    )
                    and not deps.runs.is_busy("s1")
                ):
                    break
                time.sleep(0.02)
            else:
                self.fail("the lost continuation was never started")


if __name__ == "__main__":
    unittest.main()
