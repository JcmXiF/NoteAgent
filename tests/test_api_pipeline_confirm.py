"""Confirming a run: the pages it proposes, the approval, and the compile.

The run is the server's object. Approval names a run and its plan revision, and
the core derives the pages from the plan; the client never sends a page list.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.ingest.topic_map import map_clips
from noteagent.pipeline.acquire import AcquireStage
from noteagent.pipeline.confirm import CompileResult
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


def _deps(*, stages=(), planner=None, compile=None, spawn=None) -> AppDeps:
    return AppDeps(
        clock=FixedClock(),
        ids=SequentialIds(),
        pipeline_stages=tuple(stages),
        pipeline_planner=planner,
        pipeline_compile=compile,
        pipeline_spawn=spawn,
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


def _three_page_plan(*, entropy: str = "write"):
    """The real mapping pass over a scripted model answer."""
    calls: list[dict[str, object]] = []

    def plan(layout, store, cancel):
        calls.append({"intent": store.intent, "collections": len(store.collections)})
        collection = store.collections[0]
        payload = {
            "intent": store.intent,
            "candidates": [
                {
                    "slug": "entropy",
                    "title": "熵",
                    "coverage": entropy,
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
                },
                {
                    "slug": "measurement",
                    "title": "测量",
                    "coverage": "mention",
                    "collectionIds": [collection.id],
                    "outline": [],
                },
                {
                    "slug": "noise",
                    "title": "噪声",
                    "coverage": "skip",
                    "collectionIds": [collection.id],
                    "outline": [],
                },
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


def _scripted_compile(
    result: CompileResult | None = None,
):
    calls = []

    def compile(layout, confirmation, cancel):
        calls.append(confirmation)
        return result or CompileResult(
            written=tuple(page.wiki_path for page in confirmation.writes)
        )

    return compile, calls


def _client(tmp: str, *, stages, planner, compile, spawn=None):
    repo = Path(tmp)
    init_project(repo / "projects" / "demo")
    projects_mod.use_project("demo")
    client = TestClient(
        create_app(
            _deps(
                stages=stages,
                planner=planner,
                compile=compile,
                spawn=spawn or (lambda work: work()),
            )
        )
    )
    return client


def _awaiting(client) -> tuple[str, dict[str, object]]:
    started = client.post("/api/v1/pipeline/runs", json={"intent": "熵"})
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
    return run_id, run


class PipelineConfirmationTests(unittest.TestCase):
    def test_a_planned_run_waits_with_the_pages_it_would_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, run = _awaiting(client)
                response = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/confirmation"
                )

            self.assertEqual(run["status"], "awaiting_confirmation")
            self.assertEqual(run["stage"], "confirm")
            self.assertEqual(run["planRevision"], 1)
            self.assertEqual(compile_calls, [])
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["runId"], run_id)
            self.assertEqual(body["planRevision"], 1)
            self.assertEqual(
                [(page["slug"], page["action"]) for page in body["pages"]],
                [
                    ("entropy", "write"),
                    ("measurement", "mention"),
                    ("noise", "skip"),
                ],
            )
            entropy = body["pages"][0]
            self.assertEqual(entropy["wikiPath"], "wiki/entropy.md")
            self.assertEqual(entropy["sectionIds"], ["s1"])
            self.assertEqual(entropy["sections"], [{"id": "s1", "title": "定义"}])

    def test_approval_writes_only_the_write_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 1},
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(len(compile_calls), 1)
            confirmation = compile_calls[0]
            self.assertEqual(
                [page.slug for page in confirmation.pages],
                ["entropy", "measurement", "noise"],
            )
            self.assertEqual(
                [page.slug for page in confirmation.writes], ["entropy"]
            )
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["result"]["planRevision"], 1)
            self.assertEqual(run["result"]["written"], ["wiki/entropy.md"])
            self.assertEqual(
                run["result"]["skipped"],
                [
                    {"path": "wiki/measurement.md", "reason": "mention"},
                    {"path": "wiki/noise.md", "reason": "skip"},
                ],
            )
            self.assertEqual(run["result"]["failed"], [])
            # The confirmed pass is the stored gate; mention and skip are in it
            # as actions, not as pages that were written.
            self.assertEqual(
                [(item["slug"], item["action"]) for item in clips["decisions"]],
                [
                    ("entropy", "write"),
                    ("measurement", "mention"),
                    ("noise", "skip"),
                ],
            )
            self.assertEqual(clips["choices"], [])

    def test_an_override_changes_the_action_and_the_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={
                        "planRevision": 1,
                        "overrides": [
                            {
                                "slug": "entropy",
                                "action": "skip",
                                "sectionIds": [],
                            },
                            {
                                "slug": "measurement",
                                "action": "write",
                                "sectionIds": [],
                            },
                        ],
                    },
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(
                [page.slug for page in compile_calls[0].writes],
                ["measurement"],
            )
            self.assertEqual(run["result"]["written"], ["wiki/measurement.md"])
            self.assertEqual(
                {
                    item["slug"]: item["action"]
                    for item in clips["decisions"]
                },
                {
                    "entropy": "skip",
                    "measurement": "write",
                    "noise": "skip",
                },
            )
            # The person's overrides are kept for the next plan to carry over.
            self.assertEqual(
                {
                    item["slug"]: item["action"]
                    for item in clips["choices"]
                },
                {"entropy": "skip", "measurement": "write"},
            )

    def test_a_stale_revision_is_a_conflict_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 0},
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                clips = client.get("/api/projects/demo/clips").json()

            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "pipeline.conflict")
            self.assertEqual(compile_calls, [])
            self.assertEqual(run["status"], "awaiting_confirmation")
            self.assertEqual(clips["decisions"], [])

    def test_approval_with_no_write_page_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={
                        "planRevision": 1,
                        "overrides": [
                            {
                                "slug": "entropy",
                                "action": "skip",
                                "sectionIds": [],
                            }
                        ],
                    },
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()

            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(
                response.json()["code"], "pipeline.nothingToWrite"
            )
            self.assertEqual(compile_calls, [])
            self.assertEqual(run["status"], "awaiting_confirmation")

    def test_an_unknown_page_in_an_override_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={
                        "planRevision": 1,
                        "overrides": [
                            {"slug": "elsewhere", "action": "write"}
                        ],
                    },
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()

            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["code"], "pipeline.invalid")
            self.assertEqual(compile_calls, [])
            self.assertEqual(run["status"], "awaiting_confirmation")

    def test_a_run_that_is_not_waiting_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compile, compile_calls = _scripted_compile()
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[],
                    planner=None,
                    compile=compile,
                )
                run_id, run = _awaiting(client)
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 0},
                )

            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["code"], "pipeline.conflict")
            self.assertEqual(compile_calls, [])

    def test_a_plan_with_no_write_page_is_shown_but_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan(entropy="mention")
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, _run = _awaiting(client)
                confirmation = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/confirmation"
                )
                response = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 1},
                )
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()

            self.assertEqual(confirmation.status_code, 200, confirmation.text)
            self.assertEqual(
                [page["action"] for page in confirmation.json()["pages"]],
                ["mention", "mention", "skip"],
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(
                response.json()["code"], "pipeline.nothingToWrite"
            )
            self.assertEqual(compile_calls, [])
            self.assertEqual(run["status"], "awaiting_confirmation")

    def test_editing_a_frame_while_waiting_plans_the_run_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, plan_calls = _three_page_plan()
            compile, _compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, first = _awaiting(client)
                clips = client.get("/api/projects/demo/clips").json()
                material = clips["collections"][0]["sourceKey"]
                collections = clips["collections"] + [
                    {
                        "id": "manual-1",
                        "sourceKey": material,
                        "sourceLabel": "熵",
                        "blockIds": [clips["collections"][0]["blockIds"][0]],
                        "blocks": [],
                    }
                ]
                edited = client.put(
                    "/api/projects/demo/clips",
                    json={"intent": clips["intent"], "collections": collections},
                )
                self.assertEqual(edited.status_code, 200, edited.text)
                run = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                confirmation = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/confirmation"
                )

            self.assertEqual(first["planRevision"], 1)
            # A frame change clears the plan; the waiting run plans again rather
            # than dead-ending on a confirmation with nothing behind it.
            self.assertEqual(len(plan_calls), 2)
            self.assertEqual(run["status"], "awaiting_confirmation")
            self.assertEqual(run["stage"], "confirm")
            self.assertEqual(run["planRevision"], 2)
            self.assertEqual(confirmation.status_code, 200, confirmation.text)
            self.assertEqual(confirmation.json()["planRevision"], 2)

    def test_changing_an_answer_while_waiting_bumps_the_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan, _plan_calls = _three_page_plan()
            compile, compile_calls = _scripted_compile()
            stage = AcquireStage(
                search=lambda query: _cards(1),
                fetch=lambda url, query: _capture(url),
            )
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[stage, FrameStage(), PlanStage()],
                    planner=plan,
                    compile=compile,
                )
                run_id, first = _awaiting(client)
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
                                "slug": "measurement",
                                "action": "write",
                                "sectionIds": [],
                            }
                        ],
                    },
                )
                self.assertEqual(answered.status_code, 200, answered.text)
                moved = client.get(f"/api/v1/pipeline/runs/{run_id}").json()
                stale = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 1},
                )
                confirmation = client.get(
                    f"/api/v1/pipeline/runs/{run_id}/confirmation"
                )
                approved = client.post(
                    f"/api/v1/pipeline/runs/{run_id}/approve",
                    json={"planRevision": 2},
                )

            self.assertEqual(first["planRevision"], 1)
            self.assertEqual(moved["planRevision"], 2)
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(stale.json()["code"], "pipeline.conflict")
            self.assertEqual(compile_calls[0].revision, 2)
            self.assertEqual(confirmation.json()["planRevision"], 2)
            self.assertEqual(
                [
                    (page["slug"], page["action"])
                    for page in confirmation.json()["pages"]
                ],
                [
                    ("entropy", "write"),
                    ("measurement", "write"),
                    ("noise", "skip"),
                ],
            )
            self.assertEqual(approved.status_code, 202, approved.text)

    def test_a_missing_run_has_no_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compile, _compile_calls = _scripted_compile()
            with patch.object(projects_mod, "REPO_ROOT", Path(tmp)):
                client = _client(
                    tmp,
                    stages=[],
                    planner=None,
                    compile=compile,
                )
                confirmation = client.get(
                    "/api/v1/pipeline/runs/pr-missing/confirmation"
                )
                approval = client.post(
                    "/api/v1/pipeline/runs/pr-missing/approve",
                    json={"planRevision": 0},
                )

            self.assertEqual(confirmation.status_code, 404)
            self.assertEqual(approval.status_code, 404)


if __name__ == "__main__":
    unittest.main()
