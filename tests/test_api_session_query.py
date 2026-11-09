"""One question over SSE: status, the answer as it is written, then done."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_fakes import FakeRuntime, FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import RunRegistry
from noteagent.agent.model import ModelErrorEvent, ModelStartEvent
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.wiki.agent_tools import wiki_tools

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
    from noteagent.api.session_access import session_tools
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]
    session_tools = None  # type: ignore[misc, assignment]


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


class WikiToolApiTests(unittest.TestCase):
    """The loop, the real wiki tools and the SSE stream, with a faux model."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        pages = {
            "wiki/concepts/entropy.md": (
                '---\ntitle: "Entropy"\n---\n\n熵衡量不确定性。'
            ),
            "wiki/concepts/information.md": (
                '---\ntitle: "Information"\n---\n\n信息是不确定性的减少。'
            ),
        }
        for relative, text in pages.items():
            path = self.layout.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.store = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        self.store.create("s1")

    def ask(self, *scripts):
        deps = AppDeps(
            sessions=self.store,
            model=FauxModelClient(*scripts),
            tools=wiki_tools(self.layout),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        response = TestClient(create_app(deps)).post(
            "/api/v1/sessions/s1/query", json={"content": "什么是熵？"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        events = _sse(response.text)
        observations = [data for kind, data in events if kind == "observation"]
        return events, observations

    def test_a_wiki_read_is_a_card_and_its_page_stays_with_the_model(self) -> None:
        events, observations = self.ask(
            FauxModelClient.tool_call(
                "t1", "wiki_read", '{"slug": "concepts/entropy"}'
            ).script,
            FauxModelClient.text("熵衡量不确定性。").script,
        )

        self.assertEqual(len(observations), 1)
        card = observations[0]
        self.assertEqual(card["tool"], "wiki_read")
        self.assertEqual(card["path"], "wiki/concepts/entropy.md")
        self.assertEqual(card["slug"], "concepts/entropy")
        self.assertEqual(card["title"], "Entropy")
        self.assertNotIn("熵衡量不确定性", json.dumps(card, ensure_ascii=False))

        done = events[-1][1]
        step = next(
            message
            for message in done["session"]["messages"]
            if message.get("isStep")
        )
        self.assertEqual(step["cards"][0]["path"], "wiki/concepts/entropy.md")
        entries = self.store.load("s1").entries
        self.assertEqual(
            [entry["role"] for entry in entries],
            ["user", "assistant", "toolResult", "assistant"],
        )

    def test_a_search_returns_locator_cards_before_the_answer(self) -> None:
        events, observations = self.ask(
            FauxModelClient.tool_call(
                "t1", "search", '{"query": "不确定性"}'
            ).script,
            FauxModelClient.text("熵和信息的共同点是不确定性。").script,
        )

        self.assertEqual(len(observations), 1)
        card = observations[0]
        self.assertEqual(card["tool"], "search")
        self.assertEqual(card["scope"], "wiki")
        self.assertEqual(card["query"], "不确定性")
        self.assertGreaterEqual(card["hits"], 2)
        self.assertEqual(events[-1][0], "done")


class DefaultToolTests(unittest.TestCase):
    def test_the_default_run_offers_the_projects_wiki_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(Path(tmp))
            tools = session_tools(AppDeps(sessions=None), layout)

        self.assertEqual(
            [tool.name for tool in tools.enabled()], ["wiki_read", "search"]
        )

    def test_a_test_store_without_tools_gets_a_bare_run(self) -> None:
        tools = session_tools(AppDeps(sessions=FakeSessionStore()), None)

        self.assertEqual(tools.enabled(), ())


if __name__ == "__main__":
    unittest.main()
