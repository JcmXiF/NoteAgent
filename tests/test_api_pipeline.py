"""The pipeline run routes: start, read, list, cancel.

The run is the core's object, not the interface's: these tests go through the
HTTP API with a temp project and the clock/ids a test fixes, which is the seam
the rest of the pipeline tickets will use too.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    GateDecision,
    collection_to_dict,
    save_clip_store,
)
from noteagent.ingest.topic_map import map_clips
from noteagent.pipeline.acquire import AcquireStage
from noteagent.pipeline.captures import CaptureInbox
from noteagent.pipeline.frame_model import ModelFraming
from noteagent.pipeline.framing import FrameStage, Framing
from noteagent.pipeline.lock import ProjectLock
from noteagent.pipeline.materials import MaterialLog, Materials
from noteagent.pipeline.planning import PlanStage
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.service import PipelineService
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled

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


def _deps(*, stages=(), spawn=None, frame_model=None, planner=None) -> AppDeps:
    return AppDeps(
        clock=FixedClock(),
        ids=SequentialIds(),
        pipeline_stages=tuple(stages),
        pipeline_spawn=spawn,
        pipeline_frame_model=frame_model,
        pipeline_planner=planner,
    )


def _capture(url: str = "https://example.com/entropy") -> Capture:
    html = (
        "<html><head><title>熵</title></head><body><article>"
        '<p data-na-block="b1">熵是不确定性的度量。</p>'
        "</article></body></html>"
    )
    return Capture(
        url=url,
        title="熵",
        markdown="熵是不确定性的度量。",
        html=html,
        capture_method="test",
        fidelity="ok",
        reasons=(),
    )


_LONG = "熵是不确定性的度量，它衡量一个随机变量的平均信息量。"


def _two_block_capture(url: str) -> Capture:
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


def _scripted_plan(*, slug: str = "entropy", coverage: str = "write"):
    """The real mapping pass, over a scripted model answer and staged material.

    ``default_planner`` only adds ``verify_sources=False`` to this call, so the
    test drives the same parse, gates and plan construction as production.
    """
    calls: list[dict[str, object]] = []

    def plan(layout, store, cancel):
        calls.append(
            {"intent": store.intent, "collections": len(store.collections)}
        )
        collection = store.collections[0]
        payload = {
            "intent": store.intent,
            "candidates": [
                {
                    "slug": slug,
                    "title": "熵",
                    "coverage": coverage,
                    "wikiPath": f"wiki/{slug}.md",
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

    return plan, calls


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


class _BlockingStage:
    """Holds the run open so a second start can meet it."""

    name = "acquire"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, context) -> None:
        self.started.set()
        self.release.wait(5)


class _RecordingStage:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def __call__(self, context) -> None:
        self.calls.append(self.name)


def _wait_for(client, run_id: str, statuses: set[str], timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
        if body.get("status") in statuses:
            return body
        time.sleep(0.01)
    return body


@unittest.skipIf(TestClient is None, "fastapi not installed")
class PipelineStartTests(unittest.TestCase):
    def test_starting_a_run_returns_it_and_it_can_be_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(_deps(spawn=lambda work: work()))
                )
                response = client.post(
                    "/api/v1/pipeline/runs",
                    json={
                        "intent": "写一篇熵的笔记",
                        "startedBy": "session",
                        "sessionId": "s1",
                    },
                )

                self.assertEqual(response.status_code, 201, response.text)
                body = response.json()
                self.assertEqual(body["id"], "pr-1")
                self.assertEqual(body["intent"], "写一篇熵的笔记")
                self.assertEqual(body["startedBy"], "session")
                self.assertEqual(body["sessionId"], "s1")
                self.assertEqual(body["queryMode"], "precise")
                self.assertEqual(body["materialMode"], "auto")
                self.assertEqual(body["startedAt"], "2026-11-10T09:00:00Z")
                self.assertEqual(body["status"], "succeeded")
                self.assertEqual(body["stage"], "done")

                got = client.get(f"/api/v1/pipeline/runs/{body['id']}")
                self.assertEqual(got.status_code, 200, got.text)
                self.assertEqual(got.json(), body)

    def test_a_project_held_by_another_process_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            ProjectLock(layout.project_lock, clock=FixedClock()).acquire("other")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "写一篇熵的笔记"}
                )

                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(
                    response.json()["code"], "pipeline.projectBusy"
                )

    def test_a_blank_intent_is_refused_as_a_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "   "}
                )

                self.assertEqual(response.status_code, 422, response.text)

    def test_a_second_run_is_refused_while_the_first_is_still_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            stage = _BlockingStage()
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(stages=[stage])))
                first = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "第一篇"}
                )
                self.assertEqual(first.status_code, 201, first.text)
                self.assertTrue(stage.started.wait(2), "the stage never started")

                second = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "第二篇"}
                )
                self.assertEqual(second.status_code, 409, second.text)
                self.assertEqual(second.json()["code"], "pipeline.busy")

                stage.release.set()
                settled = _wait_for(client, first.json()["id"], {"succeeded"})
                self.assertEqual(settled["status"], "succeeded")

    def test_cancel_stops_the_run_between_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            stage = _BlockingStage()
            calls: list[str] = []
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, _RecordingStage("frame", calls)],
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "写一篇熵的笔记"}
                )
                self.assertEqual(started.status_code, 201, started.text)
                self.assertTrue(stage.started.wait(2), "the stage never started")

                stopped = client.post(
                    f"/api/v1/pipeline/runs/{started.json()['id']}/cancel"
                )
                self.assertEqual(stopped.status_code, 200, stopped.text)

                stage.release.set()
                settled = _wait_for(
                    client, started.json()["id"], {"cancelled"}
                )
                self.assertEqual(settled["status"], "cancelled")
                self.assertEqual(calls, [], "a later stage ran after the cancel")

    def test_a_run_lists_the_materials_it_collected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "写一篇熵的笔记"}
                )
                self.assertEqual(started.status_code, 201, started.text)
                run_id = started.json()["id"]

                PipelineService(
                    layout,
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    spawn=lambda work: work(),
                ).stage_material(_capture(), run_id=run_id)
                response = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                )

                self.assertEqual(response.status_code, 200, response.text)
                items = response.json()["materials"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["state"], "staged")
                self.assertEqual(items[0]["title"], "熵")
                self.assertTrue(items[0]["material"].startswith("raw/web/"))

    def test_an_auto_run_stages_the_materials_it_acquired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(5)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(_deps(stages=[stage], spawn=lambda work: work()))
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                self.assertEqual(started.status_code, 201, started.text)
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                materials = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"]

            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["detail"]["acquired"], 4)
            self.assertEqual(len(materials), 4)

    def test_the_frame_stage_turns_materials_into_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(4)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(stages=[stage, FrameStage()], spawn=lambda work: work())
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                materials = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"]
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(run["detail"]["framedMaterials"], 4)
            self.assertEqual(run["detail"]["frameUsage"]["modelCalls"], 0)
            self.assertEqual(run["detail"]["frameUsage"]["fallbacks"], 0)
            self.assertTrue(all(item["state"] == "framed" for item in materials))
            self.assertEqual(len(clips["collections"]), 4)
            self.assertEqual(clips["collections"][0]["blockIds"], ["b1"])
            self.assertEqual(clips["collections"][0]["blocks"], [])

    def test_the_model_frames_with_expansions_and_reports_its_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            script = ['{"expand": ["b2"]}', '{"frames": [["b1"], ["b2"]]}']
            calls: list[list[tuple[str, str]]] = []

            def complete(messages):
                calls.append(list(messages))
                return script[len(calls) - 1]

            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _two_block_capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage()],
                            spawn=lambda work: work(),
                            frame_model=ModelFraming(ask=complete),
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                materials = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"]
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(run["detail"]["frameUsage"]["modelCalls"], 2)
            self.assertEqual(run["detail"]["frameUsage"]["charsRead"], len(_LONG))
            self.assertEqual(run["detail"]["frameUsage"]["fallbacks"], 0)
            self.assertEqual(len(clips["collections"]), 2)
            self.assertEqual(materials[0]["state"], "framed")
            self.assertEqual(materials[0]["source"], "model")
            opening = calls[0][1][1]
            self.assertIn("b2", opening)
            self.assertNotIn(_LONG, opening)
            expansion = calls[1][-1][1]
            self.assertIn(_LONG, expansion)

    def test_a_spent_budget_still_frames_from_what_was_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            script = ['{"expand": ["b2"]}', '{"frames": [["b1"]]}']
            calls: list[list[tuple[str, str]]] = []

            def ask(messages):
                calls.append(list(messages))
                return script[min(len(calls) - 1, len(script) - 1)]

            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _two_block_capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage()],
                            spawn=lambda work: work(),
                            frame_model=ModelFraming(ask=ask, rounds=1),
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(run["detail"]["frameUsage"]["modelCalls"], 2)
            self.assertEqual(run["detail"]["frameUsage"]["charsRead"], len(_LONG))
            self.assertEqual(run["detail"]["frameUsage"]["fallbacks"], 0)
            self.assertEqual(clips["collections"][0]["blockIds"], ["b1"])

    def test_a_model_that_cannot_answer_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(1)

            def complete(messages):
                return "not json at all"

            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _two_block_capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage()],
                            spawn=lambda work: work(),
                            frame_model=ModelFraming(ask=complete),
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                materials = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"]

            self.assertEqual(run["detail"]["frameUsage"]["fallbacks"], 1)
            self.assertEqual(materials[0]["state"], "framed")
            self.assertEqual(materials[0]["source"], "system")
            self.assertEqual(
                run["detail"]["frameUsage"]["modelCalls"], 1
            )

    def test_the_plan_stage_plans_the_run_and_records_a_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            plan, calls = _scripted_plan()
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage(), PlanStage()],
                            spawn=lambda work: work(),
                            planner=plan,
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                clips = client.get("/api/projects/demo/clips").json()
                materials = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"]

            # A plan is a proposal, so the run waits for a person to approve it.
            self.assertEqual(run["status"], "awaiting_confirmation")
            self.assertEqual(run["stage"], "confirm")
            self.assertEqual(run["planRevision"], 1)
            self.assertEqual(run["detail"]["planPages"], 1)
            self.assertEqual(run["detail"]["planCarried"], 0)
            self.assertEqual(materials[0]["state"], "planned")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["intent"], "熵")
            self.assertEqual(calls[0]["collections"], 1)
            self.assertEqual(
                clips["topicMap"]["candidates"][0]["slug"], "entropy"
            )
            # A page nobody answered keeps the model's suggestion.
            self.assertEqual(
                clips["topicMap"]["candidates"][0]["coverage"], "write"
            )
            self.assertEqual(clips["decisions"], [])

    def test_a_person_answer_survives_the_automatic_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(),
                    choices=(GateDecision("entropy", "skip", ("s1",)),),
                ),
            )
            plan, _calls = _scripted_plan(coverage="write")
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage(), PlanStage()],
                            spawn=lambda work: work(),
                            planner=plan,
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run = client.get(
                    f"/api/v1/pipeline/runs/{started.json()['id']}"
                ).json()
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(run["detail"]["planCarried"], 1)
            self.assertEqual(
                clips["choices"],
                [{"slug": "entropy", "action": "skip", "sectionIds": ["s1"]}],
            )
            # The new plan is a proposal; the gate answers a person confirmed
            # belong to the plan they confirmed, not to this revision.
            self.assertEqual(clips["decisions"], [])

    def test_an_empty_plan_fails_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )

            def plan(layout, store, cancel):
                # The real parse of a model answer that names no page at all.
                return map_clips(
                    layout,
                    store=store,
                    verify_sources=False,
                    map_text=json.dumps(
                        {"intent": store.intent, "candidates": []}
                    ),
                    cancel=cancel,
                )

            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage(), PlanStage()],
                            spawn=lambda work: work(),
                            planner=plan,
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run = client.get(
                    f"/api/v1/pipeline/runs/{started.json()['id']}"
                ).json()

            self.assertEqual(run["status"], "failed")
            self.assertIn("missing primary", run["error"])

    def test_a_cancelled_plan_marks_the_run_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )

            def plan(layout, store, cancel):
                # What the shared planning job raises when the reader stops it.
                raise JobCancelled()

            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage(), PlanStage()],
                            spawn=lambda work: work(),
                            planner=plan,
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run = client.get(
                    f"/api/v1/pipeline/runs/{started.json()['id']}"
                ).json()

            self.assertEqual(run["status"], "cancelled")
            self.assertEqual(run["error"], "")

    def test_a_changed_intent_clears_the_plan_and_the_next_run_replans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            plan, calls = _scripted_plan()
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage(), PlanStage()],
                            spawn=lambda work: work(),
                            planner=plan,
                        )
                    )
                )
                first = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                self.assertEqual(first.status_code, 201, first.text)
                # The first run waits for a confirmation; close it before the
                # next one can own the project.
                cancelled = client.post(
                    f"/api/v1/pipeline/runs/{first.json()['id']}/cancel"
                )
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                clips = client.get("/api/projects/demo/clips").json()
                changed = client.put(
                    "/api/projects/demo/clips",
                    json={
                        "intent": "另一个问题",
                        "collections": clips["collections"],
                        "topicMap": clips["topicMap"],
                        "decisions": clips["decisions"],
                        "choices": clips["choices"],
                    },
                )
                self.assertIsNone(changed.json()["topicMap"])
                second = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run = client.get(
                    f"/api/v1/pipeline/runs/{second.json()['id']}"
                ).json()
                replanned = client.get("/api/projects/demo/clips").json()

            self.assertEqual(len(calls), 2)
            self.assertEqual(run["planRevision"], 1)
            self.assertIsNotNone(replanned["topicMap"])

    def test_a_person_edited_frame_is_recorded_and_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            cards = _cards(1)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(
                        _deps(
                            stages=[stage, FrameStage()],
                            spawn=lambda work: work(),
                        )
                    )
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                material = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"][0]["material"]
                clips = client.get("/api/projects/demo/clips").json()
                collections = clips["collections"] + [
                    {
                        "id": "manual-1",
                        "sourceKey": material,
                        "sourceLabel": "熵",
                        "blockIds": ["b1"],
                        "blocks": [],
                    }
                ]
                response = client.put(
                    "/api/projects/demo/clips",
                    json={
                        "intent": clips["intent"],
                        "collections": collections,
                        "topicMap": clips["topicMap"],
                        "decisions": clips["decisions"],
                        "choices": clips["choices"],
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            human = [
                entry
                for entry in MaterialLog(layout.materials_log).transitions()
                if entry.material == material and entry.source == "human"
            ]
            self.assertTrue(human)
            self.assertEqual(human[-1].state, "framed")
            materials = Materials(layout, clock=FixedClock())
            self.assertEqual(materials.states()[material].source, "human")
            model_calls: list[object] = []

            def complete(messages):
                model_calls.append(list(messages))
                return '{"frames": [["b1"]]}'

            summary = Framing(
                layout,
                materials=materials,
                model=ModelFraming(ask=complete),
            ).frame_run(run_id)
            self.assertEqual(summary.framed, 0)
            self.assertEqual(summary.model_calls, 0)
            self.assertEqual(model_calls, [])

    def test_a_materials_blocks_can_be_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(4)
            stage = AcquireStage(
                search=lambda query: cards,
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(_deps(stages=[stage], spawn=lambda work: work()))
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                run_id = started.json()["id"]
                material = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials"
                ).json()["materials"][0]["material"]
                response = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials/blocks",
                    params={"material": material},
                )
                missing = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/materials/blocks",
                    params={"material": "raw/web/missing.html"},
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["blocks"][0]["id"], "b1")
            self.assertTrue(response.json()["blocks"][0]["text"])
            self.assertEqual(missing.status_code, 404, missing.text)
            self.assertEqual(
                missing.json()["code"], "material.blocksMissing"
            )

    def test_a_changed_frame_clears_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            first = ClipCollection(
                id="c1",
                source_key="raw/web/a.html",
                source_label="A",
                block_ids=("b1",),
                blocks=(),
            )
            second = ClipCollection(
                id="c2",
                source_key="raw/web/a.html",
                source_label="A",
                block_ids=("b2",),
                blocks=(),
            )
            plan = {
                "intent": "熵",
                "candidates": [
                    {
                        "slug": "p",
                        "title": "P",
                        "wikiPath": "wiki/p.md",
                        "coverage": "write",
                        "outline": [],
                    }
                ],
            }
            save_clip_store(
                layout,
                ClipStore(intent="", collections=(first,), topic_map=plan),
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.put(
                    "/api/projects/demo/clips",
                    json={
                        "intent": "",
                        "collections": [
                            collection_to_dict(first),
                            collection_to_dict(second),
                        ],
                        "topicMap": plan,
                        "decisions": [],
                    },
                )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["topicMap"])

    def test_a_sign_in_wall_waits_in_the_attention_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            cards = _cards(5)

            def fetch(url: str, query: str) -> Capture:
                if url == cards[0].url:
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
                return _capture(url)

            stage = AcquireStage(
                search=lambda query: cards,
                fetch=fetch,
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(
                    create_app(_deps(stages=[stage], spawn=lambda work: work()))
                )
                started = client.post(
                    "/api/v1/pipeline/runs", json={"intent": "熵"}
                )
                self.assertEqual(started.status_code, 201, started.text)
                attention = client.get(
                    "/api/projects/demo/attention?check=false"
                ).json()

            self.assertEqual(attention["capture"]["openCount"], 1)
            self.assertEqual(
                attention["capture"]["items"][0]["url"], cards[0].url
            )
            self.assertEqual(attention["counts"]["capture"], 1)

    def test_a_queued_sign_in_page_can_be_dismissed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            item = CaptureInbox(layout.capture_inbox).add(
                url="https://example.com/paid",
                title="Paid",
                reason="http_forbidden",
                http_status=403,
                run_id="pr-1",
                at="2026-11-10T09:00:00Z",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.post(
                    "/api/projects/demo/capture-inbox/dismiss",
                    json={"id": item["id"]},
                )
                again = client.post(
                    "/api/projects/demo/capture-inbox/dismiss",
                    json={"id": item["id"]},
                )

                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["openCount"], 0)
                self.assertEqual(again.status_code, 404, again.text)
                self.assertEqual(again.json()["code"], "capture.notFound")

    def test_materials_for_an_unknown_run_are_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.get(
                    "/api/v1/pipeline/runs/does-not-exist/materials"
                )

                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.json()["code"], "pipeline.notFound")

    def test_opening_a_project_backfills_its_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            raw = layout.raw / "web" / "entropy-a1.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("<html></html>", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app(_deps(spawn=lambda work: work())))
                response = client.post(
                    "/api/projects/use", json={"name": "demo"}
                )

                self.assertEqual(response.status_code, 200, response.text)
                state = Materials(layout, clock=FixedClock()).states()[
                    "raw/web/entropy-a1.html"
                ]
                self.assertEqual(state.state, "promoted")

    def test_startup_backfills_materials_from_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            raw = layout.raw / "web" / "entropy-a1.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("<html></html>", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                with TestClient(create_app(_deps(spawn=lambda work: work()))):
                    pass

                state = Materials(layout, clock=FixedClock()).states()[
                    "raw/web/entropy-a1.html"
                ]

            self.assertEqual(state.state, "promoted")
            self.assertTrue(state.detail.get("backfilled"))

    def test_a_run_left_open_by_a_previous_core_is_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            PipelineRunStore(layout).create(
                PipelineRun(
                    id="pr-1",
                    intent="上次没跑完",
                    started_by="human",
                    started_at="2026-11-10T09:00:00Z",
                    updated_at="2026-11-10T09:00:00Z",
                    status="running",
                    stage="frame",
                )
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                with TestClient(create_app(_deps(spawn=lambda work: work()))) as client:
                    body = client.get("/api/v1/pipeline/runs/pr-1").json()

            self.assertEqual(body["status"], "interrupted")
            self.assertIn("restart", body["error"])


if __name__ == "__main__":
    unittest.main()
