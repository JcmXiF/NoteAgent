"""A compile started from the plan board, and the chat that continues it."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.api.resume import job_cleanup
from noteagent.api.session_access import JobWait, enter_wait_for_job
from noteagent.api.topic_gate import compile_job_result
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
        {"title": "Entropy", "slug": "concepts/entropy", "coverage": "write"}
    ],
    "primary": {
        "title": "Entropy",
        "slug": "concepts/entropy",
        "coverage": "write",
    },
    "alsoInSources": [],
    "notes": "",
}

COMPILE_PAYLOAD = {
    "results": [
        {
            "written": [
                {
                    "path": "wiki/concepts/entropy.md",
                    "mode": "create",
                    "bytes": 120,
                }
            ],
            "doctor": None,
        }
    ],
    "errors": [],
}

CONFIRMED = [
    {
        "name": "Entropy",
        "slug": "concepts/entropy",
        "wikiPath": "wiki/concepts/entropy.md",
        "action": "write",
        "sectionIds": ["s1"],
    }
]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class CompileWaitApiTests(unittest.TestCase):
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
        patcher = patch.object(projects_mod, "REPO_ROOT", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        projects_mod.use_project("demo")

    def deps(self, model=None) -> "AppDeps":
        return AppDeps(
            sessions=self.store,
            model=model or FauxModelClient.text("已写入。"),
            clock=FixedClock(),
            ids=SequentialIds(),
        )

    def propose(self, job: str = "compile", wait_id: str = "w1") -> None:
        self.waits.propose(
            "s1",
            WaitState(
                wait_id=wait_id,
                job=job,  # type: ignore[arg-type]
                interface="plan_board" if job == "compile" else "source",
                status="proposed",
                proposed_at="2026-11-10T09:00:00Z",
                completion=f"start_{job}",
            ),
        )

    def post_compile(self, deps, wait_id: str = "w1"):
        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_ingest_confirmed_job",
                return_value=COMPILE_PAYLOAD,
            ),
        ):
            return TestClient(create_app(deps)).post(
                "/api/projects/demo/ingest-confirmed",
                json={
                    "intent": "熵",
                    "confirmed": CONFIRMED,
                    "sessionId": "s1",
                    "waitId": wait_id,
                },
            )

    def entries(self):
        return self.store.load("s1").entries

    def wait_for(self, predicate, message: str) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def wait_for_continuation(self, deps, content: str = "已写入。") -> None:
        self.wait_for(
            lambda: any(
                entry.get("type") == "message"
                and entry.get("role") == "assistant"
                and entry.get("content") == content
                for entry in self.entries()
            ),
            "the chat never continued after the compile",
        )
        self.wait_for(
            lambda: not deps.runs.is_busy("s1"),
            "the chat continuation did not finish",
        )

    def test_a_compile_enters_the_wait_and_the_chat_reads_its_result(
        self,
    ) -> None:
        self.propose()
        deps = self.deps()

        response = self.post_compile(deps)

        self.assertEqual(response.status_code, 200, response.text)
        events = _sse(response.text)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1], COMPILE_PAYLOAD)
        job_results = [data for kind, data in events if kind == "job_result"]
        self.assertEqual(len(job_results), 1)
        self.assertEqual(job_results[0]["status"], "succeeded")

        entered = next(
            entry for entry in self.entries() if entry["type"] == "wait_entered"
        )
        completed = next(
            entry
            for entry in self.entries()
            if entry["type"] == "wait_completed"
        )
        self.assertEqual(completed["waitId"], "w1")
        self.assertEqual(completed["jobId"], entered["jobId"])
        self.assertEqual(completed["job"], "compile")
        self.assertEqual(completed["summary"], "wrote 1 page(s)")
        self.assertEqual(
            completed["artifacts"],
            [
                {
                    "kind": "written",
                    "id": "wiki/concepts/entropy.md",
                    "label": "Entropy",
                }
            ],
        )
        self.wait_for_continuation(deps)
        self.assertFalse(deps.job_gate.is_busy())

    def test_a_compile_cannot_enter_a_plan_wait(self) -> None:
        self.propose(job="plan")

        response = TestClient(create_app(self.deps())).post(
            "/api/projects/demo/ingest-confirmed",
            json={
                "intent": "熵",
                "confirmed": CONFIRMED,
                "sessionId": "s1",
                "waitId": "w1",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "wait.wrongJob")
        self.assertFalse(
            any(entry["type"] == "wait_entered" for entry in self.entries())
        )

    def test_another_job_holds_the_gate_and_the_wait_stays_proposed(self) -> None:
        self.propose()
        deps = self.deps()
        deps.job_gate.claim("someone-else")
        try:
            with patch("noteagent.api.app.ensure_llm_configured"):
                response = TestClient(create_app(deps)).post(
                    "/api/projects/demo/ingest-confirmed",
                    json={
                        "intent": "熵",
                        "confirmed": CONFIRMED,
                        "sessionId": "s1",
                        "waitId": "w1",
                    },
                )
        finally:
            deps.job_gate.release("someone-else")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "job.busy")
        self.assertFalse(
            any(entry["type"] == "wait_entered" for entry in self.entries())
        )
        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "proposed")

        # The gate is free again, so the same wait can still be entered.
        again = self.post_compile(deps)
        self.assertEqual(again.status_code, 200, again.text)
        self.wait_for_continuation(deps)

    def test_a_plan_waits_for_the_same_gate(self) -> None:
        self.propose(job="plan")
        deps = self.deps()
        deps.job_gate.claim("someone-else")
        try:
            with (
                patch("noteagent.api.app.ensure_llm_configured"),
                patch(
                    "noteagent.api.topic_gate.run_topic_map_job",
                    return_value=PLAN_PAYLOAD,
                ),
            ):
                response = TestClient(create_app(deps)).post(
                    "/api/projects/demo/topic-map",
                    json={
                        "intent": "熵",
                        "sessionId": "s1",
                        "waitId": "w1",
                    },
                )
        finally:
            deps.job_gate.release("someone-else")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "job.busy")
        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "proposed")

    def test_a_direct_plan_press_waits_for_the_gate(self) -> None:
        deps = self.deps()
        deps.job_gate.claim("someone-else")
        try:
            with patch("noteagent.api.app.ensure_llm_configured"):
                response = TestClient(create_app(deps)).post(
                    "/api/projects/demo/topic-map", json={"intent": "熵"}
                )
        finally:
            deps.job_gate.release("someone-else")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "job.busy")

    def test_a_direct_compile_press_claims_and_releases_the_gate(self) -> None:
        deps = self.deps()
        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_ingest_confirmed_job",
                return_value=COMPILE_PAYLOAD,
            ),
        ):
            response = TestClient(create_app(deps)).post(
                "/api/projects/demo/ingest-confirmed",
                json={"intent": "熵", "confirmed": CONFIRMED},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(_sse(response.text)[-1][0], "done")
        self.assertFalse(deps.job_gate.is_busy())

    def test_a_disconnected_job_closes_its_wait_and_releases_the_gate(self) -> None:
        self.propose()
        deps = self.deps()
        job_wait = JobWait(self.store, self.waits, "s1", "w1")
        job_id = enter_wait_for_job(deps, job_wait, "compile")

        job_cleanup(deps, job_wait, self.layout, job_id, "compile")

        wait = self.waits.current("s1")
        assert wait is not None
        self.assertEqual(wait.status, "failed")
        self.assertFalse(deps.job_gate.is_busy())
        self.wait_for_continuation(deps)

    def test_a_stopped_compile_wait_cannot_start_a_job(self) -> None:
        self.propose()
        stopped = TestClient(create_app(self.deps())).post(
            "/api/v1/sessions/s1/wait/w1/cancel"
        )
        self.assertEqual(stopped.status_code, 200, stopped.text)

        response = TestClient(create_app(self.deps())).post(
            "/api/projects/demo/ingest-confirmed",
            json={
                "intent": "熵",
                "confirmed": CONFIRMED,
                "sessionId": "s1",
                "waitId": "w1",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "wait.alreadyStarted")
        self.assertFalse(
            any(entry["type"] == "wait_entered" for entry in self.entries())
        )

    def test_plan_then_compile_continues_the_chat_twice(self) -> None:
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "p1", "propose", '{"job":"plan","interface":"source"}'
            ).script,
            FauxModelClient.tool_call(
                "p2",
                "propose",
                '{"job":"compile","interface":"plan_board"}',
            ).script,
            FauxModelClient.text("编译完成，已经写入。").script,
        )
        deps = self.deps(model)
        client = TestClient(create_app(deps))

        asked = client.post(
            "/api/v1/sessions/s1/query", json={"content": "把选中的资料写成页"}
        )
        self.assertEqual(asked.status_code, 200, asked.text)
        plan_wait = next(
            data for kind, data in _sse(asked.text) if kind == "wait"
        )

        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_topic_map_job",
                return_value=PLAN_PAYLOAD,
            ),
        ):
            planned = client.post(
                "/api/projects/demo/topic-map",
                json={
                    "intent": "熵",
                    "sessionId": "s1",
                    "waitId": plan_wait["waitId"],
                },
            )
        self.assertEqual(planned.status_code, 200, planned.text)

        def compile_wait():
            wait = self.waits.current("s1")
            if wait is not None and wait.job == "compile" and wait.status == "proposed":
                return wait
            return None

        self.wait_for(
            lambda: compile_wait() is not None,
            "the plan never resulted in a compile proposal",
        )
        pending = compile_wait()
        assert pending is not None
        self.wait_for(
            lambda: not deps.job_gate.is_busy(),
            "the plan job never released the gate",
        )

        with (
            patch("noteagent.api.app.ensure_llm_configured"),
            patch(
                "noteagent.api.topic_gate.run_ingest_confirmed_job",
                return_value=COMPILE_PAYLOAD,
            ),
        ):
            compiled = client.post(
                "/api/projects/demo/ingest-confirmed",
                json={
                    "intent": "熵",
                    "confirmed": CONFIRMED,
                    "sessionId": "s1",
                    "waitId": pending.wait_id,
                },
            )
        self.assertEqual(compiled.status_code, 200, compiled.text)
        self.wait_for_continuation(deps, "编译完成，已经写入。")

        entries = self.entries()
        waits = [
            (entry["type"], entry.get("job"), entry.get("status"))
            for entry in entries
            if entry["type"].startswith("wait_")
        ]
        self.assertEqual(
            waits,
            [
                ("wait_proposed", "plan", None),
                ("wait_entered", None, None),
                ("wait_completed", "plan", "completed"),
                ("wait_proposed", "compile", None),
                ("wait_entered", None, None),
                ("wait_completed", "compile", "completed"),
            ],
        )
        self.assertEqual(
            sum(1 for entry in entries if entry["type"] == "resume_started"),
            2,
        )
        self.assertFalse(deps.job_gate.is_busy())


class CompileResultTests(unittest.TestCase):
    def test_written_pages_are_named_by_the_confirmed_page(self) -> None:
        result = compile_job_result(COMPILE_PAYLOAD, confirmed=CONFIRMED)

        self.assertEqual(result.job, "compile")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.summary, "wrote 1 page(s)")
        self.assertEqual(
            [(item.kind, item.id, item.label) for item in result.artifacts],
            [
                (
                    "written",
                    "wiki/concepts/entropy.md",
                    "Entropy",
                )
            ],
        )

    def test_a_page_without_a_confirmed_title_falls_back_to_its_stem(self) -> None:
        result = compile_job_result(COMPILE_PAYLOAD)

        self.assertEqual(result.artifacts[0].label, "entropy")
        self.assertNotIn("/", result.artifacts[0].label)

    def test_a_partial_round_is_a_failed_job_with_its_written_pages(self) -> None:
        result = compile_job_result(
            {
                "results": [
                    {
                        **COMPILE_PAYLOAD["results"][0],
                        "writeWarnings": [
                            {
                                "code": "failed",
                                "message": "the page was refused",
                                "path": "wiki/other.md",
                            }
                        ],
                    }
                ],
                "errors": [],
            },
            confirmed=CONFIRMED,
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.summary, "wrote 1 page(s), 1 failure(s)")
        self.assertIn("the page was refused", result.error)
        self.assertEqual(len(result.artifacts), 1)
