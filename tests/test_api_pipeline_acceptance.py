"""The pipeline as the product promises it: one approval, and a result by run id.

These are the acceptance paths: a complete run from intent to a written page,
the failures that must say what happened, and the surfaces an Agent uses —
start, status, events, approve, cancel — all addressed by the run id.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.ingest.clips import load_clip_store
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.ingest.topic_map import map_clips
from noteagent.pipeline.acquire import AcquireStage
from noteagent.pipeline.confirm import (
    compile_result_from,
    pipeline_write_context,
    store_for_compile,
)
from noteagent.pipeline.framing import FrameStage
from noteagent.pipeline.planning import PlanStage
from noteagent.project.init import init_project

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


_LONG = "熵是不确定性的度量，它衡量一个随机变量的平均信息量。"

GENERATION = """---FILE: wiki/entropy.md---
---
type: concept
title: 熵
created: 2026-11-18
updated: 2026-11-18
tags: []
related: []
sources: []
---

# 熵

熵衡量一个随机变量的平均信息量。
---END FILE---
"""


def _thread_spawn(work) -> None:
    threading.Thread(target=work, name="pipeline-test", daemon=True).start()


def _deps(*, stages=(), planner=None, compile=None, spawn=None) -> AppDeps:
    return AppDeps(
        clock=FixedClock(),
        ids=SequentialIds(),
        pipeline_stages=tuple(stages),
        pipeline_planner=planner,
        pipeline_compile=compile,
        pipeline_spawn=spawn or (lambda work: work()),
    )


def _cards(count: int) -> list[CandidateCard]:
    return [
        CandidateCard(
            title=f"Page {index}",
            url=f"https://example.com/page-{index}",
            site="example.com",
            snippet="",
            why="search",
            via="web_search",
        )
        for index in range(count)
    ]


def _capture(url: str = "https://example.com/entropy") -> Capture:
    html = (
        "<html><body><article>"
        '<h2 data-na-block="b1">定义</h2>'
        f'<p data-na-block="b2">{_LONG}</p>'
        "</article></body></html>"
    )
    return Capture(
        url=url,
        title="熵",
        markdown=_LONG,
        html=html,
        capture_method="test",
        fidelity="ok",
        reasons=(),
    )


def _plan():
    def plan(layout, store, cancel):
        collection = store.collections[0]
        payload = {
            "intent": store.intent,
            "candidates": [
                {
                    "slug": "entropy",
                    "title": "熵",
                    "coverage": "write",
                    "wikiPath": "wiki/entropy.md",
                    "collectionIds": [collection.id],
                    "outline": [
                        {
                            "id": "s1",
                            "title": "定义",
                            "collectionId": collection.id,
                            "blockIds": list(collection.block_ids[:1]),
                        }
                    ],
                }
            ],
        }
        return map_clips(
            layout,
            store=store,
            verify_sources=False,
            map_text=json.dumps(payload),
            cancel=cancel,
        )

    return plan


def _writing_compile():
    calls = []

    def compile(layout, confirmation, cancel):
        calls.append(confirmation)
        store = store_for_compile(load_clip_store(layout), confirmation)
        result = ingest_confirmed(
            layout,
            confirmed=[page.confirmed() for page in confirmation.pages],
            intent=confirmation.intent,
            topic_map=confirmation.topic_map,
            store=store,
            generation_text=GENERATION,
            cancel=cancel,
            write_context=pipeline_write_context(confirmation),
        )
        return compile_result_from(result)

    return compile, calls


def _client(tmp: str, *, stages, planner=None, compile=None, spawn=None):
    repo = Path(tmp)
    init_project(repo / "projects" / "demo")
    projects_mod.use_project("demo")
    return TestClient(
        create_app(
            _deps(stages=stages, planner=planner, compile=compile, spawn=spawn)
        )
    )


def _start(client, **fields) -> tuple[str, dict[str, object]]:
    response = client.post(
        "/api/v1/pipeline/runs", json={"intent": "熵", **fields}
    )
    assert response.status_code == 201, response.text
    run_id = response.json()["id"]
    return run_id, client.get(f"/api/v1/pipeline/runs/{run_id}").json()


class _HoldStage:
    """Holds a run open so the event stream can be watched while it moves."""

    name = "starting"

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, context) -> None:
        self.entered.set()
        self.release.wait(5)


def _read_until(client, run_id: str, marker: str) -> str:
    """Read a live event stream until ``marker`` appears, then close it."""
    text = ""
    with client.stream(
        "GET",
        f"/api/v1/pipeline/runs/{run_id}/events",
        timeout=10,
    ) as response:
        assert response.status_code == 200, response.text
        for line in response.iter_lines():
            text += line + "\n"
            if marker in text:
                break
    return text


def _frames(lines):
    """The (event, data) pairs in an SSE line stream, skipping comments."""
    kind = ""
    for line in lines:
        if line.startswith("event: "):
            kind = line[len("event: "):]
        elif line.startswith("data: "):
            yield kind, json.loads(line[len("data: "):])


class PipelineAcceptanceTests(unittest.TestCase):
    def test_one_run_writes_a_page_with_a_single_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compile, compile_calls = _writing_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=_plan(),
                    compile=compile,
                )
                run_id, run = _start(
                    client, startedBy="session", sessionId="sess-1"
                )

                self.assertEqual(run["startedBy"], "session")
                self.assertEqual(run["sessionId"], "sess-1")
                self.assertEqual(run["status"], "awaiting_confirmation")
                self.assertEqual(run["planRevision"], 1)

                events = _read_until(client, run_id, "event: awaiting_confirmation")
                self.assertIn("event: stage", events)
                self.assertIn("event: plan", events)
                self.assertIn("event: material", events)
                self.assertIn("event: awaiting_confirmation", events)

                confirmation = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/confirmation"
                ).json()
                self.assertEqual(confirmation["pages"][0]["action"], "write")

                approved = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 1},
                )
                self.assertEqual(approved.status_code, 202, approved.text)
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                self.assertEqual(run["status"], "succeeded")
                self.assertEqual(run["result"]["planRevision"], 1)
                self.assertIn("wiki/entropy.md", run["result"]["written"])
                self.assertEqual(len(compile_calls), 1)

                page_path = Path(tmp) / "projects" / "demo" / "wiki" / "entropy.md"
                self.assertTrue(page_path.is_file())
                sources = parse_inline_list(
                    parse_page(page_path.read_text(encoding="utf-8")).fields.get(
                        "sources", "[]"
                    )
                )
                source = compile_calls[0].selections[0].source_key
                self.assertIn(f"{source}#b1", sources)

                # The one approval is the only one; the run is no longer waiting.
                again = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 1},
                )
                self.assertEqual(again.status_code, 409, again.text)
                self.assertEqual(len(compile_calls), 1)

                terminal = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/events"
                )
                self.assertEqual(terminal.status_code, 200)
                self.assertIn("event: result", terminal.text)
                self.assertIn("event: progress", terminal.text)
                self.assertIn("event: done", terminal.text)
                self.assertIn('"seq":', terminal.text)

    def test_events_arrive_while_the_run_is_moving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hold = _HoldStage()
            compile, _calls = _writing_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[hold, stage, FrameStage(), PlanStage()],
                    planner=_plan(),
                    compile=compile,
                    spawn=_thread_spawn,
                )
                run_id, _run = _start(client)
                self.assertTrue(hold.entered.wait(5))

                kinds: set[str] = set()
                saw_keepalive = False
                with patch(
                    "noteagent.api.pipeline_events.KEEPALIVE_SECONDS", 0.05
                ):
                    with client.stream(
                        "GET",
                        f"/api/v1/pipeline/runs/{run_id}/events",
                        timeout=10,
                    ) as response:
                        for line in response.iter_lines():
                            if line.startswith(": keepalive"):
                                saw_keepalive = True
                                hold.release.set()
                            elif line.startswith("event: "):
                                kinds.add(line[len("event: "):])
                            elif line.startswith("data: ") and "awaiting_confirmation" in kinds:
                                break

                self.assertTrue(saw_keepalive)
                self.assertIn("stage", kinds)
                self.assertIn("progress", kinds)
                self.assertIn("awaiting_confirmation", kinds)

    def test_a_revision_bump_while_waiting_announces_a_fresh_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compile, _calls = _writing_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=_plan(),
                    compile=compile,
                )
                run_id, _run = _start(client)

                saw_first = threading.Event()
                second: list[dict[str, object]] = []
                gone = threading.Event()

                def read() -> None:
                    with client.stream(
                        "GET",
                        f"/api/v1/pipeline/runs/{run_id}/events",
                        timeout=10,
                    ) as response:
                        for kind, data in _frames(response.iter_lines()):
                            if kind != "awaiting_confirmation":
                                continue
                            if data.get("planRevision") == 1:
                                saw_first.set()
                            elif data.get("planRevision") == 2:
                                second.append(data)
                                gone.set()
                                return

                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                self.assertTrue(saw_first.wait(5))

                clips = client.get("/api/projects/demo/clips").json()
                answered = client.put(
                    "/api/projects/demo/clips",
                    json={
                        "intent": clips["intent"],
                        "collections": clips["collections"],
                        "topicMap": clips["topicMap"],
                        "decisions": clips["decisions"],
                        "choices": [
                            {
                                "slug": "entropy",
                                "action": "mention",
                                "sectionIds": [],
                            }
                        ],
                    },
                )
                self.assertEqual(answered.status_code, 200, answered.text)
                self.assertTrue(gone.wait(5))
                reader.join(1)
                self.assertEqual(second[0]["planRevision"], 2)
                self.assertEqual(
                    second[0]["pages"][0]["action"], "mention"
                )

    def test_the_agent_contract_uses_run_ids_for_every_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(tmp, stages=[])
                run_id, run = _start(
                    client, startedBy="session", sessionId="sess-2"
                )

                self.assertEqual(run["startedBy"], "session")
                self.assertEqual(run["sessionId"], "sess-2")
                self.assertEqual(run["status"], "succeeded")

                events = client.get(f"/api/v1/pipeline/runs/{run_id}/events")
                self.assertEqual(events.status_code, 200)
                self.assertIn("event: result", events.text)
                self.assertIn("event: done", events.text)
                self.assertIn('"seq":', events.text)

                approval = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 0},
                )
                self.assertEqual(approval.status_code, 409, approval.text)
                self.assertEqual(
                    approval.json()["code"], "pipeline.conflict"
                )

                cancelled = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/cancel"
                )
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(cancelled.json()["status"], "succeeded")

    def test_a_run_with_no_material_fails_with_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stage = AcquireStage(
                search=lambda query: [],
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(tmp, stages=[stage])
                run_id, run = _start(client)

                self.assertEqual(run["status"], "failed")
                self.assertIn("no material could be collected", run["error"])

                events = client.get(f"/api/v1/pipeline/runs/{run_id}/events")
                self.assertIn("event: error", events.text)
                self.assertIn("event: done", events.text)
                self.assertIn('"code": "job_failed"', events.text)
                self.assertIn("no material could be collected", events.text)

    def test_a_login_wall_only_fails_the_run_and_queues_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = _cards(1)

            def fetch(url: str, query: str) -> Capture:
                return Capture(
                    url=url,
                    title="Page 0",
                    markdown="",
                    html="",
                    capture_method="test",
                    fidelity="failed",
                    reasons=("http_forbidden",),
                    http_status=403,
                )

            stage = AcquireStage(
                search=lambda query: cards,
                fetch=fetch,
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(tmp, stages=[stage])
                _run_id, run = _start(client)
                attention = client.get(
                    "/api/projects/demo/attention?check=false"
                ).json()

                self.assertEqual(run["status"], "failed")
                self.assertIn("no material could be collected", run["error"])
                self.assertEqual(attention["capture"]["openCount"], 1)
                self.assertEqual(
                    attention["capture"]["items"][0]["url"], cards[0].url
                )

    def test_a_planning_model_failure_fails_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def plan(layout, store, cancel):
                raise RuntimeError("model is down")

            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                )
                run_id, run = _start(client)

                self.assertEqual(run["status"], "failed")
                self.assertIn("model is down", run["error"])

                events = client.get(f"/api/v1/pipeline/runs/{run_id}/events")
                self.assertIn("event: error", events.text)
                self.assertIn("model is down", events.text)


if __name__ == "__main__":
    unittest.main()
