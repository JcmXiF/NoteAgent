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
import time
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import Capture
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.task_op import task_op_result_entry
from noteagent.effects.journal import COMMITTED, WIKI_PAGE_WRITE, EffectJournal
from noteagent.ingest.page_history import blob_digest
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.project.init import init_project
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.task.model import (
    Approval,
    Card,
    Frame,
    Material,
    MaterialBlock,
    Op,
    SearchCard,
    StagedMaterial,
    Task,
    WriteRequest,
    material_from_dict,
)
from noteagent.task.service import TaskService
from noteagent.task.store import TaskStore, TaskUnreadableError
from noteagent.task.writing import PageLocks, SaveOutcome
from noteagent.util.tempdir import remove_tree

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
        self.exited = threading.Event()

    def __call__(self, query: str):
        self.queries.append(query)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            if self.error is not None:
                raise self.error
            return list(self.cards)
        finally:
            self.exited.set()


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


def _composed_page(request) -> FileBlock:
    """The block a composed page would be: frontmatter, sources and a marker."""
    sources = ", ".join(f'"{locator}"' for locator in request.locators)
    sections = []
    for index, section in enumerate(request.spec["sections"]):
        marker = (
            f"<!-- na-clip:{request.material} -->\n" if index == 0 else ""
        )
        blocks = ", ".join(section["blocks"])
        sections.append(
            f"{marker}## {section['title']}\n\ncomposed from {blocks}"
        )
    return FileBlock(
        path=request.wiki_path,
        content=(
            "---\n"
            f"title: {request.page_name}\n"
            "created: 2026-11-10\n"
            "updated: 2026-11-10\n"
            f"sources: [{sources}]\n"
            "---\n\n"
            + "\n\n".join(sections)
            + "\n"
        ),
    )


class FakeCompose:
    """A pure compose node whose page, failure or wait the test chooses."""

    def __init__(self, block=None, *, error=None, gate=None, on_call=None) -> None:
        self.block = block
        self.error = error
        self.gate = gate
        self.on_call = on_call
        self.requests = []
        self.cancels = []
        self.entered = threading.Event()
        self.exited = threading.Event()

    def __call__(self, layout, request, cancel=None):
        self.requests.append(request)
        self.cancels.append(cancel)
        self.entered.set()
        try:
            if self.gate is not None:
                self.gate.wait(5)
            if self.on_call is not None:
                self.on_call(request)
            if self.error is not None:
                raise self.error
            if self.block is not None:
                return self.block
            return _composed_page(request)
        finally:
            self.exited.set()


class GapPageLocks(PageLocks):
    """Delay the task's first page-lock acquisition until the test says so.

    This opens exactly the gap a person's save could land in: the executor has
    finished composing and is about to take the page lock. The test saves the
    page through the human route in that gap, then lets the executor continue.
    """

    def __init__(self) -> None:
        super().__init__()
        self.arrived = threading.Event()
        self.release = threading.Event()
        self._delay = True

    @contextmanager
    def holding(self, project_key: str, page: str):
        if self._delay:
            self._delay = False
            self.arrived.set()
            self.release.wait(5)
        with super().holding(project_key, page):
            yield


class ReleasingPageLocks(PageLocks):
    """Records which task ops exist while the page lock is still held.

    The route must record the person's edit inside the page lock, so a probe at
    release time still sees it.
    """

    def __init__(self, layout) -> None:
        super().__init__()
        self.layout = layout
        self.ops_at_release: list[list[str]] = []

    @contextmanager
    def holding(self, project_key: str, page: str):
        with super().holding(project_key, page):
            yield
            stored = TaskStore(self.layout).load()
            self.ops_at_release.append(
                [op.kind for op in stored.ops] if stored is not None else []
            )


class HoldingModel:
    """A scripted model whose chosen call holds before it answers.

    It is how a test keeps the original turn running while the executor
    finishes the op the turn stopped waiting for.
    """

    def __init__(self, model: FauxModelClient, *, call: int) -> None:
        self._model = model
        self._target = call
        self._calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.requests = model.requests

    def stream(self, request):
        self._calls += 1
        if self._calls == self._target:
            self.entered.set()
            self.release.wait(5)
        yield from self._model.stream(request)


class RecordingResume:
    """The op-completion callback, recorded instead of starting a run.

    A test that only wants to see the delta hands this in; a test that wants the
    production continuation passes ``resume=None`` so the app builds its own.
    """

    def __init__(self) -> None:
        self.notices: list = []

    def __call__(self, notice) -> None:
        self.notices.append(notice)


class _FailingEditService(TaskService):
    """A real task service whose page-edit record cannot be written.

    It is handed in through ``AppDeps.tasks``, the composition seam, rather than
    patched onto the class: the route reaches the same service through the same
    factory a production request uses, and only this one operation fails.
    """

    def record_page_edit(self, path: str, revision: str = "") -> object:
        raise RuntimeError("the task file is on a full disk")


class _CountingStore(TaskStore):
    """A task store that counts loads, to prove a read takes one snapshot."""

    def __init__(self, layout) -> None:
        super().__init__(layout)
        self.loads = 0

    def load(self):
        self.loads += 1
        return super().load()


class FakeSave:
    """A pure save node whose outcome, failure or wait the test chooses."""

    def __init__(self, outcome=None, *, error=None, gate=None) -> None:
        self.outcome = outcome
        self.error = error
        self.gate = gate
        self.requests = []
        self.entered = threading.Event()

    def __call__(self, layout, request, cancel=None):
        self.requests.append(request)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        if self.outcome is not None:
            return self.outcome
        return SaveOutcome(
            status="written",
            written=(request.wiki_path,),
            summary=f"wrote {request.wiki_path}",
        )


@unittest.skipIf(TestClient is None, "fastapi not installed")
class TaskApiCase(unittest.TestCase):
    def setUp(self) -> None:
        # A background task can still be closing a task lock or an event log
        # when the test ends; the helper waits that instant out on Windows.
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(remove_tree, self.repo)
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
        compose=_UNSET,
        save=None,
        spawn=None,
        wait_s=15.0,
        pick_wait_s=0.05,
        compose_wait_s=180.0,
        page_locks=None,
        ids=None,
        resume=_UNSET,
        tasks=None,
    ) -> "TestClient":
        # ``None`` means "let the core build the production node"; leaving the
        # argument out gives the test fake. A test that wants the real staging
        # area passes ``stage=None``; ``save=None`` is the real writer, which is
        # what a test that inspects the page on disk wants. ``resume=None`` is
        # the production op-completion callback; the default records the notice
        # so a bounded wait cannot start an unexpected background run.
        deps = AppDeps(
            clock=FixedClock(),
            ids=ids or SequentialIds(),
            model=FauxModelClient(*scripts),
            task_search=search if search is not None else FakeSearch(),
            task_fetch=FakeFetch() if fetch is _UNSET else fetch,
            task_stage=FakeStage() if stage is _UNSET else stage,
            task_compose=FakeCompose() if compose is _UNSET else compose,
            task_save=save,
            task_compose_wait_s=compose_wait_s,
            task_page_locks=page_locks or PageLocks(),
            task_spawn=spawn,
            task_wait_s=wait_s,
            task_pick_wait_s=pick_wait_s,
            task_resume=RecordingResume() if resume is _UNSET else resume,
            tasks=tasks,
        )
        client = TestClient(create_app(deps))
        # Cleanups run last-registered-first, so the client closes before the
        # temporary project is removed.
        self.addCleanup(client.close)
        return client

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

    def wait_for_task(self, predicate, *, timeout: float = 5.0):
        """Poll the stored task until it reaches the state the test expects.

        A load can still lose the race with an atomic replace under heavy load
        (the store retries briefly, but this poll is the outer loop); the next
        poll reads the file the writer finished. Tolerating that here keeps a
        transient lock from masking the assertion the test is actually making.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                stored = TaskStore(self.layout).load()
            except TaskUnreadableError:
                stored = None
            if stored is not None and predicate(stored):
                return stored
            threading.Event().wait(0.01)
        self.fail("the task did not reach the expected state")

    def session_entries(self, session_id: str) -> list[dict]:
        text = self.layout.session_path(session_id).read_text(encoding="utf-8")
        return [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]

    def wait_for_session(self, session_id: str, predicate, *, timeout: float = 5.0):
        """Poll the committed session log until the resumed run lands."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entries = self.session_entries(session_id)
            if predicate(entries):
                return entries
            threading.Event().wait(0.01)
        self.fail("the session did not reach the expected state")

    def wait_for_resume_done(self, client, session_id: str, answer: str):
        """Wait for the resumed run's answer, terminal marker and free slot.

        The test harness must not clean the temp project while a background
        resume is still writing; the marker is the last thing the run commits,
        and the slot is released after it.
        """

        def done(entries: list[dict]) -> bool:
            markers = [
                entry
                for entry in entries
                if entry.get("type") == "run_marker"
            ]
            answers = [
                entry
                for entry in entries
                if entry.get("type") == "message"
                and entry.get("role") == "assistant"
                and entry.get("content") == answer
            ]
            return (
                bool(answers)
                and sum(
                    1 for marker in markers if marker.get("state") == "running"
                )
                >= 2
                and bool(markers)
                and markers[-1].get("state") == "finished"
            )

        entries = self.wait_for_session(session_id, done)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = client.get(f"/api/v1/sessions/{session_id}").json()["state"]
            if not state.get("busy"):
                return entries
            threading.Event().wait(0.01)
        self.fail("the resumed run did not release the session")
        return entries

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
        self.assertEqual(material.id, "raw/web/entropy-a1.html")
        self.assertEqual(material.title, "Entropy")
        self.assertEqual(material.url, "https://example.com/entropy")
        self.assertEqual(material.fidelity, "ok")
        self.assertEqual(material.state, "staged")
        self.assertEqual(material.run_id, "task-1")
        self.assertEqual(material.block_count, 2)
        self.assertEqual(
            [block.id for block in material.blocks], ["b1", "b2"]
        )
        self.assertEqual(material.blocks[0].preview, "熵衡量不确定性。")

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
        self.assertEqual(len(task.materials[0].blocks), 120)
        self.assertEqual(task.materials[0].block_count, 130)

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
        self.assertTrue(material.id.startswith("raw/web/"))
        self.assertTrue(material.id.endswith(".html"))
        self.assertEqual(material.title, "Entropy")
        self.assertEqual(material.fidelity, "ok")
        self.assertEqual(
            [block.id for block in material.blocks], ["b1", "b2"]
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
        self.assertEqual(task.materials[0].id, "raw/web/entropy-a1.html")
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
        # The op lands on a background thread; a loaded CI machine can be slow
        # to finish the staging write. Wait for the material, not for a fixed
        # two seconds, or this test flakes on timing rather than behaviour.
        for _ in range(1000):
            task = TaskStore(self.layout).load()
            if task is not None and task.materials:
                break
            probe.wait(0.01)
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops[0].status, "done")
        self.assertEqual(task.materials[0].id, "raw/web/entropy-a1.html")

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


class FrameBlocksTests(TaskApiCase):
    def test_the_agent_frames_blocks_and_the_task_holds_the_frame(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", '
                '"blocks": ["b2", "b1"]}',
            ).script,
            FauxModelClient.text("画好了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        framed = observations[2]
        self.assertEqual(framed["tool"], "frame_blocks")
        self.assertEqual(framed["rev"], 3)
        self.assertEqual(len(framed["frames"]), 1)
        self.assertEqual(
            framed["frames"][0]["docId"], "raw/web/entropy-a1.html"
        )
        self.assertEqual(framed["frames"][0]["source"], "agent")
        self.assertEqual(framed["frames"][0]["blockCount"], 2)

        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual([op.kind for op in task.ops], ["search", "pick", "frame"])
        op = task.ops[2]
        self.assertEqual(op.actor, "agent")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 2)
        self.assertEqual(op.input["docId"], "raw/web/entropy-a1.html")
        self.assertEqual(op.input["blocks"], ["b2", "b1"])

        self.assertEqual(len(task.frames), 1)
        frame = task.frames[0]
        self.assertEqual(frame.doc_id, "raw/web/entropy-a1.html")
        self.assertEqual(frame.source, "agent")
        # A frame is a set of blocks; the task keeps them in document order.
        self.assertEqual(frame.blocks, ("b1", "b2"))

        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("rev 3", content)
        self.assertIn("raw/web/entropy-a1.html", content)
        self.assertIn("[b1]", content)
        self.assertIn("[b2]", content)
        self.assertNotIn("expectedRev", content)
        self.assertNotIn("ops", content)


class FrameValidationTests(TaskApiCase):
    def test_a_block_past_the_preview_cap_can_still_be_framed(self) -> None:
        blocks = tuple(
            MaterialBlock(
                id=f"b{index}", tag="p", preview=f"第{index}段的预览"
            )
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
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", '
                '"blocks": ["b130", "b1"]}',
            ).script,
            FauxModelClient.tool_call(
                "t4", "inspect", '{"view": "materials"}'
            ).script,
            FauxModelClient.text("框好了。").script,
            stage=stage,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        framed = observations[2]
        self.assertNotIn("error", framed)
        self.assertEqual(framed["frames"][0]["blockCount"], 2)

        task = self.task()
        self.assertEqual(task.frames[0].blocks, ("b1", "b130"))
        # The preview index stays bounded; only the identity list is complete.
        self.assertEqual(len(task.materials[0].blocks), 120)
        self.assertEqual(len(task.materials[0].block_ids), 130)

        material = observations[3]["materials"][0]
        self.assertEqual(material["blockCount"], 130)
        self.assertEqual(material["blocksOmitted"], 10)
        # The full identity list is the task's own bookkeeping, not a view.
        self.assertNotIn("blockIds", material)

    def test_an_unknown_block_is_bad_spec_and_writes_no_frame(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", '
                '"blocks": ["b1", "b999"]}',
            ).script,
            FauxModelClient.text("框不对。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        framed = [data for kind, data in events if kind == "observation"][2]
        self.assertTrue(framed["error"])
        self.assertEqual(framed["diagnostics"][0]["code"], "bad_spec")
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("bad_spec", content)
        self.assertIn("do not retry blindly", content.lower())
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(task.frames, ())
        self.assertEqual([op.kind for op in task.ops], ["search", "pick"])

    def test_an_unknown_document_is_not_found_and_writes_no_frame(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/missing.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.text("没有这份材料。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        framed = [data for kind, data in events if kind == "observation"][2]
        self.assertTrue(framed["error"])
        self.assertEqual(framed["diagnostics"][0]["code"], "not_found")
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("not_found", content)
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(task.frames, ())
        self.assertEqual([op.kind for op in task.ops], ["search", "pick"])

    def test_an_agent_frame_after_a_person_edited_the_task_is_a_conflict(self) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=1,
                materials=(_stored_material(),),
            )
        )
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("看到了。").script,
            FauxModelClient.tool_call(
                "t2",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.text("画不了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        # The session observes rev 1; then a person's edit moves the task.
        self.ask(client, session_id, "先看看任务")
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, rev=2, intent="a person edited it"))

        events = self.ask(client, session_id, "画个框")

        framed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(framed["error"])
        self.assertEqual(framed["diagnostics"][0]["code"], "conflict")
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("conflict", content)
        self.assertIn("do not retry blindly", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(task.frames, ())
        self.assertEqual(task.ops, ())

    def test_an_empty_frame_is_bad_spec(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": []}',
            ).script,
            FauxModelClient.text("框是空的。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        framed = [data for kind, data in events if kind == "observation"][2]
        self.assertTrue(framed["error"])
        self.assertEqual(framed["diagnostics"][0]["code"], "bad_spec")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(task.frames, ())


def _stored_material(
    material: str = "raw/web/entropy-a1.html",
    *,
    block_ids: tuple[str, ...] = ("b1", "b2"),
) -> Material:
    """A material as the task stores it, for a test that seeds a task."""
    return material_from_dict(
        {
            "id": material,
            "title": "Entropy",
            "url": "https://example.com/entropy",
            "kind": "html",
            "fidelity": "ok",
            "state": "staged",
            "runId": "task-7",
            "blockCount": len(block_ids),
            "blockIds": list(block_ids),
            "blocks": [
                {"id": block, "tag": "p", "preview": f"{block} 的预览"}
                for block in block_ids
            ],
        }
    )


def _seed_approval() -> Approval:
    return Approval(
        frame_id="frame-1",
        spec={
            "slug": "entropy",
            "title": "Entropy",
            "action": "create",
            "sections": [{"title": "What it is", "blocks": ["b1"]}],
        },
        rev=2,
        by="human",
        at="2026-11-10T09:05:00Z",
    )


def _seed_approved_task(spec: dict | None = None, *, rev: int = 3) -> Task:
    """A task at *rev* with a person's matching approval for one frame."""
    task = Task(
        id="task-7",
        created_at="2026-11-10T09:00:00Z",
        updated_at="2026-11-10T09:05:00Z",
        rev=rev,
        materials=(_stored_material(),),
        frames=(
            Frame(
                id="frame-1",
                doc_id="raw/web/entropy-a1.html",
                blocks=("b1", "b2"),
                source="agent",
            ),
        ),
        approval=Approval(
            frame_id="frame-1",
            spec=dict(spec or PAGE_SPEC),
            rev=rev,
            by="human",
            at="2026-11-10T09:05:00Z",
        ),
    )
    return task


class ApprovalInvalidationTests(TaskApiCase):
    def seed(self) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=2,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1",),
                        source="human",
                    ),
                ),
                approval=_seed_approval(),
            )
        )

    def test_a_frame_change_invalidates_the_approval_and_the_agent_sees_it(self) -> None:
        self.seed()
        self.assertIsNotNone(TaskStore(self.layout).load().approval)
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1", "b2"]}',
            ).script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("框变了，批准失效。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        framed = observations[0]
        self.assertEqual(framed["tool"], "frame_blocks")
        self.assertEqual(framed["rev"], 3)
        self.assertEqual(framed["frames"][0]["source"], "agent")
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("approval", content.lower())
        self.assertIn("cleared", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertIsNone(task.approval)
        self.assertEqual(task.frames[0].blocks, ("b1", "b2"))
        self.assertEqual(task.frames[0].source, "agent")
        self.assertTrue(task.ops[-1].result["approvalCleared"])
        self.assertEqual(task.ops[-1].kind, "frame")

        # The next inspection reads the task as it stands: no approval left.
        summary = observations[1]
        self.assertEqual(summary["rev"], 3)
        viewed = client.get("/api/v1/task", params={"view": "summary"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertEqual(viewed.json()["rev"], 3)
        self.assertIsNone(viewed.json()["approval"])

    def test_the_summary_view_says_an_approval_exists(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("有批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        summary = next(data for kind, data in events if kind == "observation")
        self.assertEqual(summary["rev"], 2)
        viewed = client.get("/api/v1/task", params={"view": "summary"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        approval = viewed.json()["approval"]
        self.assertEqual(approval["frameId"], "frame-1")
        self.assertEqual(approval["by"], "human")
        self.assertEqual(approval["rev"], 2)
        # The writing spec itself is not part of a bounded summary.
        self.assertNotIn("spec", approval)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("approval", content.lower())
        self.assertIn("frame-1", content)

    def test_a_frame_that_clears_no_approval_says_nothing_about_one(self) -> None:
        self.seed()
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, approval=None))
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b2"]}',
            ).script,
            FauxModelClient.text("画好了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        framed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(framed["rev"], 3)
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertNotIn("approval", content.lower())
        self.assertFalse(self.task().ops[-1].result["approvalCleared"])


class FrameInspectTests(TaskApiCase):
    def test_inspect_frames_returns_the_frame_bounded_with_its_rev(self) -> None:
        blocks = tuple(
            MaterialBlock(
                id=f"b{index}", tag="p", preview=f"第{index}段的预览"
            )
            for index in range(1, 81)
        )
        stage = FakeStage(_staged(blocks=blocks, block_count=80))
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1", "b2"]}',
            ).script,
            FauxModelClient.tool_call(
                "t4", "inspect", '{"view": "frames"}'
            ).script,
            FauxModelClient.text("框在这里。").script,
            stage=stage,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        inspected = observations[3]
        self.assertEqual(inspected["tool"], "inspect")
        self.assertEqual(inspected["rev"], 3)
        self.assertEqual(inspected["hits"], 1)
        frame = inspected["frames"][0]
        self.assertEqual(frame["id"], "frame-1")
        self.assertEqual(frame["docId"], "raw/web/entropy-a1.html")
        self.assertEqual(frame["source"], "agent")
        self.assertEqual(frame["blockCount"], 2)
        self.assertEqual(
            [block["id"] for block in frame["blocks"]], ["b1", "b2"]
        )
        self.assertEqual(frame["blocks"][0]["preview"], "第1段的预览")

        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("rev 3", content)
        self.assertIn("raw/web/entropy-a1.html", content)
        self.assertIn("[b1]", content)
        self.assertIn("第1段的预览", content)
        self.assertIn("source=agent", content)
        # The summary counts frames too.
        summary = observations[3]
        self.assertEqual(summary["rev"], 3)

    def test_inspect_frames_with_no_frames_is_a_normal_empty_view(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "frames"}'
            ).script,
            FauxModelClient.text("还没有框。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        inspected = next(data for kind, data in events if kind == "observation")
        self.assertEqual(inspected["frames"], [])
        self.assertEqual(inspected["hits"], 0)
        self.assertNotIn("error", inspected)
        self.assertIn("No frames", self.last_tool_result_content(session_id, "inspect"))


PAGE_SPEC = {
    "slug": "entropy",
    "title": "Entropy",
    "action": "create",
    "sections": [
        {"title": "What it is", "blocks": ["b1"]},
        {"title": "Why it matters", "blocks": ["b2"]},
    ],
}


class ComposePageTests(TaskApiCase):
    def test_compose_without_approval_needs_approval_and_writes_nothing(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1", "b2"]}',
            ).script,
            FauxModelClient.tool_call(
                "t4",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observations = [data for kind, data in events if kind == "observation"]
        composed = observations[3]
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "needs_approval")
        self.assertEqual(composed["rev"], 4)
        self.assertNotIn("error", composed)
        request = composed["request"]
        self.assertEqual(request["frameId"], "frame-1")
        self.assertEqual(request["rev"], 4)
        self.assertEqual(request["spec"]["slug"], "entropy")
        self.assertEqual(
            [section["title"] for section in request["spec"]["sections"]],
            ["What it is", "Why it matters"],
        )

        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("needs_approval", content)
        self.assertIn("no wiki page", content.lower())
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertEqual(task.status, "needs_approval")
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertEqual(task.request.frame_id, "frame-1")
        self.assertEqual(task.request.rev, 4)
        self.assertEqual(
            [op.kind for op in task.ops], ["search", "pick", "frame"]
        )
        self.assertFalse((self.layout.wiki / "entropy.md").exists())


class ApprovalGateTests(TaskApiCase):
    def seed(self, *, rev: int = 1) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=rev,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
            )
        )

    def seed_approved(self, *, rev: int = 3) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=rev,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(PAGE_SPEC),
                    rev=rev,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                ),
            )
        )

    def test_a_person_approves_and_the_agent_sees_it_inspect_approval(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.text("已获批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id, "看看再提议")
        composed = self.task()
        self.assertEqual(composed.rev, 2)
        self.assertEqual(composed.request.frame_id, "frame-1")

        approved = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 2,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )

        self.assertEqual(approved.status_code, 200, approved.text)
        body = approved.json()
        self.assertEqual(body["decision"], "approve")
        self.assertEqual(body["rev"], 3)
        approval = body["approval"]
        self.assertEqual(approval["frameId"], "frame-1")
        self.assertEqual(approval["spec"]["slug"], "entropy")
        self.assertEqual(approval["rev"], 3)
        self.assertEqual(approval["by"], "human")
        self.assertTrue(approval["at"])

        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual(task.status, "idle")
        self.assertIsNone(task.request)
        self.assertIsNotNone(task.approval)
        self.assertEqual(task.approval.frame_id, "frame-1")
        self.assertEqual(task.approval.rev, 3)
        self.assertEqual(task.approval.by, "human")
        self.assertEqual(
            [op.kind for op in task.ops], ["approve"]
        )
        op = task.ops[0]
        self.assertEqual(op.actor, "human")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 2)
        self.assertEqual(op.input["decision"], "approve")
        self.assertFalse(any(op.kind == "write" for op in task.ops))
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

        # The agent reads the go-ahead through its bounded view; the spec is
        # what it proposed, the rev is the one the approval is bound to.
        events = self.ask(client, session_id, "批准了吗")
        observed = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["rev"], 3)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("approval", content.lower())
        self.assertIn("frame-1", content)
        self.assertIn("entropy", content)
        self.assertIn("rev 3", content)


    def test_compose_with_a_matching_approval_writes_the_page_once(self) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=3,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(PAGE_SPEC),
                    rev=3,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                ),
            )
        )
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("已获批准，写好了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "再提交一次")

        observations = [data for kind, data in events if kind == "observation"]
        composed = observations[1]
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "written")
        self.assertEqual(composed["rev"], 4)
        self.assertNotIn("request", composed)
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("written", content.lower())
        self.assertIn("wiki/entropy.md", content)

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertEqual([op.kind for op in task.ops], ["write"])
        self.assertEqual(task.ops[0].status, "done")
        self.assertIsNone(task.request)
        self.assertIsNone(task.approval)
        self.assertEqual(task.result["status"], "written")
        self.assertTrue((self.layout.wiki / "entropy.md").exists())


    def test_a_rejection_clears_the_request_and_the_agent_can_revise(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t4",
                "compose_page",
                json.dumps(
                    {
                        "frameId": "frame-1",
                        "spec": {
                            **PAGE_SPEC,
                            "sections": [
                                {"title": "What it is", "blocks": ["b1", "b2"]}
                            ],
                        },
                    }
                ),
            ).script,
            FauxModelClient.text("改一版再请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id, "提议写页")
        self.assertEqual(self.task().rev, 2)

        rejected = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 2,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "reject",
            },
        )

        self.assertEqual(rejected.status_code, 200, rejected.text)
        body = rejected.json()
        self.assertEqual(body["decision"], "reject")
        self.assertEqual(body["rev"], 3)
        self.assertNotIn("approval", body)
        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertIsNone(task.approval)
        self.assertIsNone(task.request)
        self.assertEqual(task.status, "idle")
        self.assertEqual([op.kind for op in task.ops], ["approve"])
        self.assertEqual(task.ops[0].result["decision"], "reject")
        self.assertEqual(task.ops[0].actor, "human")
        self.assertFalse(any(op.kind == "write" for op in task.ops))

        # The agent reads that nothing stands, then proposes a revised spec:
        # the gate is not a dead end, and the revision is recorded anew.
        events = self.ask(client, session_id, "再提议")
        observations = [data for kind, data in events if kind == "observation"]
        inspected = observations[0]
        self.assertEqual(inspected["tool"], "inspect")
        self.assertEqual(inspected["rev"], 3)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("approval", content.lower())
        self.assertIn("none", content.lower())

        revised = observations[1]
        self.assertEqual(revised["tool"], "compose_page")
        self.assertEqual(revised["status"], "needs_approval")
        self.assertEqual(revised["rev"], 4)
        self.assertEqual(
            revised["request"]["spec"]["sections"][0]["blocks"], ["b1", "b2"]
        )
        self.assertFalse((self.layout.wiki / "entropy.md").exists())


    def test_a_changed_rev_makes_the_approval_stale_and_compose_asks_again(
        self,
    ) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=3,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(PAGE_SPEC),
                    rev=3,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                ),
            )
        )
        # Another writer moved the task on after the person approved; the
        # approval now names a revision that no longer exists.
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, rev=4, intent="a later edit"))
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("批准过期了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        viewed = client.get("/api/v1/task", params={"view": "approval"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        self.assertEqual(viewed.json()["rev"], 4)
        self.assertIsNone(viewed.json()["approval"])
        self.assertTrue(viewed.json()["approvalStale"])
        self.assertIsNone(viewed.json()["request"])

        events = self.ask(client, session_id, "提交一次")
        observations = [data for kind, data in events if kind == "observation"]
        inspected = observations[0]
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("none", content.lower())
        self.assertIn("stale", content.lower())

        composed = observations[1]
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "needs_approval")
        self.assertEqual(composed["rev"], 5)
        self.assertEqual(composed["request"]["frameId"], "frame-1")

        task = self.task()
        self.assertEqual(task.rev, 5)
        self.assertIsNone(task.approval)
        self.assertEqual(task.request.rev, 5)
        self.assertEqual(task.status, "needs_approval")
        self.assertFalse(any(op.kind == "write" for op in task.ops))
        self.assertFalse((self.layout.wiki / "entropy.md").exists())


    def test_a_changed_spec_needs_approval_and_clears_the_approval(self) -> None:
        self.seed_approved()
        changed = {
            **PAGE_SPEC,
            "sections": [{"title": "What it is", "blocks": ["b2"]}],
        }
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": changed}),
            ).script,
            FauxModelClient.text("规格变了，请再批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "换个规格")

        observations = [data for kind, data in events if kind == "observation"]
        composed = observations[1]
        self.assertEqual(composed["status"], "needs_approval")
        self.assertEqual(composed["rev"], 4)
        self.assertEqual(
            composed["request"]["spec"]["sections"][0]["blocks"], ["b2"]
        )
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("cleared", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertIsNone(task.approval)
        self.assertEqual(task.request.spec["sections"][0]["blocks"], ["b2"])
        self.assertEqual(task.ops, ())
        self.assertEqual(task.status, "needs_approval")
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

    def test_a_spec_with_too_many_sections_is_bad_spec_and_writes_nothing(
        self,
    ) -> None:
        self.seed()
        sections = [
            {"title": f"Part {index}", "blocks": ["b1"]}
            for index in range(1, 14)
        ]
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps(
                    {
                        "frameId": "frame-1",
                        "spec": {**PAGE_SPEC, "sections": sections},
                    }
                ),
            ).script,
            FauxModelClient.text("章节太多了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "提交一个过大的规格")

        composed = [data for kind, data in events if kind == "observation"][1]
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "bad_spec")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("bad_spec", content)
        self.assertIn("12 sections", content)

        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertIsNone(task.request)
        self.assertIsNone(task.approval)
        self.assertEqual(task.ops, ())
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

    def test_a_block_outside_the_frame_is_bad_spec_and_writes_nothing(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps(
                    {
                        "frameId": "frame-1",
                        "spec": {
                            **PAGE_SPEC,
                            "sections": [
                                {"title": "What it is", "blocks": ["b9"]}
                            ],
                        },
                    }
                ),
            ).script,
            FauxModelClient.text("块不在画框里。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "用不在画框里的块")
        composed = [data for kind, data in events if kind == "observation"][1]
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "bad_spec")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("bad_spec", content)
        self.assertIn("b9", content)

        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertIsNone(task.request)
        self.assertEqual(task.ops, ())
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

    def test_a_spec_with_an_unknown_field_is_bad_spec(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps(
                    {
                        "frameId": "frame-1",
                        "spec": {**PAGE_SPEC, "coverage": "write"},
                    }
                ),
            ).script,
            FauxModelClient.text("多了一个字段。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "带上 coverage")
        composed = [data for kind, data in events if kind == "observation"][1]
        self.assertEqual(composed["diagnostics"][0]["code"], "bad_spec")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("coverage", content)
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertIsNone(task.request)
        self.assertEqual(task.ops, ())

    def test_a_stale_compose_observation_is_a_conflict_and_writes_nothing(
        self,
    ) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("看过了。").script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("提交。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id, "先看看")
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, rev=2, intent="a later edit"))

        events = self.ask(client, session_id, "提交")
        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("conflict", content)
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertIsNone(task.request)
        self.assertEqual(task.ops, ())

    def test_a_search_that_moves_the_rev_invalidates_the_approval(self) -> None:
        self.seed_approved()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("批准被新版本作废了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id, "搜一下再提交")

        observations = [data for kind, data in events if kind == "observation"]
        self.assertEqual(observations[0]["tool"], "web_search")
        self.assertEqual(observations[1]["tool"], "inspect")
        self.assertEqual(observations[1]["rev"], 4)
        # The search's completion moved the revision, so the approval no longer
        # stands. It is cleared rather than left to be mistaken for a go-ahead.
        inspected = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("none", inspected.lower())
        self.assertNotIn("stale", inspected.lower())
        self.assertIsNone(TaskStore(self.layout).load().approval)

        composed = observations[2]
        self.assertEqual(composed["status"], "needs_approval")
        self.assertEqual(composed["rev"], 5)

        viewed = client.get("/api/v1/task", params={"view": "approval"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        body = viewed.json()
        self.assertEqual(body["rev"], 5)
        self.assertIsNone(body["approval"])
        self.assertFalse(body["approvalStale"])
        self.assertEqual(body["request"]["rev"], 5)

        task = self.task()
        self.assertEqual(task.rev, 5)
        self.assertIsNone(task.approval)
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

    def test_approve_with_a_stale_rev_is_a_conflict_and_changes_nothing(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 1,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertEqual(task.ops, ())

    def test_approve_with_a_changed_spec_is_needs_approval_and_changes_nothing(
        self,
    ) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 2,
                "frameId": "frame-1",
                "spec": {
                    **PAGE_SPEC,
                    "sections": [{"title": "Other", "blocks": ["b2"]}],
                },
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "needs_approval")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertEqual(task.ops, ())

    def test_approve_without_a_pending_request_is_needs_approval(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 1,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "needs_approval")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertIsNone(task.approval)
        self.assertEqual(task.ops, ())

    def test_approve_for_another_task_is_a_conflict(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-9",
                "rev": 2,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertIsNotNone(task.request)
        self.assertIsNone(task.approval)

    def test_approve_with_an_invalid_spec_is_bad_spec_and_changes_nothing(
        self,
    ) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 2,
                "frameId": "frame-1",
                "spec": {**PAGE_SPEC, "sections": []},
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "bad_spec")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertEqual(task.ops, ())


    def test_a_frame_change_clears_the_pending_request(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.tool_call(
                "t4", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.text("框变了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id, "提议")
        self.assertIsNotNone(self.task().request)

        self.ask(client, session_id, "改框")
        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertIsNone(task.request)
        self.assertIsNone(task.approval)
        self.assertEqual(task.frames[0].blocks, ("b1",))
        self.assertEqual(task.ops[-1].result["requestCleared"], True)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("none", content.lower())
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

    def test_approve_for_a_request_whose_frame_is_gone_is_not_found(self) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=1,
                materials=(_stored_material(),),
                request=WriteRequest(
                    frame_id="frame-9",
                    spec=dict(PAGE_SPEC),
                    rev=1,
                    at="2026-11-10T09:05:00Z",
                ),
            )
        )
        client = self.client(spawn=lambda work: work())

        response = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": "task-7",
                "rev": 1,
                "frameId": "frame-9",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "not_found")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertIsNone(task.approval)
        self.assertEqual(task.ops, ())


class HumanFrameTests(TaskApiCase):
    def seed(self, *, approval: Approval | None = None) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=1,
                materials=(_stored_material(),),
                approval=approval,
            )
        )

    def test_a_person_frames_blocks_and_the_chat_inspect_sees_them(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "frames"}'
            ).script,
            FauxModelClient.text("看到框了。").script,
            spawn=lambda work: work(),
        )

        before = client.get("/api/v1/task", params={"view": "frames"})
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["rev"], 1)
        self.assertEqual(before.json()["frames"], [])

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b2", "b1"],
                "expectedRev": 1,
            },
        )
        self.assertEqual(framed.status_code, 200, framed.text)
        body = framed.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["rev"], 2)
        self.assertFalse(body["approvalCleared"])
        self.assertEqual(body["frame"]["id"], "frame-1")
        self.assertEqual(
            body["frame"]["docId"], "raw/web/entropy-a1.html"
        )
        self.assertEqual(body["frame"]["source"], "human")
        self.assertEqual(body["frame"]["blocks"], ["b1", "b2"])

        # The same one task: the chat reads the frame the person drew.
        session_id = self.new_session(client)
        events = self.ask(client, session_id)
        observation = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(observation["tool"], "inspect")
        self.assertEqual(observation["rev"], 2)
        self.assertEqual(observation["hits"], 1)
        self.assertEqual(observation["frames"][0]["source"], "human")
        self.assertEqual(
            [block["id"] for block in observation["frames"][0]["blocks"]],
            ["b1", "b2"],
        )

        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual([op.kind for op in task.ops], ["frame"])
        op = task.ops[0]
        self.assertEqual(op.actor, "human")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 1)
        self.assertEqual(op.input["blocks"], ["b2", "b1"])
        self.assertEqual(task.frames[0].source, "human")

    def test_a_person_drawing_over_a_frame_replaces_its_blocks(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        first = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": 1,
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b2"],
                "expectedRev": 2,
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["frame"]["id"], "frame-1")

        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual(len(task.frames), 1)
        self.assertEqual(task.frames[0].blocks, ("b2",))

    def test_a_person_framing_invalidates_an_existing_approval(self) -> None:
        self.seed(approval=_seed_approval())
        client = self.client(spawn=lambda work: work())

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1", "b2"],
                "expectedRev": 1,
            },
        )

        self.assertEqual(framed.status_code, 200, framed.text)
        self.assertTrue(framed.json()["approvalCleared"])
        summary = client.get("/api/v1/task", params={"view": "summary"})
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertIsNone(summary.json()["approval"])
        self.assertIsNone(self.task().approval)

    def test_a_stale_frame_is_a_conflict_and_writes_no_op(self) -> None:
        self.seed()
        store = TaskStore(self.layout)
        stored = store.load()
        self.assertIsNotNone(stored)
        store.save(replace(stored, rev=2, intent="a person edited it"))
        client = self.client(spawn=lambda work: work())

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": 1,
            },
        )

        self.assertEqual(framed.status_code, 409, framed.text)
        self.assertEqual(framed.json()["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual(task.ops, ())
        self.assertEqual(task.frames, ())

    def test_a_person_framing_an_unknown_document_is_not_found(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/missing.html",
                "blocks": ["b1"],
                "expectedRev": 1,
            },
        )

        self.assertEqual(framed.status_code, 404, framed.text)
        self.assertEqual(framed.json()["code"], "not_found")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops, ())
        self.assertEqual(task.frames, ())

    def test_a_person_framing_an_unknown_block_is_bad_spec(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1", "b999"],
                "expectedRev": 1,
            },
        )

        self.assertEqual(framed.status_code, 422, framed.text)
        self.assertEqual(framed.json()["code"], "bad_spec")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(task.ops, ())
        self.assertEqual(task.frames, ())

    def test_a_person_drawing_an_empty_frame_is_bad_spec(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": [],
                "expectedRev": 1,
            },
        )

        self.assertEqual(framed.status_code, 422, framed.text)
        self.assertEqual(framed.json()["code"], "bad_spec")
        self.assertEqual(self.task().frames, ())


class TaskOverviewTests(TaskApiCase):
    """The interface's one snapshot: all bounded views from one task load."""

    VIEWS = ("summary", "cards", "materials", "frames", "approval", "result")

    def test_the_overview_reads_one_task_and_reports_one_revision(self) -> None:
        store = _CountingStore(self.layout)
        store.save(_seed_approved_task(rev=3))
        service = TaskService(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
            search=FakeSearch(),
            fetch=FakeFetch(),
            stage=FakeStage(),
            spawn=lambda work: work(),
            compose=FakeCompose(),
            store=store,
        )
        client = self.client(tasks=service)

        body = client.get(
            "/api/v1/task", params={"view": "overview"}
        ).json()

        self.assertEqual(body["view"], "overview")
        self.assertEqual(body["rev"], 3)
        # One snapshot, not six: every view is built from the same task read.
        self.assertEqual(store.loads, 1)
        for view in self.VIEWS:
            self.assertEqual(body[view]["view"], view)
            self.assertEqual(body[view]["rev"], 3)

        # A write moves the task; the next snapshot carries the one new
        # revision in every view rather than a mix of the old and the new.
        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": 3,
            },
        )
        self.assertEqual(framed.status_code, 200, framed.text)
        self.assertEqual(framed.json()["rev"], 4)
        store.loads = 0

        body = client.get(
            "/api/v1/task", params={"view": "overview"}
        ).json()

        self.assertEqual(store.loads, 1)
        self.assertEqual(body["rev"], 4)
        for view in self.VIEWS:
            self.assertEqual(body[view]["rev"], 4)
        # The person's frame is the writing basis the panel must show.
        self.assertEqual(body["frames"]["frames"][0]["docId"], "raw/web/entropy-a1.html")
        self.assertIsNone(body["approval"]["approval"])

    def test_the_individual_views_remain_for_other_clients(self) -> None:
        TaskStore(self.layout).save(_seed_approved_task(rev=3))
        client = self.client(spawn=lambda work: work())

        body = client.get(
            "/api/v1/task", params={"view": "summary"}
        ).json()

        self.assertEqual(body["view"], "summary")
        self.assertEqual(body["rev"], 3)


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

    def test_a_restarted_session_still_knows_the_rev_it_last_saw(self) -> None:
        """The session's observed revision survives the in-memory registry.

        A person's write made while the core was down must still be a conflict
        for the agent: the tool result the conversation saw is persisted with
        its revision, and the restarted wrapper names that revision, not the
        task's current one.
        """
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=0,
                materials=(_stored_material(),),
            )
        )
        ids = SequentialIds()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("找到了。").script,
            spawn=lambda work: work(),
            ids=ids,
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        self.assertEqual(self.task().rev, 1)

        # A new core: the in-memory observation is gone, the session file is
        # not. The person writes before the agent comes back.
        restarted = self.client(
            FauxModelClient.tool_call(
                "t2",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.text("框不了，先看看任务。").script,
            spawn=lambda work: work(),
            ids=ids,
        )
        human = restarted.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": 1,
            },
        )
        self.assertEqual(human.status_code, 200, human.text)
        self.assertEqual(human.json()["rev"], 2)

        events = self.ask(restarted, session_id, "画个框")

        observed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(observed["error"])
        self.assertEqual(observed["diagnostics"][0]["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 2)
        self.assertEqual([op.kind for op in task.ops], ["search", "frame"])


class CorruptTaskTests(TaskApiCase):
    """A task file that exists but cannot be read is not a missing task."""

    def corrupt(self, text: str = "{not json") -> Path:
        path = self.layout.task_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_corrupt_task_file_is_an_error_and_is_left_alone(self) -> None:
        path = self.corrupt()
        client = self.client(spawn=lambda work: work())

        read = client.get("/api/v1/task", params={"view": "overview"})

        self.assertEqual(read.status_code, 500, read.text)
        self.assertEqual(read.json()["code"], "task.unreadable")
        self.assertIn("task file", read.json()["detail"])
        # The file is not replaced by a fresh task: a read never overwrites.
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

        # A mutation is refused the same way, and still writes nothing.
        framed = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": 0,
            },
        )
        self.assertGreaterEqual(framed.status_code, 500, framed.text)
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_json_that_is_not_a_task_is_unreadable_too(self) -> None:
        path = self.corrupt("[]")
        client = self.client(spawn=lambda work: work())

        read = client.get("/api/v1/task", params={"view": "summary"})

        self.assertEqual(read.status_code, 500, read.text)
        self.assertEqual(read.json()["code"], "task.unreadable")
        self.assertEqual(path.read_text(encoding="utf-8"), "[]")


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


class ComposeWriteTests(TaskApiCase):
    def seed(self, spec: dict | None = None, *, rev: int = 3):
        TaskStore(self.layout).save(_seed_approved_task(spec, rev=rev))

    def test_an_approved_compose_writes_a_page_through_the_executor(self) -> None:
        self.seed()
        compose = FakeCompose()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("写好了。").script,
            compose=compose,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "written")
        self.assertEqual(composed["rev"], 4)
        self.assertEqual(composed["path"], "wiki/entropy.md")
        self.assertEqual(composed["slug"], "entropy")
        self.assertEqual(composed["action"], "create")
        self.assertTrue(composed["wrote_wiki"])
        self.assertNotIn("effectIds", composed)
        self.assertNotIn("diff", composed)

        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("written", content.lower())
        self.assertIn("wiki/entropy.md", content)

        # The real writer created the page from the composed, source-marked
        # text, indexed it, and journaled the write as this op's effect.
        page = self.layout.wiki / "entropy.md"
        self.assertTrue(page.is_file())
        text = page.read_text(encoding="utf-8")
        self.assertIn("<!-- na-clip:raw/web/entropy-a1.html -->", text)
        self.assertIn("composed from b1", text)
        index = (self.layout.wiki / "index.md").read_text(encoding="utf-8")
        self.assertIn("- [[entropy]]", index)

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertIsNone(task.approval)
        self.assertIsNone(task.request)
        self.assertEqual(task.status, "done")
        op = task.ops[-1]
        self.assertEqual(op.kind, "write")
        self.assertEqual(op.actor, "agent")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.input["frameId"], "frame-1")
        self.assertEqual(op.input["spec"]["slug"], "entropy")
        result = task.result
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "written")
        self.assertEqual(result["page"], "wiki/entropy.md")
        self.assertEqual(result["slug"], "entropy")
        self.assertEqual(result["title"], "Entropy")
        self.assertIn("written", result["summary"])
        self.assertIn("diff", result)
        self.assertNotIn("effectIds", result)
        self.assertNotIn("effectIds", json.dumps(composed))

        effects = EffectJournal(self.layout).query(
            run_id=op.id, kind=WIKI_PAGE_WRITE, status=COMMITTED
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].target, "wiki/entropy.md")
        self.assertEqual(effects[0].actor, "agent")
        self.assertTrue(op.result.get("effectIds"))
        self.assertEqual(compose.requests[0].wiki_path, "wiki/entropy.md")
        self.assertEqual(compose.requests[0].material, "raw/web/entropy-a1.html")

        # The interface reads the result and its diff from the task view. The
        # effect ids are in the op log only: neither this view nor the agent's
        # observations carry them.
        viewed = client.get("/api/v1/task", params={"view": "result"})
        self.assertEqual(viewed.status_code, 200, viewed.text)
        body = viewed.json()
        self.assertEqual(body["rev"], 4)
        self.assertEqual(body["result"]["status"], "written")
        self.assertIn("+composed from b1", body["result"]["diff"])
        self.assertNotIn("effectIds", body["result"])

    def test_an_update_replaces_a_page_the_same_material_owns(self) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\n"
            "title: Entropy\n"
            "created: 2026-11-01\n"
            'sources: ["raw/web/entropy-a1.html#b1", '
            '"raw/web/entropy-a1.html#b2"]\n'
            "---\n\n"
            "# Entropy\n\n"
            "<!-- na-clip:raw/web/entropy-a1.html -->\n"
            "## What it is\n\nold body\n",
            encoding="utf-8",
        )
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("更新了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["status"], "written")
        self.assertEqual(composed["action"], "update")
        text = page.read_text(encoding="utf-8")
        self.assertIn("composed from b1", text)
        self.assertNotIn("old body", text)
        task = self.task()
        self.assertEqual(task.result["status"], "written")
        self.assertTrue(task.result["diff"].startswith("@@"))
        self.assertIn("-old body", task.result["diff"])
        self.assertIn("+composed from b1", task.result["diff"])
        op = task.ops[-1]
        effects = EffectJournal(self.layout).query(
            run_id=op.id, kind=WIKI_PAGE_WRITE, status=COMMITTED
        )
        self.assertEqual(len(effects), 1)

    def test_an_update_does_not_clobber_a_page_the_material_does_not_own(
        self,
    ) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\n"
            "a person's own prose with no sources\n",
            encoding="utf-8",
        )
        before = page.read_text(encoding="utf-8")
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("没有覆盖。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["status"], "skipped")
        self.assertNotIn("error", composed)
        self.assertEqual(page.read_text(encoding="utf-8"), before)
        self.assertNotIn("composed from", page.read_text(encoding="utf-8"))
        task = self.task()
        self.assertEqual(task.result["status"], "skipped")
        self.assertIn("no merger", task.result["reason"])

    def test_a_compose_error_is_op_failed_and_records_a_failed_result(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("模型失败了。").script,
            compose=FakeCompose(error=RuntimeError("the model refused")),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "op_failed")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("could not be composed", content)
        self.assertIn("the model refused", content)
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

        task = self.task()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result["status"], "failed")
        self.assertEqual(task.result["page"], "wiki/entropy.md")
        self.assertIn("the model refused", task.result["reason"])
        self.assertEqual(task.ops[-1].status, "failed")
        self.assertEqual(task.ops[-1].error_code, "op_failed")

    def test_a_failed_write_moves_the_revision_and_needs_a_new_approval(
        self,
    ) -> None:
        """A failure that rewrites the task is a state write, so rev moves.

        The failed attempt is the task's new result; the person's earlier
        go-ahead was bound to the revision before it. It must not survive as
        usable: the agent's next compose has to ask for approval again.
        """
        self.seed()
        compose = FakeCompose(error=RuntimeError("the model refused"))
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("失败了。").script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("需要再批准。").script,
            compose=compose,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id)

        failed = self.task()
        self.assertEqual(failed.rev, 4)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.result["status"], "failed")
        self.assertIsNone(failed.approval)
        self.assertIsNone(failed.request)
        self.assertEqual(failed.ops[-1].kind, "write")
        self.assertEqual(failed.ops[-1].status, "failed")

        events = self.ask(client, session_id, "再试试")

        composed = [
            data for kind, data in events if kind == "observation"
        ][-1]
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "needs_approval")
        self.assertNotIn("error", composed)
        self.assertEqual(len(compose.requests), 1)
        task = self.task()
        self.assertEqual(task.rev, 5)
        self.assertEqual(task.status, "needs_approval")
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertEqual(task.request.frame_id, "frame-1")
        self.assertEqual(
            [op.kind for op in task.ops], ["write"]
        )

    def test_inspect_result_reads_the_outcome_without_the_diff(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("写好了。").script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "result"}'
            ).script,
            FauxModelClient.text("结果在这里。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        events = self.ask(client, session_id, "结果呢")

        observed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["status"], "written")
        self.assertEqual(observed["rev"], 4)
        self.assertEqual(observed["path"], "wiki/entropy.md")
        self.assertEqual(observed["slug"], "entropy")
        self.assertEqual(observed["action"], "create")
        self.assertTrue(observed["wrote_wiki"])
        # The agent re-reads the outcome, never the page prose or the op log.
        self.assertNotIn("diff", observed)
        self.assertNotIn("effectIds", json.dumps(observed))
        self.assertNotIn("+composed from", json.dumps(observed))

        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("wiki/entropy.md", content)
        self.assertIn("written", content.lower())
        self.assertIn("written wiki/entropy.md", content)
        self.assertIn("rev 4", content)
        self.assertNotIn("composed from b1", content)
        self.assertNotIn("effectIds", content)

        # The interface's route keeps the diff; only the agent's view drops it.
        body = client.get("/api/v1/task", params={"view": "result"}).json()
        self.assertIn("diff", body["result"])
        self.assertEqual(body["result"]["status"], "written")

    def test_inspect_result_before_any_write_says_there_is_none(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "result"}'
            ).script,
            FauxModelClient.text("还没有结果。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        observed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["rev"], 3)
        self.assertNotIn("path", observed)
        self.assertNotIn("status", observed)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("no page write", content.lower())

    def test_a_failed_save_is_recorded_as_failed_and_writes_no_page(self) -> None:
        self.seed()
        failure = SaveOutcome(
            status="failed",
            failed=(("wiki/entropy.md", "the disk is full"),),
            summary="could not write wiki/entropy.md: the disk is full",
            reason="the disk is full",
        )
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("写失败了。").script,
            save=FakeSave(failure),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["status"], "failed")
        self.assertNotIn("error", composed)
        self.assertEqual(composed["path"], "wiki/entropy.md")
        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("failed", content.lower())
        self.assertIn("disk is full", content)

        task = self.task()
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result["status"], "failed")
        self.assertEqual(task.result["failed"][0]["path"], "wiki/entropy.md")
        self.assertIn("disk is full", task.result["failed"][0]["reason"])
        self.assertEqual(task.ops[-1].status, "done")
        self.assertEqual(task.rev, 4)

    def test_a_create_does_not_overwrite_an_existing_page(self) -> None:
        self.seed()
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\nthe person's page\n",
            encoding="utf-8",
        )
        before = page.read_text(encoding="utf-8")
        compose = FakeCompose()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("跳过了。").script,
            compose=compose,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "skipped")
        self.assertEqual(composed["rev"], 4)
        self.assertNotIn("error", composed)
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("skipped", content.lower())
        self.assertIn("already exists", content.lower())

        # The page is exactly as the person left it; the compose node was never
        # asked to build a replacement.
        self.assertEqual(page.read_text(encoding="utf-8"), before)
        self.assertEqual(compose.requests, [])
        task = self.task()
        self.assertEqual(task.result["status"], "skipped")
        self.assertEqual(task.result["page"], "wiki/entropy.md")
        self.assertEqual(task.ops[-1].status, "done")
        self.assertIsNone(task.approval)
        self.assertEqual(task.rev, 4)
        self.assertFalse(
            EffectJournal(self.layout).query(run_id=task.ops[-1].id)
        )

    def test_an_update_of_a_missing_page_changes_nothing(self) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        compose = FakeCompose()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("没有这页。").script,
            compose=compose,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "skipped")
        self.assertNotIn("error", composed)
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("does not exist", content.lower())

        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        self.assertEqual(compose.requests, [])
        task = self.task()
        self.assertEqual(task.result["status"], "skipped")
        self.assertEqual(task.ops[-1].status, "done")

    def test_a_program_owned_page_is_bad_spec_and_writes_no_op(self) -> None:
        spec = {**PAGE_SPEC, "slug": "index", "title": "Index"}
        self.seed(spec)
        index = self.layout.wiki / "index.md"
        before = index.read_text(encoding="utf-8") if index.is_file() else ""
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("这页不能写。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "bad_spec")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("maintains", content.lower())

        task = self.task()
        self.assertEqual(task.ops, ())
        self.assertEqual(task.rev, 3)
        self.assertIsNotNone(task.approval)
        self.assertEqual(index.read_text(encoding="utf-8"), before)

    def test_a_task_change_during_the_compose_is_a_conflict_and_writes_nothing(
        self,
    ) -> None:
        self.seed()

        def moved(request) -> None:
            store = TaskStore(self.layout)
            current = store.load()
            self.assertIsNotNone(current)
            store.save(
                replace(
                    current,
                    rev=current.rev + 1,
                    intent="moved while the page was being composed",
                )
            )

        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("批准过期了。").script,
            compose=FakeCompose(on_call=moved),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("conflict", content.lower())
        self.assertIn("do not retry blindly", content.lower())
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

        task = self.task()
        # The failure rewrites the task's result, so it is a state write and
        # moves the revision once more.
        self.assertEqual(task.rev, 5)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result["status"], "failed")
        self.assertEqual(task.result["page"], "wiki/entropy.md")
        self.assertIn("changed while", task.result["reason"])
        self.assertEqual(task.result["diff"], "")
        op = task.ops[-1]
        self.assertEqual(op.kind, "write")
        self.assertEqual(op.status, "failed")
        self.assertEqual(op.error_code, "conflict")
        self.assertFalse(EffectJournal(self.layout).query(run_id=op.id))
        # The revision move makes the go-ahead stale by construction; the
        # failure clears it rather than leaving it to look usable.
        self.assertIsNone(task.approval)
        approval_view = client.get(
            "/api/v1/task", params={"view": "approval"}
        ).json()
        self.assertIsNone(approval_view["approval"])
        self.assertFalse(approval_view["approvalStale"])

    def test_a_page_change_during_the_compose_keeps_the_other_write(self) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\noriginal text\n",
            encoding="utf-8",
        )

        def edited(request) -> None:
            page.write_text(
                "---\ntitle: Entropy\n---\n\n# Entropy\n\n"
                "the person's newer text\n",
                encoding="utf-8",
            )

        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("页面被改过。").script,
            compose=FakeCompose(on_call=edited),
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        self.assertIn("person's newer text", page.read_text(encoding="utf-8"))
        self.assertNotIn("composed from", page.read_text(encoding="utf-8"))
        task = self.task()
        self.assertEqual(task.result["status"], "failed")
        self.assertIn("changed while", task.result["reason"])
        op = task.ops[-1]
        self.assertEqual(op.status, "failed")
        self.assertEqual(op.error_code, "conflict")
        self.assertFalse(EffectJournal(self.layout).query(run_id=op.id))

    def test_a_compose_that_outlives_the_wait_returns_requested_or_running(
        self,
    ) -> None:
        self.seed()
        gate = threading.Event()
        self.addCleanup(gate.set)
        compose = FakeCompose(gate=gate)
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("还在写。").script,
            compose=compose,
            compose_wait_s=0.05,
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertNotIn("error", composed)
        self.assertIn(composed["status"], ("requested", "running"))
        self.assertEqual(composed["rev"], 3)
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("still", content.lower())
        self.assertIn("do not compose it again", content.lower())
        # The tool returned; the executor is still the writer and has not
        # written anything yet because the compose node is held open.
        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        self.assertIsNone(self.task().result)

        gate.set()
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].status == "done"
        )
        self.assertEqual(task.rev, 4)
        self.assertEqual(task.result["status"], "written")
        self.assertTrue((self.layout.wiki / "entropy.md").is_file())
        self.assertEqual(task.ops[-1].kind, "write")
        self.assertEqual(task.ops[-1].status, "done")

        # After a timeout the agent re-reads the outcome; the result view is
        # bounded and carries no page text or effect ids.
        later = self.client(
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "result"}'
            ).script,
            FauxModelClient.text("完成了。").script,
            spawn=lambda work: work(),
        )
        events = self.ask(later, session_id, "写完了吗")
        observed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["status"], "written")
        self.assertEqual(observed["rev"], 4)
        self.assertEqual(observed["path"], "wiki/entropy.md")
        self.assertNotIn("diff", observed)
        self.assertNotIn(
            "composed from b1",
            self.last_tool_result_content(session_id, "inspect"),
        )

    def test_a_human_save_in_the_lock_gap_wins_and_the_task_conflicts(
        self,
    ) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\noriginal text\n",
            encoding="utf-8",
        )
        locks = GapPageLocks()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("页面被改过。").script,
            page_locks=locks,
        )
        session_id = self.new_session(client)
        outcome: dict[str, object] = {}

        def ask() -> None:
            try:
                outcome["events"] = self.ask(client, session_id)
            except Exception as err:  # noqa: BLE001 - re-raised in the test
                outcome["error"] = err

        worker = threading.Thread(target=ask)
        worker.start()
        try:
            # The executor has composed and is about to take the page lock.
            self.assertTrue(locks.arrived.wait(5))
            # The person saves in that gap. The check must happen under the
            # same lock as the write, so the task sees this edit and refuses
            # instead of overwriting it with content composed before it.
            opened = client.get(
                "/api/projects/demo/page",
                params={"path": "wiki/entropy.md"},
            )
            self.assertEqual(opened.status_code, 200, opened.text)
            saved = client.post(
                "/api/projects/demo/page/save",
                json={
                    "path": "wiki/entropy.md",
                    "body": "the person's edit in the lock gap",
                    "baseRevision": opened.json()["revision"],
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        finally:
            locks.release.set()
            worker.join(10)

        self.assertNotIn("error", outcome)
        events = outcome["events"]
        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        text = page.read_text(encoding="utf-8")
        self.assertIn("the person's edit in the lock gap", text)
        self.assertNotIn("composed from", text)

        task = self.task()
        # The person's save is also one more human op on the shared task; the
        # failed write then records its failure as a state write of its own.
        self.assertEqual(task.rev, 5)
        self.assertEqual(task.ops[-1].kind, "edit")
        self.assertEqual(task.ops[-1].actor, "human")
        write = next(op for op in task.ops if op.kind == "write")
        self.assertEqual(task.result["status"], "failed")
        self.assertIn("changed while", task.result["reason"])
        self.assertEqual(write.status, "failed")
        self.assertEqual(write.error_code, "conflict")
        self.assertFalse(EffectJournal(self.layout).query(run_id=write.id))

    def test_a_human_save_during_the_compose_wins_and_the_task_conflicts(
        self,
    ) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\noriginal text\n",
            encoding="utf-8",
        )
        gate = threading.Event()
        self.addCleanup(gate.set)
        compose = FakeCompose(gate=gate)
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("页面被改过。").script,
            compose=compose,
        )
        session_id = self.new_session(client)
        outcome: dict[str, object] = {}

        def ask() -> None:
            try:
                outcome["events"] = self.ask(client, session_id)
            except Exception as err:  # noqa: BLE001 - re-raised in the test
                outcome["error"] = err

        worker = threading.Thread(target=ask)
        worker.start()
        try:
            self.assertTrue(compose.entered.wait(5))

            # The person edits the same page while the agent's compose node is
            # still working. No page lock is held during composition, so the
            # editor's save goes through and is the one that must survive.
            opened = client.get(
                "/api/projects/demo/page",
                params={"path": "wiki/entropy.md"},
            )
            self.assertEqual(opened.status_code, 200, opened.text)
            saved = client.post(
                "/api/projects/demo/page/save",
                json={
                    "path": "wiki/entropy.md",
                    "body": "the person's edit during the compose",
                    "baseRevision": opened.json()["revision"],
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        finally:
            gate.set()
            worker.join(10)

        self.assertNotIn("error", outcome)
        events = outcome["events"]
        composed = next(data for kind, data in events if kind == "observation")
        self.assertEqual(composed["tool"], "compose_page")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        self.assertIn(
            "the person's edit during the compose",
            page.read_text(encoding="utf-8"),
        )
        self.assertNotIn("composed from", page.read_text(encoding="utf-8"))

        task = self.task()
        # The person's save moved the task to 4; the failed write records its
        # failure as a state write, which moves it once more.
        self.assertEqual(task.rev, 5)
        self.assertEqual(task.ops[-1].kind, "edit")
        self.assertEqual(task.ops[-1].actor, "human")
        write = next(op for op in task.ops if op.kind == "write")
        self.assertEqual(task.result["status"], "failed")
        self.assertIn("changed while", task.result["reason"])
        self.assertEqual(write.status, "failed")
        self.assertEqual(write.error_code, "conflict")
        self.assertFalse(EffectJournal(self.layout).query(run_id=write.id))

    def test_a_human_save_is_busy_while_the_task_is_saving(self) -> None:
        spec = {**PAGE_SPEC, "action": "update"}
        self.seed(spec)
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\n---\n\n# Entropy\n\noriginal text\n",
            encoding="utf-8",
        )
        gate = threading.Event()
        self.addCleanup(gate.set)
        save = FakeSave(gate=gate)
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("写好了。").script,
            save=save,
        )
        session_id = self.new_session(client)
        outcome: dict[str, object] = {}

        def ask() -> None:
            try:
                outcome["events"] = self.ask(client, session_id)
            except Exception as err:  # noqa: BLE001 - re-raised in the test
                outcome["error"] = err

        worker = threading.Thread(target=ask)
        worker.start()
        try:
            # The save node runs inside the page lock, so reaching it means the
            # task writer holds the page the person is about to open.
            self.assertTrue(save.entered.wait(5))
            opened = client.get(
                "/api/projects/demo/page",
                params={"path": "wiki/entropy.md"},
            )
            self.assertEqual(opened.status_code, 200, opened.text)
            busy = client.post(
                "/api/projects/demo/page/save",
                json={
                    "path": "wiki/entropy.md",
                    "body": "a person's edit racing the task",
                    "baseRevision": opened.json()["revision"],
                },
            )
            self.assertEqual(busy.status_code, 409, busy.text)
            self.assertEqual(busy.json()["code"], "page.busy")
        finally:
            gate.set()
            worker.join(10)

        self.assertNotIn("error", outcome)
        events = outcome["events"]
        composed = next(data for kind, data in events if kind == "observation")
        task = self.task()
        self.assertEqual(
            composed["status"],
            "written",
            json.dumps(
                {
                    "observation": composed,
                    "rev": task.rev,
                    "ops": [
                        (op.kind, op.status, op.error_code, op.error)
                        for op in task.ops
                    ],
                }
            ),
        )
        self.assertEqual(self.task().result["status"], "written")
        # The lock is released with the save: the person can save afterwards.
        opened = client.get(
            "/api/projects/demo/page", params={"path": "wiki/entropy.md"}
        )
        later = client.post(
            "/api/projects/demo/page/save",
            json={
                "path": "wiki/entropy.md",
                "body": "the person's edit afterwards",
                "baseRevision": opened.json()["revision"],
            },
        )
        self.assertEqual(later.status_code, 200, later.text)

    def test_a_task_save_is_a_conflict_while_a_human_save_holds_the_page(
        self,
    ) -> None:
        self.seed()
        locks = PageLocks()
        held = locks.lock(str(self.layout.root.resolve()), "wiki/entropy.md")
        self.assertTrue(held.acquire(blocking=False))
        self.addCleanup(held.release)
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("页面正被编辑。").script,
            page_locks=locks,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        task = self.task()
        self.assertEqual(task.result["status"], "failed")
        self.assertIn("being written", task.result["reason"])
        self.assertEqual(task.ops[-1].status, "failed")
        self.assertEqual(task.ops[-1].error_code, "conflict")


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


class OpResumeNoticeTests(TaskApiCase):
    """The executor boundary asks for exactly one continuation, with a delta."""

    def test_a_search_that_outlives_its_wait_asks_for_one_resume(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        resume = RecordingResume()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
            search=search,
            wait_s=0.05,
            resume=resume,
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertIn(card["status"], ("requested", "running"))
        # The wait ended before the node did: nothing to resume yet.
        self.assertEqual(resume.notices, [])

        gate.set()
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].status == "done"
        )

        self.assertEqual(len(resume.notices), 1)
        notice = resume.notices[0]
        self.assertEqual(notice.session_id, session_id)
        self.assertEqual(notice.op_id, task.ops[0].id)
        self.assertEqual(notice.kind, "search")
        self.assertEqual(notice.status, "done")
        self.assertEqual(notice.rev, task.rev)
        self.assertEqual(notice.result["hits"], 2)
        self.assertEqual(
            [item["title"] for item in notice.result["cards"]],
            ["Entropy", "Information"],
        )
        # The op carries the session and the once-only resume request; the
        # completion did not start a resume behind the test's recorder.
        op = task.ops[0]
        self.assertEqual(op.input["sessionId"], session_id)
        self.assertTrue(op.resume_requested)
        self.assertFalse(op.resume_started)

    def test_a_fast_search_never_asks_for_a_resume(self) -> None:
        resume = RecordingResume()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("找到了。").script,
            resume=resume,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        self.ask(client, session_id)

        self.assertEqual(resume.notices, [])
        op = self.task().ops[0]
        self.assertFalse(op.resume_requested)
        self.assertFalse(op.resume_started)

    def test_a_pick_that_outlives_its_wait_asks_for_one_resume(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        fetch = FakeFetch(gate=gate)
        resume = RecordingResume()
        # One id source across both app instances: op ids name ops in one task,
        # so a test that restarts the app must not reuse them.
        ids = SequentialIds()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("搜到了。").script,
            spawn=lambda work: work(),
            ids=ids,
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        card_id = self.task().cards[0].id

        pick_client = self.client(
            FauxModelClient.tool_call(
                "t2", "pick_card", json.dumps({"cardId": card_id})
            ).script,
            FauxModelClient.text("正在取。").script,
            fetch=fetch,
            pick_wait_s=0.05,
            resume=resume,
            ids=ids,
        )
        events = self.ask(pick_client, session_id, "取这张卡")

        card = next(data for kind, data in events if kind == "observation")
        self.assertIn(card["status"], ("requested", "running"))
        self.assertEqual(resume.notices, [])

        gate.set()
        task = self.wait_for_task(
            lambda item: len(item.ops) == 2 and item.ops[-1].status == "done"
        )
        self.assertEqual(len(resume.notices), 1)
        notice = resume.notices[0]
        self.assertEqual(notice.kind, "pick")
        self.assertEqual(notice.status, "done")
        self.assertEqual(
            notice.result["material"]["id"], task.materials[0].id
        )
        self.assertEqual(task.ops[-1].input["sessionId"], session_id)


class OpCancelTests(TaskApiCase):
    """A person stops an unfinished op; it never saves, resumes or retries."""

    def seed(self, spec: dict | None = None, *, rev: int = 3) -> None:
        TaskStore(self.layout).save(_seed_approved_task(spec, rev=rev))

    def test_a_person_cancels_a_slow_write_before_it_saves_or_resumes(self) -> None:
        self.seed()
        gate = threading.Event()
        self.addCleanup(gate.set)
        compose = FakeCompose(gate=gate)
        save = FakeSave()
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("还在写。").script,
            FauxModelClient.text("这段只在续跑时才会被读到。").script,
        )
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=FakeSearch(),
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=compose,
                    task_save=save,
                    task_compose_wait_s=0.05,
                )
            )
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertIn(card["status"], ("requested", "running"))
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].status == "running"
        )
        op_id = task.ops[-1].id
        # The executor received the op's stop signal and is composing.
        self.assertTrue(compose.cancels)
        self.assertIsNotNone(compose.cancels[-1])
        self.assertEqual(len(model.requests), 2)

        response = client.post(f"/api/v1/task/ops/{op_id}/cancel")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["cancelled"])
        self.assertEqual(body["op"]["status"], "cancelled")

        # Let the compose finish. The executor must stop before the save, and
        # the completion must not resume the session.
        gate.set()
        self.assertTrue(compose.exited.wait(5))
        threading.Event().wait(0.1)

        self.assertEqual(save.requests, [])
        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        self.assertIsNone(self.task().result)
        self.assertEqual(self.task().ops[-1].status, "cancelled")
        self.assertEqual(
            [
                entry
                for entry in self.session_entries(session_id)
                if entry.get("type") == "task_op_result"
            ],
            [],
        )
        self.assertEqual(len(model.requests), 2)

        # A second cancel is a no-op: the op is terminal.
        again = client.post(f"/api/v1/task/ops/{op_id}/cancel")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertFalse(again.json()["cancelled"])
        self.assertEqual(again.json()["op"]["status"], "cancelled")

        summary = client.get("/api/v1/task", params={"view": "summary"}).json()
        shown = next(item for item in summary["ops"] if item["id"] == op_id)
        self.assertEqual(shown["status"], "cancelled")

    def test_a_cancel_written_by_another_process_stops_the_page_save(self) -> None:
        """A cancel from another core is seen when the executor re-reads the op.

        The in-process stop signal cannot reach that writer, so the persisted
        op status is the guarantee: the final save re-reads it under the page
        and task locks and abandons the page instead of writing over the
        person's decision.
        """
        self.seed()
        gate = threading.Event()
        self.addCleanup(gate.set)
        compose = FakeCompose(gate=gate)
        save = FakeSave()
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("还在写。").script,
            compose=compose,
            save=save,
            compose_wait_s=0.05,
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].status == "running"
        )
        op_id = task.ops[-1].id
        self.assertTrue(compose.entered.wait(5))

        # Another process cancels the op by writing the task file; this
        # process's per-op event is never set.
        stored = TaskStore(self.layout).load()
        cancelled = replace(
            stored,
            ops=tuple(
                replace(
                    op,
                    status="cancelled",
                    error="the op was cancelled by a person",
                    error_code="cancelled",
                    updated_at="2026-11-10T09:06:00Z",
                )
                if op.id == op_id
                else op
                for op in stored.ops
            ),
        )
        TaskStore(self.layout).save(cancelled)
        rev_before = cancelled.rev

        gate.set()
        self.assertTrue(compose.exited.wait(5))
        threading.Event().wait(0.1)

        # The composed page was abandoned: no save, no result, and the op keeps
        # the terminal state the other writer gave it. A cancel is a log write,
        # so it did not move the content revision.
        self.assertEqual(save.requests, [])
        self.assertFalse((self.layout.wiki / "entropy.md").exists())
        final = self.task()
        final_op = next(op for op in final.ops if op.id == op_id)
        self.assertEqual(final_op.status, "cancelled")
        self.assertEqual(final_op.error_code, "cancelled")
        self.assertEqual(final.rev, rev_before)
        self.assertIsNone(final.result)
        self.assertEqual(
            [
                entry
                for entry in self.session_entries(session_id)
                if entry.get("type") == "task_op_result"
            ],
            [],
        )

    def test_a_search_that_finishes_after_cancel_writes_no_cards(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        resume = RecordingResume()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
            search=search,
            wait_s=0.05,
            resume=resume,
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].status == "running"
        )
        op_id = task.ops[-1].id

        cancelled = client.post(f"/api/v1/task/ops/{op_id}/cancel")
        self.assertTrue(cancelled.json()["cancelled"])

        gate.set()
        self.assertTrue(search.exited.wait(5))
        threading.Event().wait(0.1)

        task = self.task()
        # The final guard dropped the late answer: no cards, no done op.
        self.assertEqual(task.cards, ())
        self.assertEqual(task.ops[-1].status, "cancelled")
        self.assertEqual(resume.notices, [])
        self.assertEqual(
            self.last_tool_result_content(session_id, "web_search").count(
                "still"
            ),
            1,
        )

    def test_cancelling_a_finished_op_changes_nothing(self) -> None:
        resume = RecordingResume()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("找到了。").script,
            spawn=lambda work: work(),
            resume=resume,
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        task = self.task()
        op_id = task.ops[0].id
        rev = task.rev

        response = client.post(f"/api/v1/task/ops/{op_id}/cancel")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["cancelled"])
        self.assertEqual(body["op"]["status"], "done")
        # The delivery counter is internal; the op record shape is unchanged.
        self.assertNotIn("resumeAttempt", body["op"])
        after = self.task()
        self.assertEqual(after.rev, rev)
        self.assertEqual(len(after.cards), 2)
        self.assertEqual(resume.notices, [])


class TaskRestartTests(TaskApiCase):
    """A second app on the same store reconciles the task and resumes once."""

    def deps(self, model, *, ids, resume=_UNSET, search=None, spawn=None):
        return AppDeps(
            clock=FixedClock(),
            ids=ids,
            model=model,
            task_search=search if search is not None else FakeSearch(),
            task_fetch=FakeFetch(),
            task_stage=FakeStage(),
            task_compose=FakeCompose(),
            task_save=None,
            task_spawn=spawn,
            task_wait_s=0.05,
            task_pick_wait_s=0.05,
            task_resume=RecordingResume() if resume is _UNSET else resume,
        )

    def seed_running_op(
        self, session_id: str, *, kind: str = "search", op_id: str = "op-99"
    ) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=0,
                ops=(
                    Op(
                        id=op_id,
                        kind=kind,  # type: ignore[arg-type]
                        actor="agent",
                        status="running",
                        input={
                            "query": "entropy",
                            "cardId": "card-1",
                            "sessionId": session_id,
                        },
                        expected_rev=0,
                        created_at="2026-11-10T09:00:00Z",
                        updated_at="2026-11-10T09:00:00Z",
                    ),
                ),
            )
        )

    def test_a_completed_op_left_unresumed_resumes_once_at_startup(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        resume = RecordingResume()
        ids = SequentialIds()
        first = TestClient(
            create_app(
                self.deps(
                    FauxModelClient(
                        FauxModelClient.tool_call(
                            "t1", "web_search", '{"query": "entropy"}'
                        ).script,
                        FauxModelClient.text("还在搜。").script,
                    ),
                    ids=ids,
                    resume=resume,
                    search=search,
                )
            )
        )
        session_id = self.new_session(first)
        self.ask(first, session_id)
        gate.set()
        self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].status == "done"
        )
        # The test recorder suppressed the continuation: this is the state a
        # crash between completion and resume leaves behind.
        self.assertEqual(len(resume.notices), 1)
        self.assertFalse(self.task().ops[0].resume_started)

        # A new core reads the same project and continues it once.
        model = FauxModelClient(
            FauxModelClient.text("搜好了，继续。").script
        )
        second_deps = self.deps(
            model, ids=ids, resume=None, spawn=lambda work: work()
        )
        with TestClient(create_app(second_deps)) as client:
            entries = self.wait_for_resume_done(
                client, session_id, "搜好了，继续。"
            )
            delivered = [
                entry
                for entry in entries
                if entry.get("type") == "task_op_result"
            ]
            self.assertEqual(len(delivered), 1)
            # Delivery is recorded after the run commits the entry; poll for it
            # rather than racing the resumer's last task write.
            self.wait_for_task(
                lambda item: bool(item.ops) and item.ops[0].resume_started
            )
            self.assertEqual(self.task().ops[0].resume_error, "")

        # A third start sees the marker and starts nothing; the model has no
        # script left, so a second resume would be recorded as a failure.
        third_deps = self.deps(FauxModelClient(), ids=ids, resume=None)
        with TestClient(create_app(third_deps)):
            pass
        after = self.session_entries(session_id)
        self.assertEqual(
            len(
                [
                    entry
                    for entry in after
                    if entry.get("type") == "task_op_result"
                ]
            ),
            1,
        )
        self.assertEqual(self.task().ops[0].resume_error, "")

    def test_an_active_op_is_interrupted_then_cancelled_for_good(self) -> None:
        ids = SequentialIds()
        first = TestClient(
            create_app(self.deps(FauxModelClient(), ids=ids))
        )
        session_id = self.new_session(first)
        self.seed_running_op(session_id)

        second_deps = self.deps(FauxModelClient(), ids=ids, resume=None)
        with TestClient(create_app(second_deps)) as client:
            summary = client.get(
                "/api/v1/task", params={"view": "summary"}
            ).json()
            shown = next(item for item in summary["ops"] if item["id"] == "op-99")
            self.assertTrue(shown["interrupted"])
            self.assertEqual(shown["status"], "running")

            cancelled = client.post("/api/v1/task/ops/op-99/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertTrue(cancelled.json()["cancelled"])
            self.assertEqual(cancelled.json()["op"]["status"], "cancelled")

            # A cancelled op cannot be resumed, in this process or a later one.
            refused = client.post("/api/v1/task/ops/op-99/resume")
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["code"], "conflict")

        third_deps = self.deps(FauxModelClient(), ids=ids, resume=None)
        with TestClient(create_app(third_deps)) as client:
            summary = client.get(
                "/api/v1/task", params={"view": "summary"}
            ).json()
            shown = next(item for item in summary["ops"] if item["id"] == "op-99")
            self.assertEqual(shown["status"], "cancelled")
            self.assertEqual(
                [
                    entry
                    for entry in self.session_entries(session_id)
                    if entry.get("type") == "task_op_result"
                ],
                [],
            )

    def test_an_interrupted_op_can_be_resumed_by_a_person(self) -> None:
        ids = SequentialIds()
        first = TestClient(
            create_app(self.deps(FauxModelClient(), ids=ids))
        )
        session_id = self.new_session(first)
        self.seed_running_op(session_id)
        model = FauxModelClient(
            FauxModelClient.text("续跑完成。").script
        )
        deps = self.deps(
            model, ids=ids, resume=None, spawn=lambda work: work()
        )

        with TestClient(create_app(deps)) as client:
            resumed = client.post("/api/v1/task/ops/op-99/resume")

            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertTrue(resumed.json()["resumed"])
            self.assertNotIn("resumeAttempt", resumed.json()["op"])
            task = self.task()
            op = next(item for item in task.ops if item.id == "op-99")
            self.assertEqual(op.status, "done")
            self.assertFalse(op.interrupted)
            self.assertTrue(op.resume_requested)
            self.assertTrue(op.resume_started)
            entries = self.session_entries(session_id)
            delivered = [
                entry
                for entry in entries
                if entry.get("type") == "task_op_result"
            ]
            self.assertEqual(len(delivered), 1)
            self.assertEqual(
                [
                    entry.get("content")
                    for entry in entries
                    if entry.get("type") == "message"
                    and entry.get("role") == "assistant"
                ][-1],
                "续跑完成。",
            )

            # A second resume of the finished op is refused.
            again = client.post("/api/v1/task/ops/op-99/resume")
            self.assertEqual(again.status_code, 409, again.text)


class OpResumeRunTests(TaskApiCase):
    """The real resumer continues the session the executor finished for."""

    def seed(self, spec: dict | None = None, *, rev: int = 3) -> None:
        TaskStore(self.layout).save(_seed_approved_task(spec, rev=rev))

    def test_a_finished_search_resumes_the_session_at_the_boundary(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        # One model across both turns: the first two scripts answer the
        # conversation, and the third belongs to the resumed run.
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
            FauxModelClient.text("搜到了两篇，已经放进任务。").script,
        )
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=search,
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=FakeCompose(),
                    task_save=None,
                    task_wait_s=0.05,
                    task_pick_wait_s=0.05,
                    task_resume=None,
                )
            )
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertIn(card["status"], ("requested", "running"))
        self.assertEqual(
            [
                entry
                for entry in self.session_entries(session_id)
                if entry.get("type") == "task_op_result"
            ],
            [],
        )

        gate.set()
        entries = self.wait_for_resume_done(
            client, session_id, "搜到了两篇，已经放进任务。"
        )

        # The continuation is on the same session, delivered once: the delta
        # record and the answer that followed it.
        delivered = [
            entry for entry in entries if entry.get("type") == "task_op_result"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["kind"], "search")
        self.assertEqual(delivered[0]["status"], "done")
        self.assertEqual(delivered[0]["result"]["hits"], 2)
        self.assertEqual(
            [item["title"] for item in delivered[0]["result"]["cards"]],
            ["Entropy", "Information"],
        )

        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].resume_started
        )
        op = task.ops[0]
        self.assertEqual(op.status, "done")
        self.assertTrue(op.resume_requested)
        self.assertTrue(op.resume_started)
        self.assertEqual(delivered[0]["rev"], task.rev)

        # A reconnecting reader sees the same continuation by session id.
        session = client.get(f"/api/v1/sessions/{session_id}")
        self.assertEqual(session.status_code, 200, session.text)
        body = session.json()
        self.assertEqual(body["state"]["taskOp"]["opId"], op.id)
        self.assertEqual(body["state"]["taskOp"]["status"], "done")
        self.assertEqual(body["state"]["taskOp"]["rev"], task.rev)
        self.assertEqual(
            [
                message["content"]
                for message in body["messages"]
                if not message.get("isStep")
                and message["role"] == "assistant"
            ][-1],
            "搜到了两篇，已经放进任务。",
        )

        # The model read a bounded delta, never the task or its op log.
        prompt = "\n".join(
            message.content for message in model.requests[-1].messages
        )
        self.assertIn(op.id, prompt)
        self.assertIn("task rev", prompt)
        self.assertIn("Entropy", prompt)
        self.assertNotIn("expectedRev", prompt)
        self.assertNotIn('"ops":', prompt)

    def test_after_resume_a_human_change_makes_the_next_mutation_a_conflict(
        self,
    ) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=0,
                materials=(_stored_material(),),
            )
        )
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
            FauxModelClient.text("搜到了。").script,
            # The next person question, after the resume.
            FauxModelClient.tool_call(
                "t2",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.text("框不了，先看看任务。").script,
        )
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=search,
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=FakeCompose(),
                    task_save=None,
                    task_wait_s=0.05,
                    task_pick_wait_s=0.05,
                )
            )
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)
        gate.set()
        self.wait_for_resume_done(client, session_id, "搜到了。")
        finished = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].resume_started
        )
        delta_rev = finished.rev

        # A person draws a frame from the view they saw, moving the task on.
        human = client.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1"],
                "expectedRev": delta_rev,
            },
        )
        self.assertEqual(human.status_code, 200, human.text)
        self.assertEqual(human.json()["rev"], delta_rev + 1)

        # The resumed agent's observation is the delta's revision, not the
        # human's newer one, so its next mutation is refused as a conflict.
        events = self.ask(client, session_id, "画个框")
        observed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(observed["error"])
        self.assertEqual(observed["diagnostics"][0]["code"], "conflict")
        content = self.last_tool_result_content(session_id, "frame_blocks")
        self.assertIn("inspect", content.lower())
        task = self.task()
        self.assertEqual([op.kind for op in task.ops], ["search", "frame"])
        self.assertEqual(task.rev, delta_rev + 1)

    def test_a_resume_for_a_deleted_session_records_the_failure(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.text("还在搜。").script,
        )
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=search,
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=FakeCompose(),
                    task_save=None,
                    task_wait_s=0.05,
                    task_pick_wait_s=0.05,
                )
            )
        )
        session_id = self.new_session(client)
        self.ask(client, session_id)

        deleted = client.delete(f"/api/v1/sessions/{session_id}")
        self.assertEqual(deleted.status_code, 204, deleted.text)

        gate.set()
        task = self.wait_for_task(
            lambda item: bool(item.ops)
            and item.ops[0].status == "done"
            and bool(item.ops[0].resume_error)
        )

        # The completion is inspectable, terminal and not retried: the attempt
        # was made and failed, and the reason is on the op. Delivery was never
        # recorded, so a restart does not try again either (the error is the
        # permanent-failure marker).
        op = task.ops[0]
        self.assertTrue(op.resume_requested)
        self.assertFalse(op.resume_started)
        self.assertEqual(op.resume_attempt, 1)
        self.assertIn("FileNotFoundError", op.resume_error)
        self.assertEqual(len(task.ops), 1)
        self.assertEqual(len(model.requests), 2)
        # The failure is recorded asynchronously; let the resume thread finish
        # before the temp project is cleaned up.
        threading.Event().wait(0.1)

        # A later core reads the recorded failure and does not attempt again.
        ids = SequentialIds()
        again_deps = AppDeps(
            clock=FixedClock(),
            ids=ids,
            model=FauxModelClient(),
            task_search=FakeSearch(),
            task_fetch=FakeFetch(),
            task_stage=FakeStage(),
            task_compose=FakeCompose(),
            task_save=None,
            task_wait_s=0.05,
            task_pick_wait_s=0.05,
            task_resume=None,
            task_spawn=lambda work: work(),
        )
        with TestClient(create_app(again_deps)):
            pass
        after = self.task()
        self.assertEqual(after.ops[0].resume_attempt, 1)
        self.assertEqual(after.ops[0].resume_error, op.resume_error)

    def test_a_completion_while_a_turn_is_running_queues_the_resume(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.addCleanup(lambda: model.release.set())
        search = FakeSearch(gate=gate)
        faux = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            # The second model call is held: the turn is still running when the
            # executor finishes the search.
            FauxModelClient.text("还在搜。").script,
            FauxModelClient.text("搜完了，可以继续。").script,
        )
        model = HoldingModel(faux, call=2)
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=search,
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=FakeCompose(),
                    task_save=None,
                    task_wait_s=0.05,
                    task_pick_wait_s=0.05,
                    task_resume=None,
                    task_resume_poll_s=0.01,
                )
            )
        )
        session_id = self.new_session(client)
        outcome: dict[str, object] = {}

        def ask() -> None:
            try:
                outcome["events"] = self.ask(client, session_id)
            except Exception as err:  # noqa: BLE001 - re-raised in the test
                outcome["error"] = err

        worker = threading.Thread(target=ask)
        worker.start()
        try:
            # The tool returned requested/running, and the model is now in its
            # second call: the session still owns its one run slot.
            self.assertTrue(model.entered.wait(5))
            gate.set()
            self.wait_for_task(
                lambda item: bool(item.ops) and item.ops[0].status == "done"
            )
            # The executor finished, but the session is busy, so the completion
            # waits: no concurrent second run, no delta yet.
            threading.Event().wait(0.1)
            self.assertEqual(
                [
                    entry
                    for entry in self.session_entries(session_id)
                    if entry.get("type") == "task_op_result"
                ],
                [],
            )
            self.assertTrue(
                client.get(f"/api/v1/sessions/{session_id}").json()["state"][
                    "busy"
                ]
            )

            model.release.set()
            worker.join(5)
        finally:
            model.release.set()
            worker.join(5)
        self.assertNotIn("error", outcome)

        entries = self.wait_for_resume_done(
            client, session_id, "搜完了，可以继续。"
        )
        delivered = [
            entry for entry in entries if entry.get("type") == "task_op_result"
        ]
        self.assertEqual(len(delivered), 1)
        self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].resume_started
        )

    def test_a_queued_resume_keeps_the_completion_rev_across_a_human_write(
        self,
    ) -> None:
        """The delta names the revision the op finished at, not the claim's.

        A person may write while the completion is queued behind a running turn.
        If the resumed session were told the newer revision, its next mutation
        would be accepted and could overwrite what the person did. The delta must
        carry the completion's revision, so that mutation is a conflict.
        """
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=0,
                materials=(_stored_material(),),
            )
        )
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        faux = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            # Held: the turn is still running when the search finishes.
            FauxModelClient.text("还在搜。").script,
            FauxModelClient.text("搜完了。").script,
            FauxModelClient.tool_call(
                "t2",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1"]}',
            ).script,
            FauxModelClient.text("框不了，先看看任务。").script,
        )
        model = HoldingModel(faux, call=2)
        self.addCleanup(model.release.set)
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=search,
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=FakeCompose(),
                    task_save=None,
                    task_wait_s=0.05,
                    task_pick_wait_s=0.05,
                    task_resume=None,
                    task_resume_poll_s=0.01,
                )
            )
        )
        session_id = self.new_session(client)
        outcome: dict[str, object] = {}

        def ask() -> None:
            try:
                outcome["events"] = self.ask(client, session_id)
            except Exception as err:  # noqa: BLE001 - re-raised in the test
                outcome["error"] = err

        worker = threading.Thread(target=ask)
        worker.start()
        try:
            self.assertTrue(model.entered.wait(5))
            gate.set()
            finished = self.wait_for_task(
                lambda item: bool(item.ops) and item.ops[0].status == "done"
            )
            completion_rev = finished.rev
            # The completion is saved but the resume is still queued; the person
            # draws a frame from the revision the completion left behind.
            human = client.post(
                "/api/v1/task/frame",
                json={
                    "docId": "raw/web/entropy-a1.html",
                    "blocks": ["b1"],
                    "expectedRev": completion_rev,
                },
            )
            self.assertEqual(human.status_code, 200, human.text)
            self.assertEqual(human.json()["rev"], completion_rev + 1)
            model.release.set()
            worker.join(5)
        finally:
            model.release.set()
            worker.join(5)
        self.assertNotIn("error", outcome)

        entries = self.wait_for_resume_done(client, session_id, "搜完了。")
        delivered = [
            entry for entry in entries if entry.get("type") == "task_op_result"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["rev"], completion_rev)
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[0].resume_started
        )
        op = task.ops[0]
        self.assertEqual(op.resume_rev, completion_rev)
        self.assertTrue(op.resume_started)

        # The resumed session observed the completion rev, so its next mutation
        # is refused rather than overwriting the person's frame.
        events = self.ask(client, session_id, "画个框")
        observed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(observed["error"])
        self.assertEqual(observed["diagnostics"][0]["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, completion_rev + 1)
        self.assertEqual([op.kind for op in task.ops], ["search", "frame"])

    def test_a_slow_write_resumes_with_the_page_result_and_rev(self) -> None:
        self.seed()
        gate = threading.Event()
        self.addCleanup(gate.set)
        compose = FakeCompose(gate=gate)
        save = FakeSave()
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("还在写。").script,
            FauxModelClient.text("页面已经写好。").script,
        )
        client = TestClient(
            create_app(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    model=model,
                    task_search=FakeSearch(),
                    task_fetch=FakeFetch(),
                    task_stage=FakeStage(),
                    task_compose=compose,
                    task_save=save,
                    task_compose_wait_s=0.05,
                    task_resume=None,
                )
            )
        )
        session_id = self.new_session(client)

        events = self.ask(client, session_id)

        card = next(data for kind, data in events if kind == "observation")
        self.assertIn(card["status"], ("requested", "running"))
        self.assertIsNone(self.task().result)

        gate.set()
        entries = self.wait_for_resume_done(client, session_id, "页面已经写好。")

        delivered = [
            entry for entry in entries if entry.get("type") == "task_op_result"
        ]
        self.assertEqual(len(delivered), 1)
        result = delivered[0]["result"]
        self.assertEqual(result["status"], "written")
        self.assertEqual(result["page"], "wiki/entropy.md")
        # The delta has the page's fate, not its text, diff or effect ids.
        self.assertNotIn("diff", result)
        self.assertNotIn("effectIds", result)
        # The executor saved the page the composed block described.
        self.assertEqual(len(save.requests), 1)
        self.assertEqual(save.requests[0].wiki_path, "wiki/entropy.md")
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].resume_started
        )
        self.assertEqual(delivered[0]["rev"], task.rev)
        self.assertEqual(task.result["status"], "written")
        self.assertTrue(task.ops[-1].resume_started)


class ResumeDeliveryTests(TaskApiCase):
    """Delivery is decided by the session's entry, not a pre-consumed marker.

    The op records how many attempts were made; whether the conversation was
    actually continued is read from the committed ``task_op_result``. A crash
    between the completion and the append is therefore retried, an entry that
    is already there is marked delivered without a run, and a permanent failure
    records ``resume_error`` and is not attempted again.
    """

    def deps(self, model, *, ids):
        return AppDeps(
            clock=FixedClock(),
            ids=ids,
            model=model,
            task_search=FakeSearch(),
            task_fetch=FakeFetch(),
            task_stage=FakeStage(),
            task_compose=FakeCompose(),
            task_save=None,
            task_wait_s=0.05,
            task_pick_wait_s=0.05,
            task_resume=None,
            task_spawn=lambda work: work(),
        )

    def seed_completion(self, session_id: str, *, rev: int = 4) -> None:
        TaskStore(self.layout).save(
            Task(
                id="task-1",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                rev=rev,
                ops=(
                    Op(
                        id="op-1",
                        kind="search",
                        actor="agent",
                        status="done",
                        input={"query": "entropy", "sessionId": session_id},
                        expected_rev=0,
                        created_at="2026-11-10T09:00:00Z",
                        updated_at="2026-11-10T09:00:00Z",
                        result={"hits": 0, "cardIds": []},
                        resume_requested=True,
                        resume_rev=rev,
                    ),
                ),
            )
        )

    def deliveries(self, session_id: str) -> list[dict]:
        return [
            entry
            for entry in self.session_entries(session_id)
            if entry.get("type") == "task_op_result"
        ]

    def test_a_completion_whose_append_crashed_is_retried_once(self) -> None:
        ids = SequentialIds()
        first = self.client(ids=ids, resume=RecordingResume())
        session_id = self.new_session(first)
        self.seed_completion(session_id)
        model = FauxModelClient(FauxModelClient.text("继续。").script)

        with TestClient(create_app(self.deps(model, ids=ids))):
            pass

        task = self.task()
        op = task.ops[0]
        self.assertTrue(op.resume_started)
        self.assertEqual(op.resume_attempt, 1)
        self.assertEqual(op.resume_error, "")
        self.assertEqual(len(self.deliveries(session_id)), 1)
        self.assertEqual(len(model.requests), 1)

        # A later core sees the delivered entry and does not run again.
        with TestClient(create_app(self.deps(FauxModelClient(), ids=ids))):
            pass
        self.assertEqual(len(self.deliveries(session_id)), 1)
        self.assertEqual(len(model.requests), 1)

    def test_an_entry_already_in_the_session_is_marked_without_a_run(self) -> None:
        ids = SequentialIds()
        first = self.client(ids=ids, resume=RecordingResume())
        session_id = self.new_session(first)
        self.seed_completion(session_id)
        JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        ).append(
            session_id,
            {
                **task_op_result_entry(
                    op_id="op-1",
                    kind="search",
                    status="done",
                    rev=4,
                    result={"status": "done", "hits": 0, "cards": []},
                ),
                "id": "e-delivered-1",
                "ts": "2026-11-10T09:10:00Z",
            },
        )
        model = FauxModelClient()

        with TestClient(create_app(self.deps(model, ids=ids))):
            pass

        task = self.task()
        op = task.ops[0]
        # Delivered: no attempt is charged and the model is never called.
        self.assertTrue(op.resume_started)
        self.assertEqual(op.resume_attempt, 0)
        self.assertEqual(op.resume_error, "")
        self.assertEqual(model.requests, [])
        self.assertEqual(len(self.deliveries(session_id)), 1)


class HumanSearchTests(TaskApiCase):
    """The source side searches the same task the agent's web_search feeds."""

    def test_a_person_searches_and_the_chat_inspect_sees_the_cards(self) -> None:
        search = FakeSearch()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "cards"}'
            ).script,
            FauxModelClient.text("看到卡片了。").script,
            search=search,
            spawn=lambda work: work(),
        )

        found = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )

        self.assertEqual(found.status_code, 200, found.text)
        body = found.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["rev"], 1)
        self.assertEqual(body["query"], "entropy")
        self.assertEqual(
            [card["title"] for card in body["cards"]],
            ["Entropy", "Information"],
        )
        self.assertEqual(body["cards"][0]["id"], "card-1")
        self.assertEqual(body["cards"][0]["url"], "https://example.com/entropy")
        self.assertEqual(search.queries, ["entropy"])

        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual([op.kind for op in task.ops], ["search"])
        op = task.ops[0]
        self.assertEqual(op.actor, "human")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 0)
        self.assertEqual(op.input["query"], "entropy")
        self.assertFalse(op.resume_requested)

        # The same one task: the chat reads the cards the person's search found.
        session_id = self.new_session(client)
        events = self.ask(client, session_id)
        observed = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["rev"], 1)
        self.assertEqual(observed["hits"], 2)

    def test_a_stale_view_is_a_conflict_and_writes_no_op(self) -> None:
        client = self.client(spawn=lambda work: work())
        first = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )
        self.assertEqual(first.status_code, 200, first.text)

        stale = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )

        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "conflict")
        task = self.task()
        self.assertEqual(task.rev, 1)
        self.assertEqual(len(task.ops), 1)

    def test_a_failed_search_is_op_failed_and_writes_no_cards(self) -> None:
        client = self.client(
            search=FakeSearch(error=RuntimeError("the engine refused")),
            spawn=lambda work: work(),
        )

        response = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["code"], "op_failed")
        task = self.task()
        self.assertEqual(task.cards, ())
        self.assertEqual(task.ops[0].status, "failed")
        self.assertEqual(task.ops[0].error_code, "op_failed")
        self.assertIn("the engine refused", task.ops[0].error)

    def test_a_blank_search_is_refused_and_writes_no_op(self) -> None:
        client = self.client(spawn=lambda work: work())

        response = client.post(
            "/api/v1/task/search",
            json={"query": "   ", "expectedRev": 0},
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertIsNone(TaskStore(self.layout).load())


class PageEditTaskTests(TaskApiCase):
    """A person's real page save is one more human op on the same task."""

    PAGE = "wiki/entropy.md"

    def seed(self) -> None:
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\n"
            "title: Entropy\n"
            "created: 2026-11-01\n"
            "updated: 2026-11-01\n"
            "---\n\n"
            "# Entropy\n\noriginal prose\n",
            encoding="utf-8",
        )
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=3,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(PAGE_SPEC),
                    rev=3,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                ),
                result={
                    "status": "written",
                    "page": "wiki/entropy.md",
                    "slug": "entropy",
                    "title": "Entropy",
                    "action": "create",
                    "summary": "written wiki/entropy.md",
                    "reason": "",
                    "written": ["wiki/entropy.md"],
                    "skipped": [],
                    "failed": [],
                    "diff": "",
                    "added": 0,
                    "removed": 0,
                },
            )
        )

    def save(
        self,
        client,
        body: str = "# Entropy\n\nthe person's edit\n",
        *,
        preview: bool = False,
        base_revision: str | None = None,
    ):
        opened = client.get(
            "/api/projects/demo/page", params={"path": self.PAGE}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        return client.post(
            "/api/projects/demo/page/save",
            json={
                "path": self.PAGE,
                "body": body,
                "baseRevision": (
                    opened.json()["revision"]
                    if base_revision is None
                    else base_revision
                ),
                "preview": preview,
            },
        )

    def test_a_person_saves_a_page_and_the_task_records_a_human_edit(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        saved = self.save(client)

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["changed"])
        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertIsNone(task.approval)
        # A page edit is not a page write: the agent's last result stands.
        self.assertEqual(task.result["status"], "written")
        self.assertEqual(task.result["page"], "wiki/entropy.md")
        self.assertEqual([op.kind for op in task.ops], ["edit"])
        op = task.ops[0]
        self.assertEqual(op.actor, "human")
        self.assertEqual(op.status, "done")
        self.assertEqual(op.expected_rev, 3)
        self.assertEqual(op.input["path"], self.PAGE)
        self.assertEqual(op.input["revision"], saved.json()["revision"])
        self.assertTrue(op.result["approvalCleared"])

        # The chat's inspect sees the person's step on the shared task, bounded:
        # the op kind, who did it and how it ended, never its input or the page.
        chat = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("看到你的编辑了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(chat)
        events = self.ask(chat, session_id)
        observed = next(
            data for kind, data in events if kind == "observation"
        )
        self.assertEqual(observed["tool"], "inspect")
        self.assertEqual(observed["rev"], 4)
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("edit", content)
        self.assertIn("human", content)
        self.assertIn("rev 4", content)
        self.assertNotIn("expectedRev", content)
        self.assertNotIn("original prose", content)

    def test_the_route_records_the_edit_before_releasing_the_page_lock(
        self,
    ) -> None:
        self.seed()
        locks = ReleasingPageLocks(self.layout)
        client = self.client(spawn=lambda work: work(), page_locks=locks)

        saved = self.save(client)

        self.assertEqual(saved.status_code, 200, saved.text)
        # The edit op exists at the moment the page lock is released: the
        # route records it inside the lock, not after it.
        self.assertEqual(locks.ops_at_release[-1], ["edit"])

    def test_a_preview_records_no_task_op(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        preview = self.save(client, preview=True)

        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["changed"])
        self.assertIn("+the person's edit", preview.json()["diff"])
        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual(task.ops, ())
        self.assertIsNotNone(task.approval)
        self.assertNotIn(
            "the person's edit",
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
        )

    def test_a_save_that_changes_nothing_records_no_task_op(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())
        opened = client.get(
            "/api/projects/demo/page", params={"path": self.PAGE}
        )
        self.assertEqual(opened.status_code, 200, opened.text)

        saved = client.post(
            "/api/projects/demo/page/save",
            json={
                "path": self.PAGE,
                "body": opened.json()["body"],
                "baseRevision": opened.json()["revision"],
            },
        )

        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["changed"])
        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual(task.ops, ())
        self.assertIsNotNone(task.approval)

    def test_a_page_conflict_records_no_task_op(self) -> None:
        self.seed()
        client = self.client(spawn=lambda work: work())

        stale = self.save(client, base_revision="a revision that is not")

        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["code"], "page.changed")
        task = self.task()
        self.assertEqual(task.rev, 3)
        self.assertEqual(task.ops, ())
        self.assertIsNotNone(task.approval)
        self.assertNotIn(
            "the person's edit",
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
        )

    def test_a_failed_task_record_is_surfaced_and_the_write_is_refused(
        self,
    ) -> None:
        """A save whose task record fails is not reported as fully consistent.

        The page is the person's and stays saved. The task could not record the
        edit, so the go-ahead on it could not be cleared either; the approval's
        page digest is the second line of defence, and a compose that would
        overwrite the person's page is refused rather than silently accepted.
        """
        page = self.layout.wiki / "entropy.md"
        original = (
            "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\n"
            "# Entropy\n\noriginal prose\n"
        )
        page.write_text(original, encoding="utf-8")
        spec = {**PAGE_SPEC, "action": "update"}
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=3,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(spec),
                    rev=3,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                    page_digest=blob_digest(original),
                ),
            )
        )
        service = _FailingEditService(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
            search=FakeSearch(),
            fetch=FakeFetch(),
            stage=FakeStage(),
            spawn=lambda work: work(),
            compose=FakeCompose(),
        )
        client = self.client(
            FauxModelClient.tool_call(
                "t1",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": spec}),
            ).script,
            FauxModelClient.text("页面被改过，写不了。").script,
            tasks=service,
        )

        saved = self.save(client)

        # The page was saved; the route says so and names the record failure
        # instead of reporting a fully consistent state.
        self.assertEqual(saved.status_code, 500, saved.text)
        self.assertEqual(saved.json()["code"], "task.recordFailed")
        self.assertIn("saved", saved.json()["detail"].lower())
        self.assertIn(
            "the person's edit", page.read_text(encoding="utf-8")
        )

        # The record failure left the approval stored, but it is bound to the
        # page the person approved; the person's save changed it, so the
        # approved compose conflicts and the page is kept.
        session_id = self.new_session(client)
        events = self.ask(client, session_id)
        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        self.assertIn(
            "the person's edit", page.read_text(encoding="utf-8")
        )
        self.assertNotIn("composed from", page.read_text(encoding="utf-8"))
        task = self.task()
        self.assertEqual(task.result["status"], "failed")
        self.assertIn("changed since", task.result["reason"])
        write = next(op for op in task.ops if op.kind == "write")
        self.assertFalse(EffectJournal(self.layout).query(run_id=write.id))


class PageEditInvalidationTests(TaskApiCase):
    """A person's page edit removes the basis a standing approval covered."""

    PAGE = "wiki/entropy.md"

    def seed(self) -> None:
        page = self.layout.wiki / "entropy.md"
        page.write_text(
            "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\n"
            "# Entropy\n\noriginal prose\n",
            encoding="utf-8",
        )
        TaskStore(self.layout).save(
            Task(
                id="task-7",
                created_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:05:00Z",
                rev=3,
                materials=(_stored_material(),),
                frames=(
                    Frame(
                        id="frame-1",
                        doc_id="raw/web/entropy-a1.html",
                        blocks=("b1", "b2"),
                        source="agent",
                    ),
                ),
                approval=Approval(
                    frame_id="frame-1",
                    spec=dict(PAGE_SPEC),
                    rev=3,
                    by="human",
                    at="2026-11-10T09:05:00Z",
                ),
            )
        )

    def edit_page(self, client, body: str) -> None:
        opened = client.get(
            "/api/projects/demo/page", params={"path": self.PAGE}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        saved = client.post(
            "/api/projects/demo/page/save",
            json={
                "path": self.PAGE,
                "body": body,
                "baseRevision": opened.json()["revision"],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

    def test_a_page_edit_makes_the_agents_stale_compose_a_conflict(self) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.text("已批准。").script,
            # The next turn still believes the task is at the revision it read.
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("冲突了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id, "看看批准")
        self.assertEqual(self.task().rev, 3)

        person_text = "# Entropy\n\nthe person's text\n"
        self.edit_page(client, person_text)

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertIsNone(task.approval)
        self.assertEqual(task.ops[-1].kind, "edit")
        self.assertEqual(task.ops[-1].actor, "human")

        events = self.ask(client, session_id, "写页")
        composed = next(data for kind, data in events if kind == "observation")
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")
        content = self.last_tool_result_content(session_id, "compose_page")
        self.assertIn("conflict", content)
        self.assertIn("inspect", content.lower())

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertFalse(any(op.kind == "write" for op in task.ops))
        self.assertIsNone(task.result)
        # The person's prose is the page, exactly as they saved it.
        self.assertEqual(
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
            "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\n"
            + person_text,
        )

    def test_after_the_page_edit_a_fresh_compose_asks_for_approval_again(
        self,
    ) -> None:
        self.seed()
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.text("看过了。").script,
            FauxModelClient.tool_call(
                "t2", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("需要重新批准。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)
        self.ask(client, session_id, "先看看")
        self.assertEqual(self.task().rev, 3)

        person_text = "# Entropy\n\nthe person's newer text\n"
        self.edit_page(client, person_text)
        self.assertEqual(self.task().rev, 4)

        events = self.ask(client, session_id, "再提交")
        observations = [data for kind, data in events if kind == "observation"]
        inspected = observations[0]
        self.assertEqual(inspected["tool"], "inspect")
        self.assertEqual(inspected["rev"], 4)
        self.assertIsNone(inspected.get("request"))
        content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("none", content.lower())
        # The approval was cleared by the edit, not left looking usable.
        self.assertNotIn("stale", content.lower())

        composed = observations[1]
        self.assertEqual(composed["tool"], "compose_page")
        self.assertEqual(composed["status"], "needs_approval")
        self.assertEqual(composed["rev"], 5)
        self.assertEqual(composed["request"]["frameId"], "frame-1")

        task = self.task()
        self.assertEqual(task.rev, 5)
        self.assertIsNone(task.approval)
        self.assertIsNotNone(task.request)
        self.assertFalse(any(op.kind == "write" for op in task.ops))
        self.assertEqual(
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
            "---\ntitle: Entropy\ncreated: 2026-11-01\n---\n\n"
            + person_text,
        )


class ChatEndToEndTests(TaskApiCase):
    """The whole chat flow through the API seam, one revision at a time.

    Search, pick, frame and the first compose are one run; the person approves
    between runs; the second run must read the new approval before the write.
    No network and no real model: the nodes are fakes and the model is scripted.
    """

    def test_search_pick_frame_propose_approve_compose_result(self) -> None:
        client = self.client(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "entropy"}'
            ).script,
            FauxModelClient.tool_call(
                "t2", "pick_card", '{"cardId": "card-1"}'
            ).script,
            FauxModelClient.tool_call(
                "t3",
                "frame_blocks",
                '{"docId": "raw/web/entropy-a1.html", "blocks": ["b1", "b2"]}',
            ).script,
            FauxModelClient.tool_call(
                "t4",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准写这一页。").script,
            # The second run starts after the person's decision and reads it
            # before proposing the same page again.
            FauxModelClient.tool_call(
                "t5", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t6",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.tool_call(
                "t7", "inspect", '{"view": "result"}'
            ).script,
            FauxModelClient.text("页面写好了。").script,
            spawn=lambda work: work(),
        )
        session_id = self.new_session(client)

        first = self.ask(client, session_id, "找资料写一页")
        observations = [data for kind, data in first if kind == "observation"]
        self.assertEqual(
            [(item["tool"], item["rev"]) for item in observations],
            [
                ("web_search", 1),
                ("pick_card", 2),
                ("frame_blocks", 3),
                ("compose_page", 4),
            ],
        )
        self.assertEqual(observations[3]["status"], "needs_approval")
        self.assertEqual(observations[3]["request"]["rev"], 4)

        task = self.task()
        self.assertEqual(task.rev, 4)
        self.assertEqual(task.status, "needs_approval")
        self.assertEqual(task.request.frame_id, "frame-1")
        self.assertFalse((self.layout.wiki / "entropy.md").exists())

        approved = client.post(
            "/api/v1/task/approve",
            json={
                "taskId": task.id,
                "rev": 4,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["rev"], 5)
        self.assertEqual(approved.json()["approval"]["rev"], 5)

        second = self.ask(client, session_id, "批准了，写吧")
        observations = [data for kind, data in second if kind == "observation"]
        self.assertEqual(
            [(item["tool"], item["rev"]) for item in observations],
            [("inspect", 5), ("compose_page", 6), ("inspect", 6)],
        )
        self.assertEqual(observations[1]["status"], "written")
        self.assertEqual(observations[1]["path"], "wiki/entropy.md")
        self.assertTrue(observations[1]["wrote_wiki"])
        self.assertEqual(observations[2]["status"], "written")
        self.assertNotIn("diff", observations[1])
        self.assertNotIn("effectIds", json.dumps(observations))

        task = self.task()
        self.assertEqual(task.rev, 6)
        self.assertEqual(task.status, "done")
        self.assertIsNone(task.approval)
        self.assertIsNone(task.request)
        self.assertEqual(task.result["status"], "written")
        self.assertEqual(task.result["page"], "wiki/entropy.md")
        self.assertFalse(any(key == "effectIds" for key in task.result))
        # Every step is an op with the revision it was requested at and who
        # asked; the person's approval is one of them.
        self.assertEqual(
            [(op.kind, op.actor, op.status, op.expected_rev) for op in task.ops],
            [
                ("search", "agent", "done", 0),
                ("pick", "agent", "done", 1),
                ("frame", "agent", "done", 2),
                ("approve", "human", "done", 4),
                ("write", "agent", "done", 5),
            ],
        )
        page = (self.layout.wiki / "entropy.md").read_text(encoding="utf-8")
        self.assertIn("<!-- na-clip:raw/web/entropy-a1.html -->", page)
        self.assertIn("composed from b1", page)


class OriginEndToEndTests(TaskApiCase):
    """The origin panel drives the same task the chat reads, then a page edit
    removes the approval basis; a restarted core reads the same task."""

    def test_source_side_flow_then_page_edit_then_agent_conflict(self) -> None:
        # One id source across the app instances that write the one task: an op
        # id names an op in the task file, so a second core must not reuse it.
        ids = SequentialIds()
        # The origin's search, pick and frame are human routes with no session.
        origin = self.client(spawn=lambda work: work(), ids=ids)
        found = origin.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["rev"], 1)
        picked = origin.post(
            "/api/v1/task/pick",
            json={"cardId": "card-1", "expectedRev": 1},
        )
        self.assertEqual(picked.status_code, 200, picked.text)
        self.assertEqual(picked.json()["rev"], 2)
        framed = origin.post(
            "/api/v1/task/frame",
            json={
                "docId": "raw/web/entropy-a1.html",
                "blocks": ["b1", "b2"],
                "expectedRev": 2,
            },
        )
        self.assertEqual(framed.status_code, 200, framed.text)
        self.assertEqual(framed.json()["rev"], 3)
        self.assertEqual(framed.json()["frame"]["source"], "human")

        chat = self.client(
            FauxModelClient.tool_call(
                "t1", "inspect", '{"view": "summary"}'
            ).script,
            FauxModelClient.tool_call(
                "t2",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请批准。").script,
            FauxModelClient.tool_call(
                "t3", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t4",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("写好了。").script,
            # After the person's page edit, the same session tries again with
            # the revision it saw, then reads the task and proposes anew.
            FauxModelClient.tool_call(
                "t5",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("冲突了。").script,
            FauxModelClient.tool_call(
                "t6", "inspect", '{"view": "approval"}'
            ).script,
            FauxModelClient.tool_call(
                "t7",
                "compose_page",
                json.dumps({"frameId": "frame-1", "spec": PAGE_SPEC}),
            ).script,
            FauxModelClient.text("请重新批准。").script,
            spawn=lambda work: work(),
            ids=ids,
        )
        session_id = self.new_session(chat)

        # The chat sees the person's steps on the one task, with the revision.
        proposed = self.ask(chat, session_id, "提议写页")
        observations = [
            data for kind, data in proposed if kind == "observation"
        ]
        self.assertEqual(observations[0]["tool"], "inspect")
        self.assertEqual(observations[0]["rev"], 3)
        summary_content = self.last_tool_result_content(session_id, "inspect")
        self.assertIn("search", summary_content)
        self.assertIn("pick", summary_content)
        self.assertIn("frame", summary_content)
        self.assertIn("human", summary_content)
        self.assertEqual(observations[1]["status"], "needs_approval")
        self.assertEqual(observations[1]["rev"], 4)

        # The confirmation panel approves the request the chat card shows.
        task = self.task()
        self.assertEqual(task.request.frame_id, "frame-1")
        approved = chat.post(
            "/api/v1/task/approve",
            json={
                "taskId": task.id,
                "rev": 4,
                "frameId": "frame-1",
                "spec": PAGE_SPEC,
                "decision": "approve",
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["rev"], 5)

        written = self.ask(chat, session_id, "批准了")
        observations = [data for kind, data in written if kind == "observation"]
        self.assertEqual(observations[0]["tool"], "inspect")
        self.assertEqual(observations[0]["rev"], 5)
        self.assertEqual(observations[1]["status"], "written")
        self.assertEqual(observations[1]["rev"], 6)
        task = self.task()
        self.assertEqual(task.rev, 6)
        self.assertEqual(task.result["status"], "written")

        # The person opens the page the agent wrote and edits its prose. That is
        # one more human op on the task, and it moves the revision.
        opened = chat.get(
            "/api/projects/demo/page", params={"path": "wiki/entropy.md"}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        person_body = "# Entropy\n\nthe person's own paragraph\n"
        saved = chat.post(
            "/api/projects/demo/page/save",
            json={
                "path": "wiki/entropy.md",
                "body": person_body,
                "baseRevision": opened.json()["revision"],
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        task = self.task()
        self.assertEqual(task.rev, 7)
        self.assertEqual(task.ops[-1].kind, "edit")
        self.assertEqual(task.ops[-1].actor, "human")
        # The edit is not a page write: the agent's result still stands.
        self.assertEqual(task.result["status"], "written")

        # A restarted core reads the same task and the person's page.
        restarted = self.client(ids=ids)
        summary = restarted.get("/api/v1/task", params={"view": "summary"})
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["rev"], 7)
        self.assertEqual(summary.json()["counts"]["frames"], 1)
        result = restarted.get("/api/v1/task", params={"view": "result"})
        self.assertEqual(result.json()["result"]["status"], "written")
        self.assertIn(
            "the person's own paragraph",
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
        )

        # The agent still believes the task is at rev 6: its next compose is a
        # conflict and writes nothing. The person's page is untouched.
        conflict = self.ask(chat, session_id, "再写一次")
        composed = next(
            data for kind, data in conflict if kind == "observation"
        )
        self.assertTrue(composed["error"])
        self.assertEqual(composed["diagnostics"][0]["code"], "conflict")

        # After reading the task as it stands, the write needs a new approval.
        again = self.ask(chat, session_id, "重新提")
        observations = [data for kind, data in again if kind == "observation"]
        self.assertEqual(observations[0]["tool"], "inspect")
        self.assertEqual(observations[0]["rev"], 7)
        self.assertEqual(observations[1]["status"], "needs_approval")
        self.assertEqual(observations[1]["rev"], 8)
        ops = self.task().ops
        edit_index = next(
            index for index, op in enumerate(ops) if op.kind == "edit"
        )
        self.assertFalse(
            any(op.kind == "write" for op in ops[edit_index + 1 :])
        )
        self.assertEqual(
            [op.kind for op in ops],
            ["search", "pick", "frame", "approve", "write", "edit"],
        )
        self.assertIn(
            "the person's own paragraph",
            (self.layout.wiki / "entropy.md").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "composed from", (self.layout.wiki / "entropy.md").read_text(
                encoding="utf-8"
            )
        )

    def test_a_long_human_search_can_be_cancelled(self) -> None:
        gate = threading.Event()
        self.addCleanup(gate.set)
        search = FakeSearch(gate=gate)
        spawned: list[threading.Thread] = []

        def spawn(work) -> None:
            thread = threading.Thread(
                target=work, name="task-cancel-test", daemon=True
            )
            spawned.append(thread)
            thread.start()

        client = self.client(search=search, wait_s=0.05, spawn=spawn)

        found = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )

        self.assertEqual(found.status_code, 200, found.text)
        self.assertIn(found.json()["status"], ("requested", "running"))
        self.assertEqual(found.json()["cards"], [])
        task = self.wait_for_task(
            lambda item: bool(item.ops) and item.ops[-1].status == "running"
        )
        op_id = task.ops[-1].id

        cancelled = client.post(f"/api/v1/task/ops/{op_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(cancelled.json()["cancelled"])

        gate.set()
        self.assertTrue(search.exited.wait(5))
        # The executor takes the task lock once more on its way out. Join it,
        # rather than sleeping, so no writer is still running when the
        # temporary project is removed.
        for thread in spawned:
            thread.join(5)
            self.assertFalse(thread.is_alive(), "the search executor did not stop")
        task = self.task()
        self.assertEqual(task.cards, ())
        self.assertEqual(task.ops[-1].status, "cancelled")
        self.assertEqual(task.rev, 0)


if __name__ == "__main__":
    unittest.main()
