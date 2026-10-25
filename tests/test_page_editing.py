from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.frontmatter import parse_page
from noteagent.notes.store import create_note, list_notes
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal
from noteagent.wiki.editing import (
    page_backlinks,
    page_edit_view,
    page_versions,
    read_version,
    rename_page,
    restore_version,
    save_page,
)


def _write_page(layout, slug: str, body: str, *, title: str | None = None) -> Path:
    path = layout.wiki.joinpath(*f"{slug}.md".split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    name = title if title is not None else slug.rsplit("/", 1)[-1]
    path.write_text(
        f"---\ntype: concept\ntitle: {name}\nupdated: 2026-01-01\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


class PageEditViewTests(unittest.TestCase):
    def test_shows_the_body_apart_from_the_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\nprose")

            view = page_edit_view(layout, "wiki/concepts/kl.md")

            self.assertEqual(view["title"], "kl")
            self.assertIn("# KL", view["body"])
            self.assertNotIn("# KL", view["frontmatter"])
            self.assertTrue(view["frontmatter"].startswith("---\n"))
            self.assertEqual(len(str(view["revision"])), 64)

    def test_a_program_page_is_not_editable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(Refusal) as caught:
                page_edit_view(layout, "wiki/index.md")
            self.assertEqual(caught.exception.code, "file.protected")


class PageSaveTests(unittest.TestCase):
    def test_replaces_the_body_and_keeps_the_frontmatter_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = _write_page(layout, "concepts/kl", "# KL\n\nold")
            before = path.read_text(encoding="utf-8")
            view = page_edit_view(layout, "wiki/concepts/kl.md")

            result = save_page(
                layout,
                "wiki/concepts/kl.md",
                "# KL\n\nnew",
                base_revision=str(view["revision"]),
            )

            self.assertTrue(result["changed"])
            self.assertEqual(result["added"], 1)
            self.assertEqual(result["removed"], 1)
            self.assertIn("+new", str(result["diff"]))
            after = path.read_text(encoding="utf-8")
            self.assertIn("new", after)
            self.assertNotIn("old", after)
            # Every field the person did not write stays as it was; the stamp is
            # the program's, and it moves because the page now says something else.
            before_page = parse_page(before)
            after_page = parse_page(after)
            self.assertEqual(after_page.fields["type"], before_page.fields["type"])
            self.assertEqual(after_page.fields["title"], before_page.fields["title"])
            self.assertEqual(after_page.fields["updated"], "2026-10-20")

    def test_a_stale_editor_is_refused_rather_than_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = _write_page(layout, "concepts/kl", "# KL\n\nfirst")
            view = page_edit_view(layout, "wiki/concepts/kl.md")
            # Something else wrote the page after the editor opened.
            save_page(layout, "wiki/concepts/kl.md", "# KL\n\nsecond")

            with self.assertRaises(Refusal) as caught:
                save_page(
                    layout,
                    "wiki/concepts/kl.md",
                    "# KL\n\nedited",
                    base_revision=str(view["revision"]),
                )
            self.assertEqual(caught.exception.code, "page.changed")
            self.assertIn("second", path.read_text(encoding="utf-8"))

    def test_a_preview_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = _write_page(layout, "concepts/kl", "# KL\n\nold")
            before = path.read_text(encoding="utf-8")

            result = save_page(
                layout, "wiki/concepts/kl.md", "# KL\n\nnew", preview=True
            )

            self.assertTrue(result["changed"])
            self.assertIn("+new", str(result["diff"]))
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_saving_the_same_prose_is_not_a_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\nsame")
            view = page_edit_view(layout, "wiki/concepts/kl.md")

            result = save_page(
                layout,
                "wiki/concepts/kl.md",
                str(view["body"]),
                base_revision=str(view["revision"]),
            )

            self.assertFalse(result["changed"])
            self.assertEqual(result["diff"], "")
            self.assertEqual(page_versions(layout, "wiki/concepts/kl.md")["versions"][1:], [])

    def test_a_body_that_would_become_frontmatter_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.wiki / "concepts" / "plain.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Plain\n", encoding="utf-8")

            with self.assertRaises(Refusal) as caught:
                save_page(layout, "wiki/concepts/plain.md", "---\ntitle: x\n---\n")

            self.assertEqual(caught.exception.code, "page.bodyStartsFence")


class PageHistoryTests(unittest.TestCase):
    def test_each_save_is_a_version_that_can_be_read_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = _write_page(layout, "concepts/kl", "# KL\n\none")
            save_page(layout, "wiki/concepts/kl.md", "# KL\n\ntwo")

            history = page_versions(layout, "wiki/concepts/kl.md")
            self.assertEqual(len(history["versions"]), 2)
            self.assertTrue(history["versions"][0]["current"])
            older = history["versions"][1]
            self.assertEqual(older["by"], "person")
            self.assertEqual(older["mode"], "edit")

            read = read_version(layout, "wiki/concepts/kl.md", str(older["id"]))
            self.assertIn("one", read["content"])

            current = read_version(
                layout, "wiki/concepts/kl.md", str(history["versions"][0]["id"])
            )
            restored = restore_version(
                layout,
                "wiki/concepts/kl.md",
                str(older["id"]),
                base_revision=str(current["id"]),
            )
            self.assertTrue(restored["changed"])
            self.assertIn("one", path.read_text(encoding="utf-8"))
            # What was replaced is kept, so a restore is itself undoable.
            after = page_versions(layout, "wiki/concepts/kl.md")
            self.assertGreaterEqual(len(after["versions"]), 2)

    def test_an_unknown_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\none")
            with self.assertRaises(Refusal) as caught:
                read_version(layout, "wiki/concepts/kl.md", "0" * 64)
            self.assertEqual(caught.exception.code, "page.versionMissing")


class PageRenameTests(unittest.TestCase):
    def test_moves_the_page_and_rewrites_the_links_that_named_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\nbody")
            other = _write_page(
                layout,
                "concepts/other",
                "See [[concepts/kl]] and [[kl]] and [[concepts/kl|the KL]].",
            )
            create_note(layout, page="wiki/concepts/kl.md", body="a remark")

            result = rename_page(layout, "wiki/concepts/kl.md", "topics/kl-divergence")

            self.assertTrue(result["moved"])
            self.assertEqual(result["path"], "wiki/topics/kl-divergence.md")
            self.assertEqual(result["notes"], 1)
            self.assertTrue((layout.wiki / "topics" / "kl-divergence.md").is_file())
            self.assertFalse((layout.wiki / "concepts" / "kl.md").exists())
            text = other.read_text(encoding="utf-8")
            self.assertIn("[[topics/kl-divergence]]", text)
            self.assertIn("[[topics/kl-divergence|the KL]]", text)
            self.assertNotIn("[[kl]]", text)
            # The note follows the page it is about, body untouched.
            notes = list_notes(layout)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].page, "wiki/topics/kl-divergence.md")
            self.assertIn("remark", (layout.notes / "topics" / "kl-divergence" / f"{notes[0].id}.md").read_text(encoding="utf-8"))
            # The index names the page it has now.
            index = layout.wiki_index.read_text(encoding="utf-8")
            self.assertIn("topics/kl-divergence", index)
            self.assertNotIn("concepts/kl", index)

    def test_a_bare_link_that_still_resolves_is_left_as_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "kl", "# KL\n\nbody")
            other = _write_page(layout, "other", "See [[kl]].")

            result = rename_page(layout, "wiki/kl.md", "concepts/kl")

            self.assertTrue(result["moved"])
            self.assertEqual(other.read_text(encoding="utf-8"), "---\ntype: concept\ntitle: other\nupdated: 2026-01-01\n---\n\nSee [[kl]].\n")

    def test_a_name_already_taken_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "one", "# One\n\n")
            _write_page(layout, "two", "# Two\n\n")
            with self.assertRaises(Refusal) as caught:
                rename_page(layout, "wiki/one.md", "two")
            self.assertEqual(caught.exception.code, "page.exists")

    def test_the_history_follows_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\none")
            save_page(layout, "wiki/concepts/kl.md", "# KL\n\ntwo")

            rename_page(layout, "wiki/concepts/kl.md", "topics/kl")

            history = page_versions(layout, "wiki/topics/kl.md")
            self.assertEqual(len(history["versions"]), 2)

    def test_a_system_page_cannot_be_moved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(Refusal) as caught:
                rename_page(layout, "wiki/index.md", "index-2")
            self.assertEqual(caught.exception.code, "file.protected")


class BacklinkTests(unittest.TestCase):
    def test_counts_pages_that_point_here_and_notes_written_about_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(layout, "concepts/kl", "# KL\n\n")
            _write_page(layout, "concepts/one", "[[concepts/kl]]")
            _write_page(layout, "concepts/two", "[[kl]] and [[concepts/kl]]")
            _write_page(layout, "concepts/three", "[[concepts/other]]")
            create_note(layout, page="wiki/concepts/kl.md", body="a")
            create_note(layout, page="wiki/concepts/kl.md", body="b")

            found = page_backlinks(layout, "wiki/concepts/kl.md")

            self.assertEqual(found["count"], 2)
            self.assertEqual(found["notes"], 2)
            paths = {item["path"] for item in found["pages"]}
            self.assertEqual(paths, {"wiki/concepts/one.md", "wiki/concepts/two.md"})


if __name__ == "__main__":
    unittest.main()
