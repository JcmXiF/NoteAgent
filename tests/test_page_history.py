"""A page's own history: the versions its writers replaced, kept in the store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.page_history import (
    blob_digest,
    blob_path,
    keep_before,
    note_agent_write,
    page_snapshots,
    record_write,
)
from noteagent.project.init import init_project

PAGE = (
    "---\n"
    "title: 信息熵\n"
    "sources: ['raw/a.md#b1']\n"
    "---\n"
    "\n"
    "# 信息熵\n"
    "\n"
    "引言。\n"
    "\n"
    "<!-- na-clip:c-1 b1 -->\n"
    "## 定义\n"
    "\n"
    "旧的定义。\n"
)

PAGE_PATH = "wiki/concepts/信息熵.md"

NEWER = PAGE.replace("旧的定义。", "新的定义。")


def kept_text(layout, digest: str) -> str | None:
    """The text a version is kept under, or nothing when it was never kept."""
    path = blob_path(layout, digest)
    return path.read_text(encoding="utf-8") if path.is_file() else None


class SnapshotStoreTests(unittest.TestCase):
    def _project(self, tmp: str):
        return init_project(Path(tmp) / "demo")

    def test_a_text_is_kept_under_its_own_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            digest = keep_before(layout, PAGE)

            self.assertEqual(digest, blob_digest(PAGE))
            self.assertEqual(kept_text(layout, digest), PAGE)

    def test_the_same_text_is_only_kept_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            first = keep_before(layout, PAGE)
            before = blob_path(layout, first).stat().st_mtime_ns
            second = keep_before(layout, PAGE)

            self.assertEqual(first, second)
            self.assertEqual(blob_path(layout, first).stat().st_mtime_ns, before)

    def test_text_is_folded_to_lf_before_it_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            digest = keep_before(layout, PAGE.replace("\n", "\r\n"))

            self.assertEqual(digest, blob_digest(PAGE))
            self.assertEqual(kept_text(layout, digest), PAGE)

    def test_a_digest_that_is_not_one_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            with self.assertRaises(ValueError):
                blob_path(layout, "not-a-digest")


class RecordWriteTests(unittest.TestCase):
    def _project(self, tmp: str):
        return init_project(Path(tmp) / "demo")

    def test_a_write_notes_what_it_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            entry = record_write(
                layout,
                PAGE_PATH,
                before=keep_before(layout, PAGE),
                after_text=NEWER,
                mode="replace",
                by="agent",
            )
            assert entry is not None

            self.assertEqual(entry.path, PAGE_PATH)
            self.assertEqual(entry.by, "agent")
            self.assertEqual(entry.mode, "replace")
            self.assertEqual(kept_text(layout, entry.before), PAGE)
            self.assertEqual(kept_text(layout, entry.after), NEWER)
            self.assertEqual(entry.bytes_written, len(NEWER.encode("utf-8")))

    def test_a_write_that_changed_nothing_is_not_a_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            entry = record_write(
                layout,
                PAGE_PATH,
                before=keep_before(layout, PAGE),
                after_text=PAGE,
                mode="replace",
                by="agent",
            )

            self.assertIsNone(entry)
            self.assertEqual(page_snapshots(layout, PAGE_PATH), ())

    def test_the_pages_first_write_has_no_before(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            entry = note_agent_write(
                layout, PAGE_PATH, before="", after_text=PAGE, mode="create"
            )
            assert entry is not None

            self.assertEqual(entry.before, "")
            self.assertEqual(kept_text(layout, entry.after), PAGE)

    def test_the_pipeline_notes_its_own_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            entry = note_agent_write(
                layout,
                PAGE_PATH,
                before=keep_before(layout, PAGE),
                after_text=NEWER,
                mode="replace",
            )
            assert entry is not None

            self.assertEqual(entry.by, "agent")
            self.assertEqual(entry.mode, "replace")


class PageSnapshotsTests(unittest.TestCase):
    def _project(self, tmp: str):
        return init_project(Path(tmp) / "demo")

    def _write(
        self,
        layout,
        path: str,
        after_text: str,
        *,
        before: str = "",
        mode: str = "replace",
    ):
        return record_write(
            layout, path, before=before, after_text=after_text, mode=mode, by="agent"
        )

    def test_the_writes_to_one_page_come_back_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            first = self._write(
                layout, PAGE_PATH, NEWER, before=keep_before(layout, PAGE)
            )
            second = self._write(
                layout, PAGE_PATH, NEWER + "\n尾。\n", before=keep_before(layout, NEWER)
            )
            assert first is not None and second is not None

            items = page_snapshots(layout, PAGE_PATH)
            self.assertEqual([item.after for item in items], [second.after, first.after])

    def test_only_the_page_asked_for_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            self._write(layout, PAGE_PATH, NEWER, before=keep_before(layout, PAGE))
            self._write(layout, "wiki/concepts/别的.md", "别的页。", mode="create")

            items = page_snapshots(layout, PAGE_PATH)
            self.assertEqual([item.path for item in items], [PAGE_PATH])

    def test_the_limit_caps_how_many_come_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            previous = PAGE
            for step in range(3):
                following = f"{NEWER}\n第 {step} 步。\n"
                self._write(
                    layout, PAGE_PATH, following, before=keep_before(layout, previous)
                )
                previous = following

            self.assertEqual(len(page_snapshots(layout, PAGE_PATH, limit=2)), 2)
            self.assertEqual(len(page_snapshots(layout, PAGE_PATH)), 3)

    def test_a_page_no_one_wrote_has_no_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            self.assertEqual(page_snapshots(layout, "wiki/concepts/没有.md"), ())

    def test_a_line_that_cannot_be_read_does_not_lose_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._project(tmp)
            self._write(layout, PAGE_PATH, NEWER, before=keep_before(layout, PAGE))
            with layout.snapshot_log.open("a", encoding="utf-8") as handle:
                handle.write("{ this is not json\n")
            self._write(
                layout, PAGE_PATH, NEWER + "\n再。\n", before=keep_before(layout, NEWER)
            )

            items = page_snapshots(layout, PAGE_PATH)
            self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
