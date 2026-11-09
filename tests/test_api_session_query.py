"""One question over SSE: status, the answer as it is written, then done."""

from __future__ import annotations

import json
import unittest

from agent_fakes import FakeRuntime, FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import RunRegistry
from noteagent.agent.model import ModelErrorEvent, ModelStartEvent

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        if not block or block.startswith(":"):
            continue
        kind = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if kind:
            events.append((kind, json.loads(data)))
    return events


class SessionQueryTests(unittest.TestCase):
    def test_a_question_streams_an_answer_and_a_done_session(self) -> None:
        store = FakeSessionStore()
        store.create("s1", "Entropy")
        deps = AppDeps(
            sessions=store,
            model=FauxModelClient.text("答案", chunks=["答", "案"]),
            clock=FixedClock(),
            ids=SequentialIds(),
        )

        response = TestClient(create_app(deps)).post(
            "/api/v1/sessions/s1/query", json={"content": "问题"}
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])
        events = _sse(response.text)
        self.assertEqual([data["seq"] for _kind, data in events], list(range(1, len(events) + 1)))
        self.assertEqual(events[0][0], "status")
        self.assertEqual(
            "".join(data["text"] for kind, data in events if kind == "token"),
            "答案",
        )
        self.assertEqual(events[-1][0], "done")
        done = events[-1][1]
        self.assertEqual(done["run"]["status"], "completed")
        self.assertEqual(
            [message["role"] for message in done["session"]["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(done["session"]["messages"][-1]["content"], "答案")
        self.assertEqual(
            [entry["role"] for entry in store.load("s1").entries],
            ["user", "assistant"],
        )

    def test_a_model_failure_is_an_error_event_and_no_answer_entry(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        deps = AppDeps(
            sessions=store,
            model=FauxModelClient(
                [
                    ModelStartEvent("faux"),
                    ModelErrorEvent("model_error", "the model refused", retryable=True),
                ]
            ),
        )

        response = TestClient(create_app(deps)).post(
            "/api/v1/sessions/s1/query", json={"content": "问题"}
        )

        events = _sse(response.text)
        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "model_error")
        self.assertTrue(events[-1][1]["retryable"])
        self.assertEqual(
            [entry["role"] for entry in store.load("s1").entries],
            ["user"],
        )

    def test_a_missing_session_is_refused_before_the_stream_starts(self) -> None:
        client = TestClient(create_app(AppDeps(sessions=FakeSessionStore())))

        response = client.post(
            "/api/v1/sessions/missing/query", json={"content": "问题"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "session.notFound")

    def test_a_second_question_is_refused_while_the_session_is_running(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        runs = RunRegistry()
        deps = AppDeps(sessions=store, runs=runs)
        runs.claim("s1")
        try:
            response = TestClient(create_app(deps)).post(
                "/api/v1/sessions/s1/query", json={"content": "第二问"}
            )
        finally:
            runs.release("s1")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "busy")
        self.assertEqual(store.load("s1").entries, ())

    def test_cancel_asks_the_thread_that_owns_the_run_to_stop(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        runtime = FakeRuntime()
        runtime.running.add("s1")

        response = TestClient(
            create_app(AppDeps(sessions=store, runtime=runtime))
        ).post("/api/v1/sessions/s1/cancel")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"cancelled": True})
        self.assertEqual(runtime.cancelled, ["s1"])

    def test_cancelling_an_idle_session_changes_nothing(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        runtime = FakeRuntime()

        response = TestClient(
            create_app(AppDeps(sessions=store, runtime=runtime))
        ).post("/api/v1/sessions/s1/cancel")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"cancelled": False})
        self.assertEqual(runtime.cancelled, [])

    def test_cancelling_a_session_that_went_away_is_not_found(self) -> None:
        client = TestClient(create_app(AppDeps(sessions=FakeSessionStore())))

        response = client.post("/api/v1/sessions/missing/cancel")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "session.notFound")


if __name__ == "__main__":
    unittest.main()
