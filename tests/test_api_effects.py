"""Project effects API: list, preview, and restore a run's writes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.effects.journal import (
    COMMITTED,
    WIKI_PAGE_WRITE,
    Effect,
    EffectJournal,
)
from noteagent.ingest.page_history import (
    blob_digest,
    keep_before,
    note_agent_write,
)
from noteagent.io.text_file import write_text
from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]

PAGE_PATH = "wiki/concepts/entropy.md"
PAGE_OLD = """---
type: concept
title: 信息熵
sources: []
---

# 信息熵

旧正文。
"""
PAGE_NEW = PAGE_OLD.replace("旧正文。", "新正文。")


@unittest.skipIf(TestClient is None, "fastapi not installed")
class EffectsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.layout = init_project(self.repo / "projects" / "demo")
        self.client = TestClient(create_app())
        self._patch = patch.object(projects_mod, "REPO_ROOT", self.repo)
        self._patch.start()
        self.seed()

    def tearDown(self) -> None:
        self._patch.stop()
        self.client.close()
        self._tmp.cleanup()

    def seed(self) -> None:
        page = self.layout.root.joinpath(*PAGE_PATH.split("/"))
        page.parent.mkdir(parents=True, exist_ok=True)
        write_text(page, PAGE_OLD)
        before = keep_before(self.layout, PAGE_OLD)
        write_text(page, PAGE_NEW)
        entry = note_agent_write(
            self.layout,
            PAGE_PATH,
            before=before,
            after_text=PAGE_NEW,
            mode="edit",
            effect_id="seed-1",
            run_id="run-1",
        )
        assert entry is not None
        EffectJournal(self.layout).append(
            Effect(
                kind=WIKI_PAGE_WRITE,
                ident="seed-1",
                target=PAGE_PATH,
                run_id="run-1",
                session_id="s1",
                actor="pipeline",
                before=before,
                after=entry.after,
                reversible="snapshot",
                undo="page_history",
                provider="page_history",
                status=COMMITTED,
            )
        )

    def test_effects_are_listed_for_a_run(self) -> None:
        response = self.client.get(
            "/api/v1/projects/demo/effects", params={"runId": "run-1"}
        )

        self.assertEqual(response.status_code, 200, response.text)
        effects = response.json()["effects"]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0]["id"], "seed-1")
        self.assertEqual(effects[0]["target"], PAGE_PATH)
        self.assertEqual(effects[0]["kind"], WIKI_PAGE_WRITE)
        self.assertEqual(effects[0]["status"], COMMITTED)

    def test_the_preview_classifies_and_the_restore_undoes(self) -> None:
        preview = self.client.get(
            "/api/v1/projects/demo/effects/restore/preview",
            params={"runId": "run-1"},
        )

        self.assertEqual(preview.status_code, 200, preview.text)
        page = preview.json()["pages"][0]
        self.assertEqual(page["classification"], "unchanged")
        self.assertEqual(page["defaultAction"], "restore")

        restored = self.client.post(
            "/api/v1/projects/demo/effects/restore",
            json={
                "runId": "run-1",
                "pages": [{"path": PAGE_PATH, "action": "restore"}],
            },
        )

        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(len(restored.json()["restored"]), 1)
        self.assertEqual(
            self.layout.root.joinpath(*PAGE_PATH.split("/")).read_text(
                encoding="utf-8"
            ),
            PAGE_OLD,
        )
        after = self.client.get(
            "/api/v1/projects/demo/effects/restore/preview",
            params={"runId": "run-1"},
        ).json()["pages"][0]
        self.assertEqual(after["classification"], "changed_by_restore")
        self.assertEqual(after["defaultAction"], "skip")

    def test_the_project_prefix_is_also_served(self) -> None:
        response = self.client.get(
            "/api/projects/demo/effects", params={"runId": "run-1"}
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["effects"]), 1)


if __name__ == "__main__":
    unittest.main()
