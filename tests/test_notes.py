from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from noteagent.notes.store import (
    create_note,
    delete_note,
    find_note_path,
    list_notes,
    mirror_dir,
    note_id_new,
    note_path,
    note_to_dict,
    normalize_page,
    parse_note_text,
    read_note,
    render_note,
    update_note,
    validate_note_id,
    wiki_page_path,
)
from noteagent.notes.store import Note
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal

MOMENT = datetime(2026, 10, 10, 19, 57, 0, tzinfo=timezone.utc)


class NoteIdTests(unittest.TestCase):
    def test_sorts_by_time_and_varies(self) -> None:
        early = note_id_new(MOMENT)
        late = note_id_new(datetime(2026, 10, 10, 19, 57, 1, tzinfo=timezone.utc))
        self.assertTrue(early.startswith("20261010-195700-"))
        self.assertLess(early, late)
        self.assertEqual(validate_note_id(early), early)

    def test_two_ids_in_the_same_second_differ(self) -> None:
        names = {note_id_new(MOMENT) for _ in range(8)}
        self.assertEqual(len(names), 8)

    def test_rejects_anything_that_could_become_a_path(self) -> None:
        for bad in ("", "  ", "../etc/passwd", "20261010-195700", "a/b", "20261010-195700-ABCD"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    validate_note_id(bad)


class PageNameTests(unittest.TestCase):
    def test_takes_a_wiki_page_and_normalises_the_separators(self) -> None:
        self.assertEqual(normalize_page("wiki/concepts/x.md"), "wiki/concepts/x.md")
        self.assertEqual(normalize_page("wiki\\concepts\\x.md"), "wiki/concepts/x.md")
        self.assertEqual(normalize_page("/wiki/concepts/x.md"), "wiki/concepts/x.md")
        self.assertEqual(normalize_page("wiki//concepts/x.md"), "wiki/concepts/x.md")

    def test_refuses_anything_that_is_not_a_wiki_page(self) -> None:
        for bad in (
            "",
            "raw/x.md",
            "concepts/x.md",
            "wiki/concepts/x.txt",
            "wiki/../raw/x.md",
            "../wiki/x.md",
        ):
            with self.subTest(page=bad):
                with self.assertRaises(ValueError):
                    normalize_page(bad)


class NotePathTests(unittest.TestCase):
    def test_mirrors_the_wiki_shape_without_the_wiki_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertEqual(
                mirror_dir(layout, "wiki/concepts/互信息.md"),
                layout.notes / "concepts" / "互信息",
            )
            self.assertEqual(
                note_path(layout, "wiki/concepts/互信息.md", "20261010-195700-ab12"),
                layout.notes / "concepts" / "互信息" / "20261010-195700-ab12.md",
            )
            page = wiki_page_path(layout, "wiki/concepts/互信息.md")
            self.assertEqual(page, layout.wiki / "concepts" / "互信息.md")

    def test_mirrors_a_flat_note_and_a_source_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertEqual(
                mirror_dir(layout, "wiki/互信息.md"),
                layout.notes / "互信息",
            )
            self.assertEqual(
                mirror_dir(layout, "wiki/sources/paper.md"),
                layout.notes / "sources" / "paper",
            )


class FrontmatterTests(unittest.TestCase):
    def test_a_written_note_reads_back_with_its_time_of_day(self) -> None:
        note = Note(
            id="20261010-195700-ab12",
            page="wiki/concepts/x.md",
            body="正文\n",
            anchor="前言",
            quote="引用",
            created="2026-10-10T19:57:00+00:00",
            updated="2026-10-10T20:31:05+00:00",
        )
        text = render_note(note)
        self.assertTrue(text.startswith("---\npage: wiki/concepts/x.md\n"))
        self.assertIn('created: "2026-10-10T19:57:00+00:00"', text)
        fields, body = parse_note_text(text)
        self.assertEqual(fields["created"], "2026-10-10T19:57:00+00:00")
        self.assertEqual(fields["updated"], "2026-10-10T20:31:05+00:00")
        self.assertEqual(fields["anchor"], "前言")
        self.assertEqual(body, "正文\n")

    def test_keys_are_written_in_one_order(self) -> None:
        text = render_note(Note(id="x", page="wiki/a.md", body="b\n"))
        keys = [line.split(":", 1)[0] for line in text.split("---\n")[1].splitlines()]
        self.assertEqual(keys, ["page", "anchor", "quote", "created", "updated"])

    def test_text_without_frontmatter_is_all_body(self) -> None:
        fields, body = parse_note_text("手写的，没有前言\n")
        self.assertEqual(fields, {})
        self.assertEqual(body, "手写的，没有前言\n")

    def test_crlf_on_disk_is_read_as_one_kind_of_line_ending(self) -> None:
        fields, body = parse_note_text("---\r\npage: wiki/a.md\r\n---\r\n\r\n正文\r\n")
        self.assertEqual(fields["page"], "wiki/a.md")
        self.assertEqual(body, "正文\n")


class StoreTests(unittest.TestCase):
    def project(self, tmp: str):
        layout = init_project(Path(tmp) / "proj")
        page = layout.wiki / "concepts" / "x.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# X\n\n正文。\n", encoding="utf-8")
        return layout, page

    def test_create_writes_the_note_and_leaves_the_wiki_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, page = self.project(tmp)
            before = page.read_bytes()
            note = create_note(
                layout,
                page="wiki/concepts/x.md",
                body="这是笔记。\n",
                anchor="前言",
                now=MOMENT,
            )
            self.assertTrue(note.id.startswith("20261010-195700-"))
            written = layout.notes / "concepts" / "x" / f"{note.id}.md"
            self.assertEqual(find_note_path(layout, note.id), written)
            self.assertEqual(note.page, "wiki/concepts/x.md")
            self.assertEqual(page.read_bytes(), before)

    def test_create_refuses_an_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            for body in ("", "   ", "\n\n"):
                with self.subTest(body=body):
                    with self.assertRaises(ValueError):
                        create_note(layout, page="wiki/concepts/x.md", body=body)
            self.assertFalse(layout.notes.exists())

    def test_create_refuses_a_page_that_is_not_a_wiki_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            with self.assertRaises(ValueError):
                create_note(layout, page="raw/x.md", body="笔记\n")

    def test_a_note_answers_to_the_page_it_names_not_its_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            moved = layout.notes / "elsewhere" / f"{note.id}.md"
            moved.parent.mkdir(parents=True, exist_ok=True)
            find_note_path(layout, note.id).rename(moved)
            self.assertEqual(read_note(layout, note.id).page, "wiki/concepts/x.md")
            self.assertEqual([item.id for item in list_notes(layout)], [note.id])

    def test_a_note_without_a_page_field_falls_back_to_where_it_sits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            handwritten = layout.notes / "concepts" / "x" / "20261010-195700-ab12.md"
            handwritten.parent.mkdir(parents=True, exist_ok=True)
            handwritten.write_text("手写的笔记\n", encoding="utf-8")
            note = read_note(layout, "20261010-195700-ab12")
            self.assertEqual(note.page, "wiki/concepts/x.md")
            self.assertEqual(note.body, "手写的笔记\n")

    def test_a_note_with_no_body_on_disk_still_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            path = find_note_path(layout, note.id)
            fields, _body = parse_note_text(path.read_text(encoding="utf-8"))
            path.write_text(
                "---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n---\n",
                encoding="utf-8",
            )
            self.assertEqual(read_note(layout, note.id).body, "")

    def test_update_keeps_what_it_was_not_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(
                layout,
                page="wiki/concepts/x.md",
                body="第一版\n",
                anchor="前言",
                now=MOMENT,
            )
            later = datetime(2026, 10, 10, 20, 31, 5, tzinfo=timezone.utc)
            changed = update_note(layout, note.id, body="第二版\n", now=later)
            self.assertEqual(changed.body, "第二版\n")
            self.assertEqual(changed.anchor, "前言")
            self.assertEqual(changed.created, "2026-10-10T19:57:00.000000+00:00")
            self.assertEqual(changed.updated, "2026-10-10T20:31:05.000000+00:00")
            self.assertEqual(read_note(layout, note.id).body, "第二版\n")
            self.assertLess(changed.created, changed.updated)

    def test_update_refuses_an_empty_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            with self.assertRaises(ValueError):
                update_note(layout, note.id, body="  ")
            self.assertEqual(read_note(layout, note.id).body, "笔记\n")

    def test_update_writes_back_where_the_note_was_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            moved = layout.notes / "elsewhere" / f"{note.id}.md"
            moved.parent.mkdir(parents=True, exist_ok=True)
            find_note_path(layout, note.id).rename(moved)
            update_note(layout, note.id, body="改过\n")
            self.assertEqual(moved.read_text(encoding="utf-8").count("改过"), 1)
            self.assertEqual(len(list(layout.notes.rglob("*.md"))), 1)

    def test_delete_takes_the_note_and_its_empty_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            self.assertEqual(delete_note(layout, note.id), note.id)
            self.assertFalse((layout.notes / "concepts").exists())
            self.assertTrue(layout.notes.is_dir())
            with self.assertRaises(Refusal) as caught:
                read_note(layout, note.id)
            self.assertEqual(caught.exception.code, "note.missing")

    def test_delete_leaves_a_folder_that_still_holds_something(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            first = create_note(layout, page="wiki/concepts/x.md", body="一\n", now=MOMENT)
            second = create_note(
                layout,
                page="wiki/concepts/x.md",
                body="二\n",
                now=datetime(2026, 10, 10, 19, 58, 0, tzinfo=timezone.utc),
            )
            delete_note(layout, first.id)
            self.assertTrue(find_note_path(layout, second.id).is_file())

    def test_a_page_lists_its_own_notes_and_not_a_nested_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            outer = create_note(layout, page="wiki/concepts/x.md", body="外\n", now=MOMENT)
            inner = create_note(
                layout,
                page="wiki/concepts/x/inner.md",
                body="内\n",
                now=datetime(2026, 10, 10, 19, 58, 0, tzinfo=timezone.utc),
            )
            mine = [note.id for note in list_notes(layout, "wiki/concepts/x.md")]
            self.assertEqual(mine, [outer.id])
            self.assertEqual(len(list_notes(layout)), 2)
            self.assertEqual(
                [note.id for note in list_notes(layout, "wiki/concepts/x/inner.md")],
                [inner.id],
            )

    def test_notes_come_back_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            first = create_note(
                layout,
                page="wiki/concepts/x.md",
                body="一\n",
                now=datetime(2026, 10, 10, 19, 57, 0, tzinfo=timezone.utc),
            )
            second = create_note(
                layout,
                page="wiki/concepts/x.md",
                body="二\n",
                now=datetime(2026, 10, 10, 19, 57, 1, tzinfo=timezone.utc),
            )
            self.assertEqual([note.id for note in list_notes(layout)], [first.id, second.id])

    def test_missing_note_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            for reader in (read_note, delete_note):
                with self.subTest(reader=reader.__name__):
                    with self.assertRaises(Refusal) as caught:
                        reader(layout, "20261010-195700-ab12")
                    self.assertEqual(caught.exception.code, "note.missing")
                    self.assertEqual(caught.exception.status, 404)
            self.assertEqual(find_note_path(layout, "20261010-195700-ab12"), None)

    def test_listing_before_anything_exists_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            self.assertFalse(layout.notes.exists())
            self.assertEqual(list_notes(layout), [])
            self.assertEqual(list_notes(layout, "wiki/concepts/x.md"), [])

    def test_a_name_already_taken_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            taken = "20261010-195700-ab12"
            create_note(layout, page="wiki/concepts/x.md", body="一\n", note_id=taken)
            with self.assertRaises(ValueError):
                create_note(layout, page="wiki/concepts/x.md", body="二\n", note_id=taken)

    def test_the_wire_shape_holds_everything_a_reader_needs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout, _page = self.project(tmp)
            note = create_note(layout, page="wiki/concepts/x.md", body="笔记\n", now=MOMENT)
            payload = note_to_dict(note)
            self.assertEqual(
                sorted(payload),
                ["anchor", "body", "created", "id", "page", "quote", "updated"],
            )


if __name__ == "__main__":
    unittest.main()
