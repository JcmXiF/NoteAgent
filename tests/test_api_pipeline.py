"""The pipeline run routes: start, read, list, cancel.

The run is the core's object, not the interface's: these tests go through the
HTTP API with a temp project and the clock/ids a test fixes, which is the seam
the rest of the pipeline tickets will use too.
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.pipeline.acquire import AcquireStage
from noteagent.pipeline.captures import CaptureInbox
from noteagent.pipeline.lock import ProjectLock
from noteagent.pipeline.materials import Materials
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.service import PipelineService
from noteagent.pipeline.store import PipelineRunStore
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


def _deps(*, stages=(), spawn=None) -> AppDeps:
    return AppDeps(
        clock=FixedClock(),
        ids=SequentialIds(),
        pipeline_stages=tuple(stages),
        pipeline_spawn=spawn,
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
