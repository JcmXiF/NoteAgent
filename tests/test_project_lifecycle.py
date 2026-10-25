from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.project.init import init_project, is_valid_project
from noteagent.project.lifecycle import (
    ProjectConfirmError,
    create_named_project,
    delete_named_project,
    init_named_project,
    reset_named_project,
)
from noteagent.project.names import ProjectNameError, validate_project_name
from noteagent.util.refusal import Refusal


class ProjectNameTests(unittest.TestCase):
    def test_accepts_plain_names(self) -> None:
        self.assertEqual(validate_project_name("  information  "), "information")

    def test_rejects_escape_and_reserved(self) -> None:
        for bad in ("", "..", "../x", "a/b", "CON", "use", "aux", "foo."):
            with self.subTest(bad=bad):
                with self.assertRaises(ProjectNameError):
                    validate_project_name(bad)


class ProjectLifecycleTests(unittest.TestCase):
    def test_create_init_reset_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            created = create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            self.assertTrue(is_valid_project(dest))
            self.assertTrue(created["active"])
            pointer = json.loads((repo / ".noteagent-project").read_text(encoding="utf-8"))
            self.assertIn("projects/notes", pointer["path"].replace("\\", "/"))

            with self.assertRaises(Refusal) as caught:
                create_named_project("notes", repo_root=repo)
            self.assertEqual(caught.exception.code, "project.exists")
            self.assertEqual(caught.exception.status, 409)

            (dest / "wiki" / "index.md").unlink()
            init_named_project("notes", repo_root=repo)
            self.assertTrue((dest / "wiki" / "index.md").is_file())

            concept = dest / "wiki" / "concepts" / "x.md"
            concept.parent.mkdir(parents=True, exist_ok=True)
            concept.write_text("# x\n", encoding="utf-8")
            raw = dest / "raw" / "web" / "keep.md"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("# keep\n", encoding="utf-8")

            preview = reset_named_project("notes", repo_root=repo, dry_run=True)
            self.assertTrue(preview["dryRun"])
            self.assertTrue(concept.is_file())

            with self.assertRaises(ProjectConfirmError):
                reset_named_project("notes", repo_root=repo, confirm_name="wrong")

            reset_named_project("notes", repo_root=repo, confirm_name="notes")
            self.assertFalse(concept.exists())
            self.assertTrue(raw.is_file())
            self.assertTrue((dest / "wiki" / "index.md").is_file())

            with self.assertRaises(ProjectConfirmError):
                delete_named_project("notes", repo_root=repo, confirm_name="nope")

            deleted = delete_named_project(
                "notes", repo_root=repo, confirm_name="notes"
            )
            self.assertEqual(deleted["deleted"], "notes")
            self.assertIsNone(deleted["active"])
            self.assertFalse(dest.exists())
            self.assertFalse((repo / ".noteagent-project").exists())

    def test_delete_retargets_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "alpha")
            create_named_project("beta", repo_root=repo)
            result = delete_named_project("beta", repo_root=repo, confirm_name="beta")
            self.assertEqual(result["active"], "alpha")
            self.assertTrue((repo / "projects" / "alpha").is_dir())
            self.assertFalse((repo / "projects" / "beta").exists())

    def test_init_missing_is_not_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Refusal) as caught:
                init_named_project("ghost", repo_root=Path(tmp))
            self.assertEqual(caught.exception.code, "project.unknown")


if __name__ == "__main__":
    unittest.main()
