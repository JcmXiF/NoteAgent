from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from noteagent.ingest.clip_ledger import (
    is_clip_ledger_path,
    ledger_title,
    merge_clip_ledger,
    parse_block_ids,
    read_clip_ledger,
    render_clip_ledger_body,
    render_clip_ledger_page,
    union_collections,
)
from noteagent.ingest.clips import ClipCollection
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.write_file_blocks import write_file_blocks
from noteagent.project.init import init_project

BULLET_LEDGER = (
    "---\n"
    "type: source\n"
    "title: 信息熵 clips\n"
    "created: 2026-09-04\n"
    "updated: 2026-09-04\n"
    "tags: [clips]\n"
    "related: []\n"
    'sources: ["raw/web/entropy.html#b4", "raw/web/entropy.html#b5"]\n'
    "---\n"
    "\n"
    "# 信息熵\n"
    "\n"
    "## Collections\n"
    "\n"
    "- 定义 (`7da90415-be75-45d6-935c-f46c9b4961bd`): "
    "`raw/web/entropy.html` (b4, b5, b6)\n"
    "\n"
    "## Block locators\n"
    "\n"
    "- `raw/web/entropy.html#b4`\n"
    "- `raw/web/entropy.html#b5`\n"
)

TABLE_LEDGER = (
    "---\n"
    "type: source\n"
    "title: 联合熵 clips\n"
    "created: 2026-09-14\n"
    "updated: 2026-09-16\n"
    "tags: [clips]\n"
    "related: []\n"
    "sources: []\n"
    "---\n"
    "\n"
    "## Collections\n"
    "\n"
    "| 源文档 | Clip 集合 | 集合 ID | 区块 | 记录于 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| `raw/web/joint.html` | 联合熵 | "
    "`f57a159c-060a-4672-90a0-45c5b5ffc27a` | b2, b3, b5 | 磁盘版本 |\n"
    "| `raw/web/paper.pdf` | Elements | "
    "`ds-ad2736f8-4` | p1, p2 | 新生成版本 |\n"
    "\n"
    "## Block locators\n"
    "\n"
    "- `raw/web/joint.html#b2`\n"
    "- `raw/web/joint.html#b3`\n"
)


class ClipLedgerPathTests(unittest.TestCase):
    def test_recognises_wiki_sources_ledgers(self) -> None:
        self.assertTrue(is_clip_ledger_path("wiki/sources/信息熵-clips.md"))
        self.assertTrue(is_clip_ledger_path("wiki\\sources\\熵-CLIPS.MD"))
        self.assertFalse(is_clip_ledger_path("wiki/sources/信息熵.md"))
        self.assertFalse(is_clip_ledger_path("wiki/concepts/信息熵-clips.md"))

    def test_title_drops_the_clips_marker(self) -> None:
        self.assertEqual(ledger_title("信息熵 clips"), "信息熵")
        self.assertEqual(ledger_title('"KL散度/相对熵 clips"'), "KL散度/相对熵")
        self.assertEqual(ledger_title(""), "clip session")


class ClipLedgerReadTests(unittest.TestCase):
    def test_reads_the_bullet_shape(self) -> None:
        ledger = read_clip_ledger(BULLET_LEDGER)
        self.assertEqual(
            [item.id for item in ledger.collections],
            ["7da90415-be75-45d6-935c-f46c9b4961bd"],
        )
        self.assertEqual(ledger.collections[0].block_ids, ("b4", "b5", "b6"))
        # The locator rows repeat what the frontmatter already lists.
        self.assertEqual(
            ledger.locators,
            (
                "raw/web/entropy.html#b4",
                "raw/web/entropy.html#b5",
            ),
        )

    def test_reads_the_table_shape_including_page_blocks(self) -> None:
        ledger = read_clip_ledger(TABLE_LEDGER)
        by_id = {item.id: item for item in ledger.collections}
        self.assertEqual(
            set(by_id),
            {"f57a159c-060a-4672-90a0-45c5b5ffc27a", "ds-ad2736f8-4"},
        )
        self.assertEqual(
            by_id["ds-ad2736f8-4"].block_ids,
            ("p1", "p2"),
        )
        self.assertEqual(
            ledger.locators,
            ("raw/web/joint.html#b2", "raw/web/joint.html#b3"),
        )

    def test_locators_neither_from_frontmatter_nor_bullets_are_dropped(self) -> None:
        text = (
            '---\ntype: source\ntitle: x clips\nsources: ["raw/a.html#b1"]\n---\n'
            "\n## Block locators\n\n"
            "- `raw/b.html#b2`\n"
            "- `preview:https://example.com`\n"
            "- (none)\n"
        )
        ledger = read_clip_ledger(text)
        self.assertEqual(
            ledger.locators,
            ("raw/a.html#b1", "raw/b.html#b2"),
        )

    def test_block_ids_expand_ranges(self) -> None:
        self.assertEqual(parse_block_ids("b2, b3"), ("b2", "b3"))
        self.assertEqual(parse_block_ids("b3–b5"), ("b3", "b4", "b5"))
        self.assertEqual(parse_block_ids("p1, p2"), ("p1", "p2"))


class ClipLedgerRenderTests(unittest.TestCase):
    def test_rendered_page_reads_back_the_same_rows(self) -> None:
        collections = (
            ClipCollection(
                id="f57a159c-060a-4672-90a0-45c5b5ffc27a",
                source_key="raw/web/joint.html",
                source_label="联合熵",
                block_ids=("b2", "b3"),
                blocks=(),
            ),
        )
        locators = ["raw/web/joint.html#b2", "raw/web/other.html#b7"]
        text = render_clip_ledger_page(
            title="联合熵",
            collections=collections,
            locators=locators,
            today=date(2026, 1, 1),
        )
        self.assertIn("title: 联合熵 clips\n", text)
        self.assertIn("created: 2026-01-01\n", text)
        again = read_clip_ledger(text)
        self.assertEqual([item.id for item in again.collections], [collections[0].id])
        self.assertEqual(again.collections[0].block_ids, ("b2", "b3"))
        self.assertEqual(again.locators, tuple(locators))

    def test_empty_sections_still_render(self) -> None:
        body = render_clip_ledger_body(title="x", collections=(), locators=())
        self.assertIn("## Collections\n\n- (none)\n", body)
        self.assertIn("## Block locators\n\n- (none)\n", body)


class ClipLedgerMergeTests(unittest.TestCase):
    def test_union_keeps_first_seen_order_and_merges_block_ids(self) -> None:
        old = ClipCollection(
            id="a", source_key="raw/web/x.html", source_label="x",
            block_ids=("b1",), blocks=(),
        )
        new = ClipCollection(
            id="a", source_key="raw/web/x.html", source_label="x",
            block_ids=("b1", "b2"), blocks=(),
        )
        other = ClipCollection(
            id="b", source_key="raw/web/y.html", source_label="y",
            block_ids=("b3",), blocks=(),
        )
        merged = union_collections((old,), (new, other))
        self.assertEqual([item.id for item in merged], ["a", "b"])
        self.assertEqual(merged[0].block_ids, ("b1", "b2"))
        self.assertEqual(merged[1].block_ids, ("b3",))

    def test_merge_unions_locators_and_keeps_locked_frontmatter(self) -> None:
        result = merge_clip_ledger(
            BULLET_LEDGER,
            TABLE_LEDGER,
            today=lambda: "2026-06-01",
        )
        self.assertEqual(result.mode, "ledger-merge")
        self.assertIn("created: 2026-09-04", result.content)
        self.assertIn("title: 信息熵 clips", result.content)
        self.assertIn("updated: 2026-06-01", result.content)
        ledger = read_clip_ledger(result.content)
        self.assertEqual(
            [item.id for item in ledger.collections],
            [
                "7da90415-be75-45d6-935c-f46c9b4961bd",
                "f57a159c-060a-4672-90a0-45c5b5ffc27a",
                "ds-ad2736f8-4",
            ],
        )
        self.assertEqual(
            ledger.locators,
            (
                "raw/web/entropy.html#b4",
                "raw/web/entropy.html#b5",
                "raw/web/joint.html#b2",
                "raw/web/joint.html#b3",
            ),
        )

    def test_merging_the_same_ledger_again_changes_nothing(self) -> None:
        once = merge_clip_ledger(
            BULLET_LEDGER,
            TABLE_LEDGER,
            today=lambda: "2026-06-01",
        )
        twice = merge_clip_ledger(
            once.content,
            once.content,
            today=lambda: "2026-06-01",
        )
        self.assertEqual(twice.content, once.content)


class ClipLedgerWriteTests(unittest.TestCase):
    def _layout(self, tmp: str):
        return init_project(Path(tmp) / "proj")

    def test_write_path_unions_without_calling_the_merger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(tmp)
            target = layout.wiki / "sources" / "probe-clips.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(BULLET_LEDGER, encoding="utf-8")

            def explode(*_args, **_kwargs):
                raise AssertionError("a ledger must not reach the merge model")

            result = write_file_blocks(
                layout,
                [FileBlock(path="wiki/sources/probe-clips.md", content=TABLE_LEDGER)],
                merger=explode,
                today=lambda: "2026-06-01",
            )
            self.assertEqual(result.warnings, ())
            self.assertEqual(result.written[0].mode, "ledger-merge")
            ledger = read_clip_ledger(target.read_text(encoding="utf-8"))
            self.assertEqual(
                [item.id for item in ledger.collections],
                [
                    "7da90415-be75-45d6-935c-f46c9b4961bd",
                    "f57a159c-060a-4672-90a0-45c5b5ffc27a",
                    "ds-ad2736f8-4",
                ],
            )

    def test_a_ledger_created_by_the_writer_is_already_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._layout(tmp)
            page = render_clip_ledger_page(
                title="联合熵",
                collections=(),
                locators=["raw/web/joint.html#b2"],
                today=date(2026, 3, 1),
            )
            write_file_blocks(
                layout,
                [FileBlock(path="wiki/sources/联合熵-clips.md", content=page)],
            )
            on_disk = (layout.wiki / "sources" / "联合熵-clips.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("type:", on_disk)
            self.assertNotIn("tags:", on_disk)
            self.assertNotIn("related:", on_disk)
            again = render_clip_ledger_page(
                title="联合熵",
                collections=(),
                locators=["raw/web/joint.html#b2"],
                today=date(2026, 3, 1),
            )
            self.assertEqual(on_disk, again)


if __name__ == "__main__":
    unittest.main()
