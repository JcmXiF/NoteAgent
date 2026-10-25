from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


def _page(layout, slug: str, body: str, title: str = "") -> Path:
    path = layout.wiki.joinpath(*f"{slug}.md".split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    name = title or slug.rsplit("/", 1)[-1]
    path.write_text(
        f"---\ntype: concept\ntitle: {name}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@unittest.skipIf(TestClient is None, "fastapi not installed")
class PageApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.layout = init_project(self.repo / "projects" / "demo")
        self.client = TestClient(create_app())
        self._patch = patch.object(projects_mod, "REPO_ROOT", self.repo)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._client_close()
        self._tmp.cleanup()

    def _client_close(self) -> None:
        self.client.close()

    def test_a_page_is_read_edited_and_read_back(self) -> None:
        page = _page(self.layout, "concepts/kl", "# KL\n\nfirst")
        opened = self.client.get(
            "/api/projects/demo/page", params={"path": "wiki/concepts/kl.md"}
        )
        self.assertEqual(opened.status_code, 200, opened.text)
        revision = opened.json()["revision"]

        preview = self.client.post(
            "/api/projects/demo/page/save",
            json={
                "path": "wiki/concepts/kl.md",
                "body": "# KL\n\nsecond",
                "baseRevision": revision,
                "preview": True,
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["changed"])
        self.assertIn("+second", preview.json()["diff"])
        self.assertIn("first", page.read_text(encoding="utf-8"))

        saved = self.client.post(
            "/api/projects/demo/page/save",
            json={
                "path": "wiki/concepts/kl.md",
                "body": "# KL\n\nsecond",
                "baseRevision": revision,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertIn("second", page.read_text(encoding="utf-8"))

    def test_a_save_from_an_old_revision_is_a_conflict(self) -> None:
        _page(self.layout, "concepts/kl", "# KL\n\nfirst")
        opened = self.client.get(
            "/api/projects/demo/page", params={"path": "wiki/concepts/kl.md"}
        ).json()
        self.client.post(
            "/api/projects/demo/page/save",
            json={"path": "wiki/concepts/kl.md", "body": "# KL\n\nmine"},
        )
        stale = self.client.post(
            "/api/projects/demo/page/save",
            json={
                "path": "wiki/concepts/kl.md",
                "body": "# KL\n\ntheirs",
                "baseRevision": opened["revision"],
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "page.changed")

    def test_history_lists_versions_and_restores_one(self) -> None:
        _page(self.layout, "concepts/kl", "# KL\n\nfirst")
        self.client.post(
            "/api/projects/demo/page/save",
            json={"path": "wiki/concepts/kl.md", "body": "# KL\n\nsecond"},
        )
        history = self.client.get(
            "/api/projects/demo/page/history",
            params={"path": "wiki/concepts/kl.md"},
        )
        self.assertEqual(history.status_code, 200, history.text)
        versions = history.json()["versions"]
        self.assertEqual(len(versions), 2)

        older = versions[1]
        read = self.client.get(
            "/api/projects/demo/page/version",
            params={"path": "wiki/concepts/kl.md", "id": older["id"]},
        )
        self.assertEqual(read.status_code, 200, read.text)
        self.assertIn("first", read.json()["content"])

        restored = self.client.post(
            "/api/projects/demo/page/restore",
            json={
                "path": "wiki/concepts/kl.md",
                "id": older["id"],
                "baseRevision": versions[0]["id"],
            },
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertTrue(restored.json()["changed"])

    def test_renaming_answers_with_what_it_rewrote(self) -> None:
        _page(self.layout, "kl", "# KL\n\n")
        _page(self.layout, "other", "See [[kl]].")
        renamed = self.client.post(
            "/api/projects/demo/page/rename",
            json={"path": "wiki/kl.md", "to": "concepts/divergence"},
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        payload = renamed.json()
        self.assertEqual(payload["path"], "wiki/concepts/divergence.md")
        self.assertEqual(payload["rewritten"], ["wiki/other.md"])

    def test_a_program_page_cannot_be_saved(self) -> None:
        saved = self.client.post(
            "/api/projects/demo/page/save",
            json={"path": "wiki/index.md", "body": "changed"},
        )
        self.assertEqual(saved.status_code, 400)
        self.assertEqual(saved.json()["code"], "file.protected")

    def test_search_answers_pages_and_says_what_it_left_out(self) -> None:
        _page(self.layout, "concepts/kl", "# KL\n\nshared word")
        _page(self.layout, "concepts/other", "# Other\n\nshared word")
        found = self.client.get(
            "/api/projects/demo/search", params={"q": "shared", "limit": 1}
        )
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(len(found.json()["pages"]), 1)
        self.assertEqual(found.json()["counts"]["pages"], 2)

    def test_attention_counts_the_queues(self) -> None:
        _page(self.layout, "concepts/kl", "# KL\n\n")
        answer = self.client.get("/api/projects/demo/attention")
        self.assertEqual(answer.status_code, 200, answer.text)
        payload = answer.json()
        self.assertEqual(
            payload["total"], sum(payload["counts"].values())
        )
        self.assertEqual(payload["counts"]["revision"], 0)
        self.assertEqual(payload["counts"]["recovery"], 0)
        self.assertIsNotNone(payload["doctor"])
        self.assertIn("findings", payload["doctor"])

    def test_deleting_a_page_says_what_pointed_at_it(self) -> None:
        _page(self.layout, "concepts/kl", "# KL\n\n")
        _page(self.layout, "concepts/other", "See [[concepts/kl]].")
        deleted = self.client.post(
            "/api/projects/demo/file/delete",
            json={"path": "wiki/concepts/kl.md"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(deleted.json()["backlinkCount"], 1)
        self.assertEqual(
            deleted.json()["backlinkPages"],
            [{"path": "wiki/concepts/other.md", "title": "other"}],
        )


if __name__ == "__main__":
    unittest.main()
