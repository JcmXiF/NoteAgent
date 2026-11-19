"""The writing task through the API seam: a search, its cards, and inspect.

The model is scripted, the search node is a fake, and the executor runs on the
calling thread unless a test needs the bounded wait to expire. The task is the
core's object: every assertion below reads it through HTTP or its stored shape,
never through a handler's scratch space.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.project.init import init_project
from noteagent.task.model import SearchCard
from noteagent.task.store import TaskStore

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


CARDS = (
    SearchCard(
        title="Entropy",
        url="https://example.com/entropy",
        snippet="熵衡量不确定性。",
        site="example.com",
    ),
    SearchCard(
        title="Information",
        url="https://example.com/information",
        snippet="信息是不确定性的减少。",
        site="example.com",
    ),
)


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


class FakeSearch:
    """A pure search node whose answer, failure or wait the test chooses."""

    def __init__(self, cards=CARDS, *, error=None, gate=None) -> None:
        self.cards = list(cards)
        self.error = error
        self.gate = gate
        self.queries: list[str] = []

    def __call__(self, query: str):
        self.queries.append(query)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return list(self.cards)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class TaskApiCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.layout = init_project(self.repo / "projects" / "demo")
        patcher = patch.object(projects_mod, "REPO_ROOT", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        projects_mod.use_project("demo")

    def client(
        self,
        *scripts,
        search=None,
        spawn=None,
        wait_s=15.0,
        ids=None,
    ) -> "TestClient":
        deps = AppDeps(
            clock=FixedClock(),
            ids=ids or SequentialIds(),
            model=FauxModelClient(*scripts),
            task_search=search if search is not None else FakeSearch(),
            task_spawn=spawn,
            task_wait_s=wait_s,
        )
        return TestClient(create_app(deps))

    def new_session(self, client) -> str:
        created = client.post("/api/v1/sessions", json={"title": "Writing"})
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["id"]

    def ask(self, client, session_id: str, question: str = "搜一下") -> list[tuple[str, dict]]:
        response = client.post(
            f"/api/v1/sessions/{session_id}/query",
            json={"content": question},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return _sse(response.text)

    def task(self):
        stored = TaskStore(self.layout).load()
        self.assertIsNotNone(stored)
        return stored

    def tool_results(self, session_id: str, name: str) -> list[dict]:
        entries = self.layout.session_path(session_id).read_text(encoding="utf-8")
        records = [
            json.loads(line)
            for line in entries.splitlines()
            if line.strip()
        ]
        return [
            entry
            for entry in records
            if entry.get("role") == "toolResult" and entry.get("name") == name
        ]

    def last_tool_result_content(self, session_id: str, name: str) -> str:
        return self.tool_results(session_id, name)[-1]["content"]


class WebSearchTests(TaskApiCase):
    def test_a_search_becomes_cards_in_the_task_and_a_card_in_the_chat(self) -> None:
        search = FakeSearch()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("找到了两篇。").script,
            search=search,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        self.assertEqual(len(observations), 1)
        card = observations[0]
        self.assertEqual(card["tool"], "web_search")
        self.assertEqual(card["query"], "entropy")
        self.assertEqual(card["hits"], 2)
        self.assertEqual(card["rev"], 1)
        self.assertEqual(
            [item["title"] for item in card["cards"]],
            ["Entropy", "Information"],
        )
        self.assertEqual(
            [item["url"] for item in card["cards"]],
            ["https://example.com/entropy", "https://example.com/information"],
        )
        self.assertEqual(search.queries, ["entropy"])

        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(
            [(item.title, item.url) for item in task.cards],
            [(item.title, item.url) for item in CARDS],
        )
        self.assertEqual(len(task.ops), 1)
        op = task.ops[0]
        self.assertEqual(op.kind, "search")
        self.assertEqual(op.actor, "agent")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 0)
        self.assertEqual(op.input["query"], "entropy")

        content = self.last_tool_result_content(session_id, "web_search")
        self.assertIn("rev 1", content)
        self.assertIn("https://example.com/entropy", content)
        self.assertNotIn("expectedRev", content)
        self.assertNotIn("ops", content)

        done = events[-1][1]
        step = next(
            message
            for message in done["session"]["messages"]
            if message.get("isStep")
        )
        self.assertEqual(step["cards"][0]["tool"], "web_search")

    def test_inspect_returns_a_bounded_view_and_the_same_rev(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "cards"}'
            ).script,
            FauxModelClient.text("看过了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        summary = observations[1]
        self.assertEqual(summary["tool"], "inspect")
        self.assertEqual(summary["rev"], 1)
        self.assertIn("summary", summary["title"])

        cards = observations[2]
        self.assertEqual(cards["tool"], "inspect")
        self.assertEqual(cards["rev"], 1)
        self.assertEqual(cards["hits"], 2)

        cards_content = self.last_tool_result_content(session_id, "inspect")
        # Both inspect results are written; the last one is the cards view.
        self.assertIn("card-1", cards_content)
        self.assertIn("Entropy", cards_content)
        self.assertNotIn("materials", cards_content)

    def test_an_empty_search_is_a_normal_empty_view(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "nothing"}'
            ).script,
            FauxModelClient.text("没有结果。").script,
            search=FakeSearch(cards=()),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(card["hits"], 0)
        self.assertEqual(card["cards"], [])
        self.assertNotIn("error", card)
        task = self.task()
        self.assertEqual(task.cards, ())
        self.assertEqual(task.ops[0].status, "done")


class TaskSurvivalTests(TaskApiCase):
    def test_a_summary_read_creates_the_task_before_any_search(self) -> None:
        client = self.client()

        response = client.get("/api/v1/task", params={"view": "summary"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["view"], "summary")
        self.assertEqual(body["rev"], 0)
        self.assertEqual(body["status"], "idle")
        self.assertEqual(body["counts"]["cards"], 0)
        self.assertEqual(self.task().id, "task-1")

    def test_the_task_and_the_chat_card_survive_a_restart(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("找到了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        # A new core reads the same project: a new deps, a new app, no shared
        # in-memory state beyond the files under .noteagent.
        restarted = self.client()

        task = restarted.get("/api/v1/task", params={"view": "cards"})
        self.assertEqual(task.status_code, 200, task.text)
        body = task.json()
        self.assertEqual(body["rev"], 1)
        self.assertEqual(
            [card["title"] for card in body["cards"]],
            ["Entropy", "Information"],
        )

        session = restarted.get(f"/api/v1/sessions/{session_id}")
        self.assertEqual(session.status_code, 200, session.text)
        step = next(
            message
            for message in session.json()["messages"]
            if message.get("isStep")
        )
        self.assertEqual(step["cards"][0]["tool"], "web_search")
        self.assertEqual(step["cards"][0]["rev"], 1)
        self.assertEqual(
            [card["title"] for card in step["cards"][0]["cards"]],
            ["Entropy", "Information"],
        )


class SearchFailureTests(TaskApiCase):
    def test_a_failed_node_is_an_op_failed_observation_that_says_do_not_retry(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("搜索失败了。").script,
            search=FakeSearch(error=RuntimeError("the engine refused")),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertTrue(card["error"])
        self.assertEqual(card["diagnostics"][0]["code"], "op_failed")
        self.assertIn("op_failed", card["diagnostics"][0]["code"])

        content = self.last_tool_result_content(session_id, "web_search")
        self.assertIn("op_failed", content)
        self.assertIn("do not retry blindly", content.lower())
        task = self.task()
        self.assertEqual(task.ops[0].status, "failed")
        self.assertIn("the engine refused", task.ops[0].error)
        self.assertEqual(task.cards, ())

    def test_a_stale_expected_rev_is_a_conflict_and_writes_no_op(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("读过了。").script,
            FauxModelClient.tool_call(
                "t2", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "cards"}'
            ).script,
            FauxModelClient.tool_call(
                "t4", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("重新读过，再搜一次。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        # The first inspect creates the task at rev 0 and the session observes it.
        store = TaskStore(self.layout)
        self.assertEqual(store.load(), None)
        self.ask(client, session_id, "先看看任务")
        observed = store.load()
        self.assertIsNotNone(observed)
        self.assertEqual(observed.rev, 0)

        # A person's edit lands between the observation and the next mutation.
        store.save(replace(observed, rev=observed.rev + 1, intent="human edit"))

        events = self.ask(client, session_id, "搜索一下")

        observations = [data for kind, data in events if kind == "observation"]
        self.assertEqual(observations[0]["tool"], "web_search")
        self.assertTrue(observations[0]["error"])
        self.assertEqual(observations[0]["diagnostics"][0]["code"], "conflict")
        contents = [
            entry["content"]
            for entry in self.tool_results(session_id, "web_search")
        ]
        self.assertTrue(any("conflict" in item for item in contents))
        self.assertTrue(any("do not retry blindly" in item.lower() for item in contents))
        self.assertTrue(any("inspect" in item.lower() for item in contents))

        task = self.task()
        # The conflict wrote no op; the inspect after it saw the new rev, so the
        # retried search is the one search recorded.
        self.assertEqual([op.kind for op in task.ops], ["search"])
        self.assertEqual(task.ops[0].status, "done")
        self.assertEqual(task.rev, 2)
        self.assertEqual(len(task.cards), 2)
        self.assertEqual(observations[1]["rev"], 1)
        self.assertEqual(observations[1]["hits"], 0)
        self.assertEqual(observations[2]["rev"], 2)
        self.assertEqual(observations[2]["hits"], 2)


class BoundedWaitTests(TaskApiCase):
    def test_a_search_that_outlives_the_wait_returns_requested_or_running(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
            search=search,
            wait_s=0.05,
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertNotIn("error", card)
        self.assertIn(card["status"], ("requested", "running"))
        self.assertEqual(card["rev"], 0)
        content = self.last_tool_result_content(session_id, "web_search")
        self.assertIn("still", content.lower())
        self.assertIn("do not start it again", content.lower())

        # The executor is still the writer: letting the node finish lands the
        # cards in the task even though the model already got its answer.
        gate.set()
        deadline = threading.Event()
        for _ in range(200):
            task = TaskStore(self.layout).load()
            if task is not None and task.ops and task.ops[0].status == "done":
                break
            deadline.wait(0.01)
        task = self.task()
        self.assertEqual(task.ops[0].status, "done")
        self.assertEqual(len(task.cards), 2)
        self.assertEqual(task.rev, 1)


if __name__ == "__main__":
    unittest.main()
