from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from noteagent.api.files import delete_project_file
from noteagent.api.projects import list_projects
from noteagent.project.lifecycle import (
    create_named_project,
    delete_named_project,
    project_contents,
    reset_named_project,
)
from noteagent.project.layout import default_layout
from noteagent.project.names import ProjectNameError, validate_project_name
from noteagent.project.trash import (
    list_trash,
    move_to_trash,
    prune_trash,
    restore_trash,
    trash_root,
)


class TrashTests(unittest.TestCase):
    def test_deleting_a_project_keeps_it_and_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            page = dest / "wiki" / "concepts" / "x.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("# x\nkept\n", encoding="utf-8")

            result = delete_named_project("notes", repo_root=repo, confirm_name="notes")

            self.assertFalse(dest.exists())
            # The trash is a peer of the projects, so the delete could not take it.
            self.assertTrue(trash_root(repo / "projects").is_dir())
            self.assertEqual(list_projects(repo), [])
            kept = list_trash(repo / "projects")
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]["kind"], "project")
            self.assertEqual(kept[0]["id"], result["trashId"])

            restore_trash(repo / "projects", result["trashId"])

            self.assertTrue(dest.is_dir())
            self.assertEqual(page.read_text(encoding="utf-8"), "# x\nkept\n")
            self.assertEqual(list_trash(repo / "projects"), [])

    def test_restoring_over_a_name_takes_the_current_one_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            page = repo / "projects" / "notes" / "wiki" / "a.md"
            page.write_text("old\n", encoding="utf-8")
            removed = delete_named_project("notes", repo_root=repo, confirm_name="notes")

            # A new project of the same name is made before the old one comes back.
            create_named_project("notes", repo_root=repo)
            page.write_text("new\n", encoding="utf-8")

            restored = restore_trash(repo / "projects", removed["trashId"])

            self.assertEqual(page.read_text(encoding="utf-8"), "old\n")
            self.assertIsNotNone(restored["replaced"])
            # What the restore displaced is itself recoverable.
            kept = list_trash(repo / "projects")
            self.assertEqual(len(kept), 1)
            self.assertEqual(kept[0]["id"], restored["replaced"])

    def test_reset_keeps_the_pages_it_wiped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            page = dest / "wiki" / "concepts" / "x.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text("# x\n", encoding="utf-8")

            result = reset_named_project("notes", repo_root=repo, confirm_name="notes")

            self.assertFalse(page.exists())
            self.assertIn("wiki/concepts/x.md", result["deletedFiles"])
            self.assertIsNotNone(result["trashId"])
            kept = list_trash(repo / "projects")
            self.assertEqual(kept[0]["kind"], "wiki")

            restore_trash(repo / "projects", str(result["trashId"]))

            self.assertTrue(page.is_file())
            # The wiki the reset restored in its place did not disappear silently.
            self.assertTrue((dest / "wiki" / "index.md").is_file())

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            page = repo / "projects" / "notes" / "wiki" / "a.md"
            page.write_text("# a\n", encoding="utf-8")

            result = reset_named_project("notes", repo_root=repo, dry_run=True)

            self.assertTrue(page.is_file())
            self.assertIsNone(result["trashId"])
            self.assertEqual(list_trash(repo / "projects"), [])

    def test_a_deleted_source_is_kept_and_comes_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            layout = default_layout(dest)
            source = dest / "raw" / "web" / "kept.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("# kept\n", encoding="utf-8")

            result = delete_project_file(layout, "raw/web/kept.md")

            self.assertFalse(source.exists())
            self.assertIsNotNone(result["trashId"])
            kept = list_trash(dest.parent)
            self.assertEqual(kept[0]["kind"], "file")
            self.assertEqual(kept[0]["relativePath"], "raw/web/kept.md")

            restore_trash(dest.parent, str(result["trashId"]))

            self.assertEqual(source.read_text(encoding="utf-8"), "# kept\n")

    def test_what_has_been_kept_too_long_is_let_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "gone.md"
            source.write_text("x", encoding="utf-8")
            old = datetime.now(timezone.utc) - timedelta(days=40)
            kept = move_to_trash(base, source, kind="file", name="gone.md", now=old)

            self.assertEqual(prune_trash(base, now=datetime.now(timezone.utc)), [kept["id"]])
            self.assertEqual(list_trash(base), [])

    def test_an_unreadable_timestamp_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "gone.md"
            source.write_text("x", encoding="utf-8")
            move_to_trash(base, source, kind="file", name="gone.md")
            meta = next(trash_root(base).glob("*/meta.json"))
            text = meta.read_text(encoding="utf-8").replace('"removedAt": "', '"removedAt": "not a time')
            meta.write_text(text, encoding="utf-8")

            self.assertEqual(prune_trash(base), [])
            self.assertEqual(len(list_trash(base)), 1)

    def test_counts_what_deleting_would_take(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            (dest / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
            (dest / "wiki" / "concepts" / "x.md").write_text("# x\n", encoding="utf-8")
            (dest / "raw" / "web").mkdir(parents=True, exist_ok=True)
            (dest / "raw" / "web" / "s.md").write_text("# s\n", encoding="utf-8")
            (dest / "notes" / "concepts").mkdir(parents=True, exist_ok=True)
            (dest / "notes" / "concepts" / "n.md").write_text("n\n", encoding="utf-8")

            counts = project_contents("notes", repo_root=repo)

            # index.md and log.md are the program's own and are not counted.
            self.assertEqual(counts["pages"], 1)
            self.assertEqual(counts["sources"], 1)
            self.assertEqual(counts["notes"], 1)
            self.assertGreater(counts["bytes"], 0)

    def test_a_hidden_folder_is_not_a_project_name(self) -> None:
        for bad in (".trash", ".hidden"):
            with self.subTest(bad=bad):
                with self.assertRaises(ProjectNameError):
                    validate_project_name(bad)


if __name__ == "__main__":
    unittest.main()
