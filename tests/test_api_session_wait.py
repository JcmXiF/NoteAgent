"""A proposed wait over the API: the stream, the view, and stopping it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from test_api_session_query import _sse

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class SessionWaitApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def app(self, model=None):
        return create_app(
            AppDeps(
                sessions=self.store,
                model=model,
                clock=FixedClock(),
                ids=SequentialIds(),
            )
        )

    def propose(self) -> tuple[list[tuple[str, dict]], dict]:
        model = FauxModelClient.tool_call(
            "p1",
            "propose",
            '{"job":"plan","interface":"source","intent":"plan entropy"}',
            text="我来安排。",
        )
        response = TestClient(self.app(model)).post(
            "/api/v1/sessions/s1/query", json={"content": "安排一下"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        events = _sse(response.text)
        waits = [data for kind, data in events if kind == "wait"]
        self.assertEqual(len(waits), 1)
        return events, waits[0]

    def test_a_proposal_streams_a_wait_and_the_done_session_carries_it(self) -> None:
        events, wait = self.propose()

        self.assertEqual(wait["job"], "plan")
        self.assertEqual(wait["interface"], "source")
        self.assertEqual(wait["completion"], "start_plan")
        self.assertEqual(wait["status"], "proposed")
        self.assertEqual(wait["intent"], "plan entropy")
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["run"]["status"], "waiting")
        session = events[-1][1]["session"]
        self.assertEqual(session["wait"]["waitId"], wait["waitId"])
        self.assertEqual(
            [(message["role"], message["content"]) for message in session["messages"]],
            [("user", "安排一下"), ("assistant", "我来安排。")],
        )

        reloaded = TestClient(self.app()).get("/api/v1/sessions/s1").json()
        self.assertEqual(reloaded["wait"]["waitId"], wait["waitId"])
        self.assertEqual(reloaded["wait"]["status"], "proposed")
        self.assertTrue(
            any(
                entry["type"] == "wait_proposed"
                for entry in self.store.load("s1").entries
            )
        )

    def test_stopping_a_proposed_wait_records_it_and_the_view_says_cancelled(
        self,
    ) -> None:
        _events, wait = self.propose()

        response = TestClient(self.app()).post(
            f"/api/v1/sessions/s1/wait/{wait['waitId']}/cancel"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["cancelled"])
        self.assertEqual(body["wait"]["status"], "cancelled")

        reloaded = TestClient(self.app()).get("/api/v1/sessions/s1").json()
        self.assertEqual(reloaded["wait"]["status"], "cancelled")
        self.assertTrue(
            any(
                entry["type"] == "wait_cancelled"
                for entry in self.store.load("s1").entries
            )
        )

        again = TestClient(self.app()).post(
            f"/api/v1/sessions/s1/wait/{wait['waitId']}/cancel"
        )
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["cancelled"])

    def test_a_wait_that_entered_cannot_be_stopped_from_the_chat(self) -> None:
        waits = SessionWaitStore(self.store)
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

        response = TestClient(self.app()).post(
            "/api/v1/sessions/s1/wait/w1/cancel"
        )

        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["code"], "wait.notCancelable")
        self.assertEqual(body["params"]["status"], "entered")
        self.assertFalse(
            any(
                entry["type"] == "wait_cancelled"
                for entry in self.store.load("s1").entries
            )
        )

    def test_a_wait_that_does_not_exist_is_not_found(self) -> None:
        response = TestClient(self.app()).post(
            "/api/v1/sessions/s1/wait/missing/cancel"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "wait.notFound")

    def test_a_wait_on_a_missing_session_is_not_found(self) -> None:
        response = TestClient(self.app()).post(
            "/api/v1/sessions/nope/wait/w1/cancel"
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "session.notFound")


if __name__ == "__main__":
    unittest.main()
