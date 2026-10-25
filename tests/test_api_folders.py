from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
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


@unittest.skipIf(TestClient is None, "fastapi not installed")
class FolderApiTests(unittest.TestCase):
    def test_a_folder_is_inspected_then_copied_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = repo / "elsewhere" / "vault"
            vault.mkdir(parents=True)
            (vault / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())

                found = client.get("/api/folders/inspect", params={"path": str(vault)})
                self.assertEqual(found.status_code, 200, found.text)
                self.assertEqual(found.json()["markdownCount"], 1)
                self.assertFalse(found.json()["canLink"])

                opened = client.post(
                    "/api/folders/open",
                    json={"path": str(vault), "mode": "copy"},
                )
                self.assertEqual(opened.status_code, 200, opened.text)
                self.assertEqual(opened.json()["name"], "vault")

                listed = client.get("/api/projects").json()["projects"]
                self.assertEqual([item["name"] for item in listed], ["vault"])
                self.assertTrue(listed[0]["active"])
                self.assertFalse(listed[0]["linked"])

    def test_opening_a_folder_that_is_not_there_is_a_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                missing = client.post(
                    "/api/folders/open",
                    json={"path": str(repo / "nope"), "mode": "link"},
                )
                self.assertEqual(missing.status_code, 404)

                unknown = client.post(
                    "/api/folders/open",
                    json={"path": str(repo), "mode": "elsewhere"},
                )
                self.assertEqual(unknown.status_code, 422)

    def test_a_linked_project_is_listed_and_can_be_let_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = init_project(repo / "elsewhere" / "notes").root
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())

                opened = client.post(
                    "/api/folders/open",
                    json={"path": str(folder), "mode": "link"},
                )
                self.assertEqual(opened.status_code, 200, opened.text)

                listed = client.get("/api/projects").json()["projects"]
                self.assertTrue(listed[0]["linked"])

                # Reading it goes through the same routes as a managed project.
                tree = client.get("/api/projects/notes/tree")
                self.assertEqual(tree.status_code, 200, tree.text)

                removed = client.post(
                    "/api/projects/notes/delete",
                    json={"confirmName": "notes"},
                )
                self.assertEqual(removed.status_code, 200, removed.text)
                self.assertTrue(removed.json()["unlinked"])
                self.assertTrue(folder.is_dir())
                self.assertEqual(client.get("/api/projects").json()["projects"], [])

    def test_the_whole_project_comes_back_as_one_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = init_project(repo / "projects" / "vault")
            (vault.wiki / "topic.md").write_text("# Topic\n", encoding="utf-8")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())

                answer = client.get("/api/projects/vault/export")

            self.assertEqual(answer.status_code, 200, answer.text)
            self.assertEqual(answer.headers["content-type"], "application/zip")
            self.assertIn("attachment", answer.headers["content-disposition"])
            with zipfile.ZipFile(io.BytesIO(answer.content)) as opened:
                self.assertIn("vault/wiki/topic.md", opened.namelist())

    def test_exporting_a_project_that_is_not_there_is_a_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                missing = client.get("/api/projects/ghost/export")
            self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
