from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from noteagent.api.files import delete_project_file
from noteagent.api.projects import get_project_layout, list_projects
from noteagent.project.config import save_active_project
from noteagent.project.export import export_project
from noteagent.project.folders import free_name, linked_projects, remember_folder
from noteagent.project.init import init_project
from noteagent.project.lifecycle import (
    create_named_project,
    delete_named_project,
    reset_named_project,
    resolve_named_project_dir,
)
from noteagent.project.opening import inspect_folder, open_folder
from noteagent.project.trash import list_trash, restore_trash


def _vault(root: Path) -> Path:
    """A folder of Markdown with a nested page and an attachment."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "alpha.md").write_text("# Alpha\n\nfirst\n", encoding="utf-8")
    nested = root / "ideas"
    nested.mkdir()
    (nested / "beta.md").write_text("# Beta\n\nsecond\n", encoding="utf-8")
    (nested / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


def _elsewhere_project(repo: Path, name: str = "notes") -> Path:
    """A complete project that lives outside the managed folder."""
    return init_project(repo / "elsewhere" / name).root


class InspectTests(unittest.TestCase):
    def test_counts_what_a_folder_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _vault(repo / "somewhere" / "vault")

            found = inspect_folder(folder, repo_root=repo)

            self.assertEqual(found["markdownCount"], 2)
            self.assertEqual(found["otherCount"], 1)
            self.assertGreater(found["bytes"], 0)
            self.assertFalse(found["canLink"])
            self.assertFalse(found["alreadyManaged"])

    def test_a_project_can_be_opened_where_it_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)

            found = inspect_folder(folder, repo_root=repo)

            self.assertTrue(found["canLink"])

    def test_a_folder_that_merely_has_a_wiki_folder_is_copied(self) -> None:
        # Using a folder where it is means writing into it. A folder that only
        # happens to hold a ``wiki`` folder may belong to something else, so it is
        # offered as a copy: the copy can be thrown away, their folder cannot.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = repo / "checkout"
            (folder / "wiki").mkdir(parents=True)
            (folder / "wiki" / "one.md").write_text("# One\n", encoding="utf-8")

            found = inspect_folder(folder, repo_root=repo)

            self.assertFalse(found["canLink"])
            self.assertFalse((folder / "raw").exists())
            self.assertFalse((folder / "wiki" / "index.md").exists())

    def test_a_folder_that_is_not_there_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                inspect_folder(Path(tmp) / "nope", repo_root=tmp)


class CopyTests(unittest.TestCase):
    def test_a_folder_of_pages_becomes_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = _vault(repo / "elsewhere" / "vault")

            result = open_folder(vault, repo_root=repo, mode="copy")

            dest = repo / "projects" / "vault"
            self.assertEqual(result["mode"], "copy")
            self.assertEqual(result["imported"], 2)
            self.assertEqual(result["attachments"], 1)
            # The pages keep their shape, so a folder of folders stays one.
            self.assertTrue((dest / "wiki" / "alpha.md").is_file())
            self.assertTrue((dest / "wiki" / "ideas" / "beta.md").is_file())
            # An attachment is not a page; it is kept with the originals.
            self.assertTrue((dest / "raw" / "imported" / "ideas" / "diagram.png").is_file())
            self.assertEqual(
                list_projects(repo),
                [{"name": "vault", "path": "projects/vault", "active": True, "linked": False}],
            )
            self.assertTrue((dest / "wiki" / "index.md").is_file())

    def test_the_folder_it_came_from_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = _vault(repo / "elsewhere" / "vault")

            open_folder(vault, repo_root=repo, mode="copy")

            self.assertEqual(
                sorted(path.name for path in vault.iterdir()), ["alpha.md", "ideas"]
            )

    def test_a_page_named_like_the_index_is_left_out_and_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = _vault(repo / "vault")
            (vault / "index.md").write_text("# my own index\n", encoding="utf-8")

            result = open_folder(vault, repo_root=repo, mode="copy")

            self.assertEqual(result["skipped"], ["index.md"])
            # The program's index is not replaced by the reader's page.
            self.assertNotIn(
                "my own index",
                (repo / "projects" / "vault" / "wiki" / "index.md").read_text(encoding="utf-8"),
            )

    def test_a_copy_never_takes_a_name_already_in_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("vault", repo_root=repo)
            vault = _vault(repo / "elsewhere" / "vault")

            result = open_folder(vault, repo_root=repo, mode="copy")

            self.assertEqual(result["name"], "vault-2")
            self.assertTrue((repo / "projects" / "vault-2").is_dir())


class LinkTests(unittest.TestCase):
    def test_a_project_is_read_where_it_stands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)
            (folder / "wiki" / "mine.md").write_text("# Mine\n", encoding="utf-8")

            result = open_folder(folder, repo_root=repo, mode="link")

            self.assertEqual(result["mode"], "link")
            self.assertEqual(linked_projects(repo), {"notes": folder.resolve()})
            # Nothing was copied: the page is still where the reader keeps it.
            self.assertEqual(
                (folder / "wiki" / "mine.md").read_text(encoding="utf-8"), "# Mine\n"
            )
            listed = list_projects(repo)
            self.assertEqual([item["name"] for item in listed], ["notes"])
            self.assertTrue(listed[0]["linked"])
            self.assertTrue(listed[0]["active"])

    def test_a_wiki_without_sources_is_completed_not_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = repo / "elsewhere" / "pages"
            (folder / "wiki").mkdir(parents=True)
            (folder / "wiki" / "one.md").write_text("# One\n", encoding="utf-8")

            open_folder(folder, repo_root=repo, mode="link")

            # Opening it makes it writable, and the page that was there is kept.
            self.assertTrue((folder / "raw").is_dir())
            self.assertTrue((folder / "wiki" / "one.md").is_file())
            self.assertIn(
                "[[one]]", (folder / "wiki" / "index.md").read_text(encoding="utf-8")
            )

    def test_a_plain_folder_is_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            vault = _vault(repo / "vault")

            with self.assertRaises(ValueError):
                open_folder(vault, repo_root=repo, mode="link")

    def test_opening_the_same_folder_twice_is_one_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)

            first = open_folder(folder, repo_root=repo, mode="link")
            second = open_folder(folder, repo_root=repo, mode="link")

            self.assertEqual(first["name"], second["name"])
            self.assertTrue(second["alreadyLinked"])
            self.assertEqual(len(linked_projects(repo)), 1)

    def test_removing_it_from_the_list_leaves_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)
            open_folder(folder, repo_root=repo, mode="link")

            result = delete_named_project("notes", repo_root=repo, confirm_name="notes")

            self.assertTrue(result["unlinked"])
            self.assertIsNone(result["trashId"])
            self.assertTrue(folder.is_dir())
            self.assertEqual(list_projects(repo), [])
            self.assertEqual(list_trash(repo / "projects"), [])

    def test_a_linked_project_is_found_for_reading_and_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)
            open_folder(folder, repo_root=repo, mode="link")

            layout = get_project_layout("notes", repo_root=repo)

            self.assertEqual(layout.root, folder.resolve())

    def test_a_new_project_cannot_take_a_linked_folder_s_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            open_folder(_elsewhere_project(repo), repo_root=repo, mode="link")

            with self.assertRaises(ValueError):
                resolve_named_project_dir("notes", repo_root=repo)

    def test_a_reset_keeps_what_it_wiped_and_can_put_it_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)
            page = folder / "wiki" / "kept.md"
            page.write_text("# Kept\n", encoding="utf-8")
            open_folder(folder, repo_root=repo, mode="link")

            result = reset_named_project("notes", repo_root=repo, confirm_name="notes")

            self.assertFalse(page.exists())
            # The copy lives with the managed projects, not in the reader's folder.
            kept = list_trash(repo / "projects")
            self.assertEqual(kept[0]["kind"], "wiki")

            restore_trash(repo / "projects", str(result["trashId"]))

            self.assertTrue(page.is_file())

    def test_a_removed_page_comes_back_to_the_folder_it_came_from(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = _elsewhere_project(repo)
            page = folder / "wiki" / "gone.md"
            page.write_text("# Gone\n", encoding="utf-8")
            open_folder(folder, repo_root=repo, mode="link")
            layout = get_project_layout("notes", repo_root=repo)

            result = delete_project_file(
                layout,
                "wiki/gone.md",
                project="notes",
                trash_base=repo / "projects",
            )

            self.assertFalse(page.exists())
            self.assertFalse((repo / "elsewhere" / ".trash").exists())
            restore_trash(repo / "projects", str(result["trashId"]))
            self.assertTrue(page.is_file())

    def test_two_folders_cannot_share_a_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            open_folder(_elsewhere_project(repo, "notes"), repo_root=repo, mode="link")
            other = repo / "other" / "notes"
            init_project(other)
            save_active_project(other, config_dir=repo)

            result = open_folder(other, repo_root=repo, mode="link")

            self.assertEqual(result["name"], "notes-2")
            self.assertEqual(set(linked_projects(repo)), {"notes", "notes-2"})

    def test_a_name_that_is_taken_gets_a_suffix_inside_the_limit(self) -> None:
        long = "x" * 64
        named = free_name(long, taken=[long])
        self.assertLessEqual(len(named), 64)
        self.assertTrue(named.endswith("-2"))


class ExportTests(unittest.TestCase):
    def test_the_archive_holds_the_pages_and_leaves_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            create_named_project("notes", repo_root=repo)
            dest = repo / "projects" / "notes"
            (dest / "wiki" / "topic.md").write_text("# Topic\n", encoding="utf-8")
            (dest / "raw" / "web").mkdir(parents=True, exist_ok=True)
            (dest / "raw" / "web" / "s.md").write_text("# s\n", encoding="utf-8")
            cache = dest / ".noteagent" / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "big.json").write_text("{}", encoding="utf-8")

            archive, filename, count = export_project("notes", folder=dest)
            try:
                self.assertTrue(filename.startswith("notes-"))
                self.assertTrue(filename.endswith(".zip"))
                with zipfile.ZipFile(archive) as opened:
                    names = opened.namelist()
                self.assertIn("notes/wiki/topic.md", names)
                self.assertIn("notes/raw/web/s.md", names)
                self.assertNotIn("notes/.noteagent/cache/big.json", names)
                # Everything counted was written.
                self.assertEqual(len(names), count)
            finally:
                archive.unlink(missing_ok=True)

    def test_a_missing_project_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                export_project("ghost", folder=Path(tmp) / "ghost")


class RegistryTests(unittest.TestCase):
    def test_an_entry_whose_folder_is_gone_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            folder = repo / "later" / "notes"
            folder.mkdir(parents=True)
            remember_folder(repo, "notes", folder)
            folder.rmdir()

            self.assertEqual(linked_projects(repo), {})
            # And the name it held is free again.
            self.assertEqual(free_name("notes", taken=list(linked_projects(repo))), "notes")


if __name__ == "__main__":
    unittest.main()
