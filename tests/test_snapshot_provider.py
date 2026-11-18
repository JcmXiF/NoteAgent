"""The snapshot provider seam: put text, read a ref, restore with a base check."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.effects.provider import (
    PageHistoryProvider,
    SnapshotProvider,
)
from noteagent.ingest.page_history import page_snapshots, record_write
from noteagent.io.text_file import write_text
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal

PAGE = (
    "---\n"
    "title: 信息熵\n"
    "sources: ['raw/a.md#b1']\n"
    "---\n"
    "\n"
    "# 信息熵\n"
    "\n"
    "旧的定义。\n"
)

NEWER = PAGE.replace("旧的定义。", "新的定义。")
PAGE_PATH = "wiki/concepts/信息熵.md"


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.page = self.layout.root.joinpath(*PAGE_PATH.split("/"))
        self.page.parent.mkdir(parents=True, exist_ok=True)
        write_text(self.page, PAGE)
        self.provider = PageHistoryProvider(self.layout)

    def test_put_and_get_round_trip_the_text(self) -> None:
        ref = self.provider.put(PAGE_PATH, PAGE)

        self.assertEqual(len(ref), 64)
        self.assertEqual(self.provider.get(ref), PAGE)
        self.assertEqual(self.provider.current(PAGE_PATH), ref)

    def test_current_of_a_missing_page_is_empty(self) -> None:
        self.assertEqual(
            self.provider.current("wiki/concepts/没有.md"), ""
        )

    def test_current_refuses_a_path_that_is_not_a_wiki_page(self) -> None:
        with self.assertRaises(Refusal) as caught:
            self.provider.current("../outside.md")

        self.assertEqual(caught.exception.code, "page.notAPage")
        self.assertEqual(caught.exception.status, 400)

    def test_put_refuses_a_path_that_is_not_a_wiki_page(self) -> None:
        with self.assertRaises(Refusal) as caught:
            self.provider.put("raw/a.md", PAGE)

        self.assertEqual(caught.exception.code, "page.notAPage")

    def test_the_provider_satisfies_the_protocol(self) -> None:
        self.assertIsInstance(self.provider, SnapshotProvider)

    def prepare_version(self) -> str:
        before = self.provider.put(PAGE_PATH, PAGE)
        write_text(self.page, NEWER)
        record_write(
            self.layout,
            PAGE_PATH,
            before=before,
            after_text=NEWER,
            mode="edit",
            by="agent",
        )
        return before

    def test_restore_puts_an_earlier_version_back(self) -> None:
        before = self.prepare_version()
        current = self.provider.current(PAGE_PATH)
        self.assertNotEqual(current, before)

        restored = self.provider.restore(
            PAGE_PATH, before, base=current
        )

        self.assertEqual(restored, before)
        self.assertEqual(self.page.read_text(encoding="utf-8"), PAGE)
        self.assertEqual(
            page_snapshots(self.layout, PAGE_PATH)[0].mode, "restore"
        )

    def test_a_changed_base_is_refused_unless_forced(self) -> None:
        before = self.prepare_version()

        with self.assertRaises(Refusal) as caught:
            self.provider.restore(PAGE_PATH, before, base="stale")
        self.assertEqual(caught.exception.code, "page.changed")
        self.assertEqual(
            caught.exception.status, 409
        )
        self.assertEqual(self.page.read_text(encoding="utf-8"), NEWER)

        restored = self.provider.restore(
            PAGE_PATH, before, base="stale", force=True
        )

        self.assertEqual(restored, before)
        self.assertEqual(self.page.read_text(encoding="utf-8"), PAGE)

    def test_a_missing_ref_is_a_refusal(self) -> None:
        with self.assertRaises(Refusal) as caught:
            self.provider.get("f" * 64)

        self.assertEqual(caught.exception.code, "snapshot.missing")
        self.assertEqual(caught.exception.status, 404)

    def test_a_ref_that_is_not_a_digest_is_the_same_missing_refusal(self) -> None:
        with self.assertRaises(Refusal) as caught:
            self.provider.get("not-a-digest")

        self.assertEqual(caught.exception.code, "snapshot.missing")
        self.assertEqual(caught.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
