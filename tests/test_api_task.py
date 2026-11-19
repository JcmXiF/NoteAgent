"""The writing task through the API seam: search, pick, materials and inspect.

The model is scripted, the nodes are fakes, and the executor runs on the calling
thread unless a test needs a bounded wait to expire. The task is the core's
object: every assertion below reads it through HTTP or its stored shape, never
through a handler's scratch space.
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
from noteagent.acquire.types import Capture
from noteagent.agent.faux import FauxModelClient
from noteagent.project.init import init_project
from noteagent.task.model import (
    Card,
    MaterialBlock,
    SearchCard,
    StagedMaterial,
    Task,
)
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


#: Tells an omitted node (use the test fake) from an explicit ``None`` (let the
#: core build its production node).
_UNSET = object()


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


def _capture(
    url: str = "https://example.com/entropy",
    *,
    title: str = "Entropy",
    fidelity: str = "ok",
) -> Capture:
    return Capture(
        url=url,
        title=title,
        markdown="熵衡量不确定性。",
        html=(
            "<html><head><title>Entropy</title></head><body><article>"
            '<p data-na-block="b1">熵衡量不确定性。</p>'
            '<p data-na-block="b2">信息是不确定性的减少。</p>'
            "</article></body></html>"
        ),
        capture_method="test",
        fidelity=fidelity,
        reasons=(),
    )


def _staged(
    material: str = "raw/web/entropy-a1.html",
    *,
    title: str = "Entropy",
    url: str = "https://example.com/entropy",
    fidelity: str = "ok",
    blocks: tuple[MaterialBlock, ...] = (
        MaterialBlock(id="b1", tag="p", preview="熵衡量不确定性。"),
        MaterialBlock(id="b2", tag="p", preview="信息是不确定性的减少。"),
    ),
    block_count: int | None = None,
) -> StagedMaterial:
    return StagedMaterial(
        material=material,
        title=title,
        url=url,
        kind="html",
        fidelity=fidelity,
        state="staged",
        block_count=len(blocks) if block_count is None else block_count,
        blocks=blocks,
    )


class FakeFetch:
    """A pure fetch node whose page, failure or wait the test chooses."""

    def __init__(self, capture=None, *, error=None, gate=None) -> None:
        self.capture = _capture() if capture is None else capture
        self.error = error
        self.gate = gate
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return self.capture


class FakeStage:
    """A pure stage node whose material, failure or wait the test chooses."""

    def __init__(self, material=None, *, error=None, gate=None) -> None:
        self.material = _staged() if material is None else material
        self.error = error
        self.gate = gate
        self.run_ids: list[str] = []
        self.captures: list[Capture] = []

    def __call__(self, capture: Capture, run_id: str):
        self.captures.append(capture)
        self.run_ids.append(run_id)
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return self.material


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
        fetch=_UNSET,
        stage=_UNSET,
        spawn=None,
        wait_s=15.0,
        pick_wait_s=0.05,
        ids=None,
    ) -> "TestClient":
        # ``None`` means "let the core build the production node"; leaving the
        # argument out gives the test fake. A test that wants the real staging
        # area passes ``stage=None``.
        deps = AppDeps(
            clock=FixedClock(),
            ids=ids or SequentialIds(),
            model=FauxModelClient(*scripts),
            task_search=search if search is not None else FakeSearch(),
            task_fetch=FakeFetch() if fetch is _UNSET else fetch,
            task_stage=FakeStage() if stage is _UNSET else stage,
            task_spawn=spawn,
            task_wait_s=wait_s,
            task_pick_wait_s=pick_wait_s,
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


class PickCardTests(TaskApiCase):
    def test_a_pick_fetches_and_stages_the_card_into_the_task(self) -> None:
        fetch = FakeFetch()
        stage = FakeStage()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("材料好了。").script,
            fetch=fetch,
            stage=stage,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        picked = observations[1]
        self.assertEqual(picked["tool"], "pick_card")
        self.assertEqual(picked["rev"], 2)
        self.assertEqual(picked["fidelity"], "ok")
        self.assertEqual(
            [item["id"] for item in picked["materials"]],
            ["raw/web/entropy-a1.html"],
        )
        self.assertEqual(picked["materials"][0]["title"], "Entropy")
        self.assertEqual(picked["materials"][0]["state"], "staged")
        self.assertEqual(picked["materials"][0]["blockCount"], 2)
        # The chat card carries the summary, not the whole block index.
        self.assertNotIn("blocks", picked["materials"][0])

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual([op.kind for op in task.ops], ["search", "pick"])
        pick = task.ops[1]
        self.assertEqual(pick.actor, "agent")
        self.assertEqual(pick.status, "done")
        self.assertEqual(pick.expected_rev, 1)
        self.assertEqual(pick.input["cardId"], "card-1")

        self.assertEqual(len(task.materials), 1)
        material = task.materials[0]
        self.assertEqual(material["id"], "raw/web/entropy-a1.html")
        self.assertEqual(material["title"], "Entropy")
        self.assertEqual(material["url"], "https://example.com/entropy")
        self.assertEqual(material["fidelity"], "ok")
        self.assertEqual(material["state"], "staged")
        self.assertEqual(material["runId"], "task-1")
        self.assertEqual(material["blockCount"], 2)
        self.assertEqual(
            [block["id"] for block in material["blocks"]], ["b1", "b2"]
        )
        self.assertEqual(material["blocks"][0]["preview"], "熵衡量不确定性。")

        self.assertEqual(fetch.urls, ["https://example.com/entropy"])
        self.assertEqual(stage.run_ids, ["task-1"])

        content = self.last_tool_result_content(session_id, "pick_card")
        self.assertIn("rev 2", content)
        self.assertIn("Entropy", content)
        self.assertIn("inspect", content)

        done = events[-1][1]
        steps = [
            message
            for message in done["session"]["messages"]
            if message.get("isStep")
        ]
        self.assertEqual(steps[-1]["cards"][0]["tool"], "pick_card")
        self.assertEqual(
            steps[-1]["cards"][0]["materials"][0]["id"],
            "raw/web/entropy-a1.html",
        )

    def test_inspect_materials_returns_a_bounded_block_index(self) -> None:
        blocks = tuple(
            MaterialBlock(id=f"b{index}", tag="p", preview=f"第{index}块的预览")
            for index in range(1, 131)
        )
        stage = FakeStage(_staged(blocks=blocks, block_count=130))
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "materials"}'
            ).script,
            FauxModelClient.text("读到了材料。").script,
            stage=stage,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        picked = observations[1]
        self.assertEqual(picked["materials"][0]["blockCount"], 130)

        inspected = observations[2]
        self.assertEqual(inspected["tool"], "inspect")
        self.assertEqual(inspected["rev"], 2)
        material = inspected["materials"][0]
        self.assertEqual(material["id"], "raw/web/entropy-a1.html")
        self.assertEqual(material["blockCount"], 130)
        self.assertEqual(material["blocksOmitted"], 10)
        # The event card is a summary; the index stays in the model's content.
        self.assertNotIn("blocks", material)

        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("[b1]", content)
        self.assertIn("第1块的预览", content)
        self.assertIn("[b120]", content)
        self.assertNotIn("[b121]", content)
        self.assertIn("10 more block(s)", content)

        task = self.task()
        self.assertEqual(len(task.materials[0]["blocks"]), 120)
        self.assertEqual(task.materials[0]["blockCount"], 130)

    def test_an_unknown_card_is_not_found_and_writes_no_material(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-999"}'
            ).script,
            FauxModelClient.text("卡片不存在。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        picked = observations[1]
        self.assertTrue(picked["error"])
        self.assertEqual(picked["diagnostics"][0]["code"], "not_found")
        content = self.last_tool_result_content(session_id, "pick_card")
        self.assertIn("not_found", content)
        self.assertIn("do not retry blindly", content.lower())
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops[1].status, "failed")
        self.assertEqual(task.ops[1].error_code, "not_found")

    def test_a_fetch_failure_is_op_failed_and_writes_no_material(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("抓取失败了。").script,
            fetch=FakeFetch(error=RuntimeError("the route refused")),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        picked = [data for kind, data in events if kind == "observation"][1]
        self.assertTrue(picked["error"])
        self.assertEqual(picked["diagnostics"][0]["code"], "op_failed")
        content = self.last_tool_result_content(session_id, "pick_card")
        self.assertIn("op_failed", content)
        self.assertIn("do not retry blindly", content.lower())

        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertIn("the route refused", task.ops[1].error)
        self.assertEqual(task.ops[1].error_code, "op_failed")

    def test_a_stage_failure_writes_no_half_material(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("暂存失败了。").script,
            stage=FakeStage(error=RuntimeError("the disk refused")),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        picked = [data for kind, data in events if kind == "observation"][1]
        self.assertEqual(picked["diagnostics"][0]["code"], "op_failed")
        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertEqual(task.ops[1].status, "failed")

    def test_a_failed_capture_stages_nothing_even_with_the_real_staging(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("页面质量不合格。").script,
            fetch=FakeFetch(_capture(fidelity="failed")),
            stage=None,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        picked = [data for kind, data in events if kind == "observation"][1]
        self.assertTrue(picked["error"])
        self.assertEqual(picked["diagnostics"][0]["code"], "op_failed")
        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertEqual(task.ops[1].status, "failed")
        # No manifest means no material; the staging area did not get a folder.
        self.assertFalse((self.layout.staging / task.id).exists())
        self.assertEqual(
            list(self.layout.staging.glob(f"{task.id}/*/manifest.json")), []
        )

    def test_a_pick_that_outlives_the_wait_returns_requested_or_running(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        fetch = FakeFetch(gate=gate)
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("还在抓。").script,
            fetch=fetch,
            pick_wait_s=0.05,
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        picked = [data for kind, data in events if kind == "observation"][1]
        self.assertNotIn("error", picked)
        self.assertIn(picked["status"], ("requested", "running"))
        self.assertEqual(picked["rev"], 1)
        content = self.last_tool_result_content(session_id, "pick_card")
        self.assertIn("still", content.lower())
        self.assertIn("do not pick it again", content.lower())

        # The executor is still the writer: letting the fetch finish lands the
        # material in the task even though the model already got its answer.
        gate.set()
        probe = threading.Event()
        task = None
        for _ in range(200):
            task = TaskStore(self.layout).load()
            if task is not None and task.materials:
                break
            probe.wait(0.01)
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(len(task.materials), 1)
        self.assertEqual(task.ops[1].status, "done")

    def test_the_real_staging_writes_the_manifest_block_index_into_the_task(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.text("材料已暂存。").script,
            fetch=FakeFetch(_capture()),
            # Explicit None builds the core's staging node over the temp project.
            stage=None,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        picked = [data for kind, data in events if kind == "observation"][1]
        self.assertEqual(picked["materials"][0]["blockCount"], 2)

        task = self.task()
        material = task.materials[0]
        self.assertTrue(material["id"].startswith("raw/web/"))
        self.assertTrue(material["id"].endswith(".html"))
        self.assertEqual(material["title"], "Entropy")
        self.assertEqual(material["fidelity"], "ok")
        self.assertEqual(
            [block["id"] for block in material["blocks"]], ["b1", "b2"]
        )
        manifests = list(
            (self.layout.staging / task.id).glob("*/manifest.json")
        )
        self.assertEqual(len(manifests), 1)


class HumanPickTests(TaskApiCase):
    def seed_task(self) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=0,
                cards=(
                    Card(
                        id="card-1",
                        title="Entropy",
                        url="https://example.com/entropy",
                        snippet="熵衡量不确定性。",
                        site="example.com",
                    ),
                ),
            )
        )

    def test_a_person_picks_a_card_and_the_chat_inspect_sees_the_material(self) -> None:
        self.seed_task()
        stage = FakeStage()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "materials"}'
            ).script,
            FauxModelClient.text("看到材料了。").script,
            stage=stage,
            spawn=lambda work: work(),
        )

        viewed = client.get("/api/v1/task", params={"view": "cards"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertEqual(viewed.json()["rev"], 0)
        self.assertEqual(viewed.json()["cards"][0]["id"], "card-1")

        picked = client.post(
            "/api/v1/task/pick",
            json={"cardId": "card-1", "expectedRev": 0},
        )
        self.assertEqual(picked.status_code, 200, picked.text)
        body = picked.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["rev"], 1)
        self.assertEqual(body["material"]["id"], "raw/web/entropy-a1.html")
        self.assertEqual(body["material"]["title"], "Entropy")
        self.assertEqual(body["material"]["state"], "staged")

        # The same one task: the chat reads the material the person picked.
        session_id = self.new_session(client)
        events = self.ask(client, session_id)
        observation = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(observation["tool"], "inspect")
        self.assertEqual(observation["rev"], 1)
        self.assertEqual(
            [item["id"] for item in observation["materials"]],
            ["raw/web/entropy-a1.html"],
        )

        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual([op.kind for op in task.ops], ["pick"])
        self.assertEqual(task.ops[0].actor, "human")
        self.assertEqual(task.ops[0].status, "done")
        self.assertEqual(task.ops[0].expected_rev, 0)
        self.assertEqual(task.materials[0]["id"], "raw/web/entropy-a1.html")
        self.assertEqual(stage.run_ids, ["task-7"])

    def test_a_pick_that_outlives_the_wait_returns_the_op_status(self) -> None:
        self.seed_task()
        gate = threading.Event()
        self.addCleanup(gate.set)
        client = self.client(
            fetch=FakeFetch(gate=gate),
            pick_wait_s=0.05,
        )

        response = client.post(
            "/api/v1/task/pick",
            json={"cardId": "card-1", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIn(body["status"], ("requested", "running"))
        self.assertEqual(body["rev"], 0)
        self.assertNotIn("material", body)

        gate.set()
        probe = threading.Event()
        for _ in range(200):
            task = TaskStore(self.layout).load()
            if task is not None and task.materials:
                break
            probe.wait(0.01)
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops[0].status, "done")
        self.assertEqual(task.materials[0]["id"], "raw/web/entropy-a1.html")

    def test_a_person_picking_an_unknown_card_is_not_found(self) -> None:
        self.seed_task()
        client = self.client(spawn=lambda work: work())

        response = client.post(
            "/api/v1/task/pick",
            json={"cardId": "card-999", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "not_found")
        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertEqual(task.ops[0].status, "failed")
        self.assertEqual(task.ops[0].error_code, "not_found")

    def test_a_person_picking_a_page_that_fails_to_fetch_gets_op_failed(self) -> None:
        self.seed_task()
        client = self.client(
            fetch=FakeFetch(error=RuntimeError("no route")),
            spawn=lambda work: work(),
        )

        response = client.post(
            "/api/v1/task/pick",
            json={"cardId": "card-1", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["code"], "op_failed")
        task = self.task()
        self.assertEqual(task.materials, ())
        self.assertEqual(task.ops[0].status, "failed")
        self.assertEqual(task.ops[0].error_code, "op_failed")

    def test_a_stale_view_is_a_conflict_and_writes_no_op(self) -> None:
        self.seed_task()
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, rev=1, intent="a person edited it"))
        stage = FakeStage()
        client = self.client(stage=stage, spawn=lambda work: work())

        response = client.post(
            "/api/v1/task/pick",
            json={"cardId": "card-1", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops, ())
        self.assertEqual(task.materials, ())
        self.assertEqual(stage.run_ids, [])


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
