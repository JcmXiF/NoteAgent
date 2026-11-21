"""The old pipeline no longer starts; its runs stay readable and restorable."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.effects.journal import UNDO, EffectJournal
from noteagent.ingest.write_file_blocks import (
    WriteContext,
    write_wiki_page,
)
from noteagent.pipeline.run import PipelineRun
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


@unittest.skipIf(TestClient is None, "fastapi not installed")
class PipelineRetiredTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        self.layout = init_project(repo / "projects" / "demo")
        patched = patch.object(projects_mod, "REPO_ROOT", repo)
        patched.start()
        self.addCleanup(patched.stop)
        projects_mod.use_project("demo")
        PipelineRunStore(self.layout).create(
            PipelineRun(
                id="pr-old",
                intent="旧主题",
                started_by="human",
                started_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                status="succeeded",
                stage="done",
            )
        )
        self.client = TestClient(
            create_app(
                AppDeps(clock=FixedClock(), ids=SequentialIds())
            )
        )

    def test_the_manual_start_is_closed(self) -> None:
        response = self.client.post(
            "/api/v1/pipeline/runs",
            json={"intent": "写一篇关于熵的 wiki"},
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(response.json()["code"], "pipeline.retired")

    def test_the_session_job_starts_are_closed(self) -> None:
        topic = self.client.post(
            "/api/projects/demo/topic-map",
            json={"intent": "熵"},
        )
        self.assertEqual(topic.status_code, 410, topic.text)
        self.assertEqual(topic.json()["code"], "pipeline.retired")

        compile_ = self.client.post(
            "/api/projects/demo/ingest-confirmed",
            json={"confirmed": [{"name": "Entropy"}]},
        )
        self.assertEqual(compile_.status_code, 410, compile_.text)
        self.assertEqual(
            compile_.json()["code"], "pipeline.retired"
        )

    def test_the_run_approve_is_closed(self) -> None:
        response = self.client.post(
            "/api/v1/pipeline/runs/pr-old/approve",
            json={"planRevision": 1},
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(response.json()["code"], "pipeline.retired")

    def test_the_old_run_is_still_readable(self) -> None:
        listing = self.client.get("/api/v1/pipeline/runs")

        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(
            [run["id"] for run in listing.json()["runs"]],
            ["pr-old"],
        )
        one = self.client.get("/api/v1/pipeline/runs/pr-old")
        self.assertEqual(one.status_code, 200, one.text)
        self.assertEqual(one.json()["intent"], "旧主题")

    def test_the_old_run_can_restore_a_written_page(self) -> None:
        relative = "wiki/concepts/entropy.md"
        old = (
            "---\ntype: concept\ntitle: Entropy\n---\n\n"
            "# 熵\n\n旧正文。\n"
        )
        content = (
            "---\ntype: concept\ntitle: Entropy\n---\n\n"
            "# 熵\n\n正文。\n"
        )
        target = self.layout.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(old, encoding="utf-8")
        written = write_wiki_page(
            self.layout,
            relative,
            content,
            mode="overwrite",
            write_context=WriteContext(
                run_id="pr-written",
                session_id="s-old",
                actor="agent",
            ),
        )
        PipelineRunStore(self.layout).create(
            PipelineRun(
                id="pr-written",
                intent="旧主题",
                started_by="human",
                started_at="2026-11-10T09:00:00Z",
                updated_at="2026-11-10T09:00:00Z",
                status="succeeded",
                stage="done",
                result={
                    "planRevision": 1,
                    "written": [relative],
                    "skipped": [],
                    "failed": [],
                    "effectIds": [written.effect_id],
                },
            )
        )

        preview = self.client.get(
            "/api/v1/pipeline/runs/pr-written/restore/preview"
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn(
            relative,
            [item["path"] for item in preview.json()["pages"]],
        )

        restored = self.client.post(
            "/api/v1/pipeline/runs/pr-written/restore",
            json={
                "runId": "pr-written",
                "pages": [{"path": relative, "action": "restore"}],
            },
        )

        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(len(restored.json()["restored"]), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), old)
        self.assertTrue(
            EffectJournal(self.layout).query(kind=UNDO)
        )


if __name__ == "__main__":
    unittest.main()
