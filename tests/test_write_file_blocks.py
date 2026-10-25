from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.parse_file_blocks import FileBlock, parse_file_blocks
from noteagent.ingest.page_merge import merged_frontmatter
from noteagent.ingest.wiki_paths import resolve_wiki_write_path
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    write_file_blocks,
)
from noteagent.ingest.auto_ingest import hard_write_failure
from noteagent.project.layout import default_layout
from noteagent.project.init import init_project

FIXTURE = Path(__file__).parent / "fixtures" / "sample_file_blocks.txt"


class WriteFileBlocksTests(unittest.TestCase):
    def test_writes_safe_blocks_into_temp_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            text = FIXTURE.read_text(encoding="utf-8")
            parsed = parse_file_blocks(text)
            result = write_file_blocks(layout, parsed.blocks)

            self.assertEqual(len(result.written), 2)
            self.assertEqual(result.warnings, ())

            bellman = root / "wiki" / "concepts" / "bellman-equation.md"
            value = root / "wiki" / "concepts" / "value-function.md"
            self.assertTrue(bellman.is_file())
            self.assertTrue(value.is_file())
            self.assertIn("贝尔曼方程", bellman.read_text(encoding="utf-8"))
            self.assertIn("价值函数", value.read_text(encoding="utf-8"))

    def test_log_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            first = FileBlock(
                path="wiki/log.md",
                content="## 2026-07-20\n\n- first entry",
            )
            second = FileBlock(
                path="wiki/log.md",
                content="## 2026-07-21\n\n- second entry",
            )
            write_file_blocks(layout, [first])
            write_file_blocks(layout, [second])
            text = layout.wiki_log.read_text(encoding="utf-8")
            self.assertIn("first entry", text)
            self.assertIn("second entry", text)

    def test_resolve_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = default_layout(tmp)
            (layout.root / "wiki").mkdir(parents=True, exist_ok=True)
            with self.assertRaises(ValueError):
                resolve_wiki_write_path(layout, "../secret.md")

    def test_dry_run_does_not_touch_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            block = FileBlock(path="wiki/concepts/demo.md", content="# Demo\n")
            result = write_file_blocks(layout, [block], dry_run=True)
            self.assertEqual(len(result.written), 1)
            self.assertTrue(result.written[0].mode.startswith("dry-run:"))
            self.assertFalse((root / "wiki" / "concepts" / "demo.md").exists())

    def test_merged_frontmatter_lists(self) -> None:
        existing = (
            "---\n"
            "type: concept\n"
            "title: Old Title\n"
            "created: 2026-01-01\n"
            "updated: 2026-01-01\n"
            "tags: [a]\n"
            "related: [x]\n"
            'sources: ["raw/one.md"]\n'
            "---\n"
            "\n# Old Body\n"
        )
        incoming = (
            "---\n"
            "type: concept\n"
            "title: New Title\n"
            "created: 2026-07-21\n"
            "updated: 2026-07-21\n"
            "tags: [b]\n"
            "related: [y]\n"
            'sources: ["raw/two.md"]\n'
            "---\n"
            "\n# New Body\n"
        )
        merged = merged_frontmatter(existing, incoming)
        self.assertIn("title: Old Title", merged)
        self.assertIn("created: 2026-01-01", merged)
        self.assertIn("updated: 2026-07-21", merged)
        self.assertIn("tags: [b]", merged)
        self.assertIn("related: [y]", merged)
        self.assertIn("raw/one.md", merged)
        self.assertIn("raw/two.md", merged)
        self.assertIn("# New Body", merged)
        self.assertNotIn("# Old Body", merged)

    def test_write_merge_mode_uses_merger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            first = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "---\n"
                    "type: concept\n"
                    "title: Demo\n"
                    "created: 2026-01-01\n"
                    "tags: [a]\n"
                    'sources: ["raw/one.md"]\n'
                    "---\n"
                    "\n# Demo from one\n\n" + ("alpha " * 30) + "\n"
                ),
            )
            second = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "---\n"
                    "type: concept\n"
                    "title: Renamed\n"
                    "created: 2026-07-21\n"
                    "tags: [b]\n"
                    'sources: ["raw/two.md"]\n'
                    "---\n"
                    "\n# Demo from two\n\n" + ("beta " * 30) + "\n"
                ),
            )

            def merger(
                existing: str, incoming: str, identity: str
            ) -> str:
                self.assertEqual(identity, "raw/two.md")
                return (
                    "---\n"
                    "type: concept\n"
                    "title: LLM\n"
                    "created: 2099-01-01\n"
                    "tags: [z]\n"
                    'sources: ["raw/two.md"]\n'
                    "---\n"
                    "\n# Demo merged\n\n"
                    "from one and two\n\n" + ("gamma " * 40) + "\n"
                )

            write_file_blocks(
                layout, [first], source_identity="raw/one.md"
            )
            result = write_file_blocks(
                layout,
                [second],
                source_identity="raw/two.md",
                merger=merger,
                today=lambda: "2026-08-10",
            )
            self.assertEqual(result.written[0].mode, "merge")
            text = (root / "wiki" / "concepts" / "demo.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("title: Demo", text)
            self.assertIn("created: 2026-01-01", text)
            self.assertIn("tags: [b]", text)
            self.assertNotIn("tags: [b, z]", text)
            self.assertIn("Demo merged", text)
            self.assertIn("raw/one.md", text)
            self.assertIn("raw/two.md", text)

    def test_write_replace_mode_single_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            first = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "---\n"
                    "type: concept\n"
                    "title: Demo\n"
                    "created: 2026-01-01\n"
                    'sources: ["raw/one.md"]\n'
                    "---\n"
                    "\n# Obsolete\n"
                ),
            )
            second = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "---\n"
                    "type: concept\n"
                    "title: Renamed\n"
                    "created: 2026-07-21\n"
                    'sources: ["raw/one.md"]\n'
                    "---\n"
                    "\n# Corrected\n"
                ),
            )
            write_file_blocks(
                layout, [first], source_identity="raw/one.md"
            )
            result = write_file_blocks(
                layout,
                [second],
                source_identity="raw/one.md",
                merger=lambda *_: "should-not-run",
                today=lambda: "2026-08-10",
            )
            self.assertEqual(result.written[0].mode, "replace")
            text = (root / "wiki" / "concepts" / "demo.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Corrected", text)
            self.assertNotIn("Obsolete", text)
            self.assertIn("title: Demo", text)

    def _locator_page(self, sources: str, body: str) -> FileBlock:
        return FileBlock(
            path="wiki/concepts/demo.md",
            content=(
                "---\n"
                "type: concept\n"
                "title: Demo\n"
                "created: 2026-01-01\n"
                f"sources: {sources}\n"
                "---\n"
                f"\n{body}"
            ),
        )

    def test_write_replaces_a_locator_page_this_round_fully_covers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            first = self._locator_page('["raw/x.html#b1"]', "# Old\n")
            second = self._locator_page(
                '["raw/x.html#b2"]', "# Rewritten From Both\n"
            )
            write_file_blocks(layout, [first])
            result = write_file_blocks(
                layout,
                [second],
                source_identity="raw/x.html",
                material={
                    second.path: {"raw/x.html#b1", "raw/x.html#b2"}
                },
                merger=lambda *_: "should-not-run",
                today=lambda: "2026-08-10",
            )
            self.assertEqual(result.written[0].mode, "replace")
            text = (root / "wiki" / "concepts" / "demo.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Rewritten From Both", text)
            self.assertIn("raw/x.html#b1", text)
            self.assertIn("raw/x.html#b2", text)

    def test_write_merges_when_a_claimed_block_is_not_in_this_round(self) -> None:
        # The page says it came from #b1; this round only carries #b2. Replacing
        # would leave the page claiming prose it no longer contains, so the old
        # body stays and the new material goes through the merge path.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            first = self._locator_page('["raw/x.html#b1"]', "# Original\n")
            second = self._locator_page('["raw/x.html#b2"]', "# Newer\n")
            write_file_blocks(layout, [first])
            result = write_file_blocks(
                layout,
                [second],
                source_identity="raw/x.html",
                material={second.path: {"raw/x.html#b2"}},
                today=lambda: "2026-08-10",
            )
            self.assertEqual(result.written[0].mode, "merge-fallback")
            text = (root / "wiki" / "concepts" / "demo.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Original", text)
            self.assertNotIn("#b2", text)

    def test_repairs_missing_opening_frontmatter_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            block = FileBlock(
                path="wiki/concepts/projection.md",
                content=(
                    "type: concept\n"
                    "title: Projection\n"
                    'sources: ["raw/x.md"]\n'
                    "---\n\n# Projection\n"
                ),
            )
            result = write_file_blocks(layout, [block])
            self.assertEqual(len(result.written), 1)
            self.assertTrue(any(w.code == "repaired" for w in result.warnings))
            text = (root / "wiki" / "concepts" / "projection.md").read_text(
                encoding="utf-8"
            )
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("type: concept", text)

    def test_strips_leftover_file_wrapper_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            block = FileBlock(
                path="wiki/concepts/entropy.md",
                content=(
                    "---FILE: wiki/concepts/entropy.md\n"
                    "---\n"
                    "type: concept\n"
                    "title: Entropy\n"
                    'sources: ["raw/web/ex.html#b1"]\n'
                    "---\n\n# Entropy\n"
                ),
            )
            result = write_file_blocks(layout, [block])
            self.assertEqual(len(result.written), 1)
            self.assertTrue(any(w.code == "repaired" for w in result.warnings))
            text = (root / "wiki" / "concepts" / "entropy.md").read_text(
                encoding="utf-8"
            )
            self.assertTrue(text.startswith("---\n"))
            self.assertNotIn("---FILE:", text)
            self.assertIn("title: Entropy", text)
            self.assertIn("raw/web/ex.html#b1", text)

    def test_log_strips_embedded_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            dirty = FileBlock(
                path="wiki/log.md",
                content=(
                    "---\n"
                    "type: log\n"
                    "title: polluted\n"
                    "---\n\n"
                    "## [2026-08-12] ingest | demo\n"
                    "- real entry\n"
                ),
            )
            write_file_blocks(layout, [dirty])
            text = layout.wiki_log.read_text(encoding="utf-8")
            self.assertIn("real entry", text)
            self.assertNotIn("type: log", text)
            self.assertNotIn("title: polluted", text)

    def _refused_setup(self, tmp: str):
        root = Path(tmp) / "proj"
        layout = init_project(root)
        old = (
            "---\ntype: concept\ntitle: Entropy\ncreated: 2026-01-01\n"
            'updated: 2026-01-01\nsources: ["raw/one.md"]\n---\n\n'
            "Entropy measures how uncertain a distribution is.\n"
        )
        page = root / "wiki" / "entropy.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(old, encoding="utf-8")
        block = FileBlock(
            path="wiki/entropy.md",
            content=(
                "---\ntype: concept\ntitle: Entropy\ncreated: 2026-01-01\n"
                'updated: 2026-02-02\nsources: ["raw/two.md"]\n---\n\n'
                "Entropy is the average information.\n"
            ),
        )
        return root, layout, page, block

    def test_a_refused_page_is_written_when_the_repair_passes(self) -> None:
        good = (
            "---\ntype: concept\ntitle: Entropy\ncreated: 2026-01-01\n"
            'updated: 2026-02-02\nsources: ["raw/two.md"]\n---\n\n'
            "Entropy measures how uncertain a distribution is, and is the average\n"
            "information.\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root, layout, page, block = self._refused_setup(tmp)
            result = write_file_blocks(
                layout,
                [block],
                source_identity="raw/two.md",
                merger=lambda *_: "body with no frontmatter\n",
                repair=lambda *_: good,
                today=lambda: "2026-02-02",
            )
            text = page.read_text(encoding="utf-8")
            self.assertEqual(result.written[0].mode, "repair")
            self.assertEqual(
                [w for w in result.warnings if w.code == "repair_failed"], []
            )
            # The program settles the frontmatter; the body is the repair's.
            self.assertIn("average\ninformation.", text)
            self.assertIn("raw/one.md", text)
            self.assertIn("raw/two.md", text)

    def test_a_page_refused_twice_is_a_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, layout, page, block = self._refused_setup(tmp)
            before = page.read_text(encoding="utf-8")
            result = write_file_blocks(
                layout,
                [block],
                source_identity="raw/two.md",
                merger=lambda *_: "body with no frontmatter\n",
                repair=lambda *_: "still no frontmatter\n",
                today=lambda: "2026-02-02",
            )
            self.assertEqual(result.written[0].mode, "repair-fallback")
            codes = [w.code for w in result.warnings]
            self.assertIn("repair_failed", codes)
            self.assertTrue(HARD_WRITE_CODES & {"repair_failed"})
            self.assertTrue(
                any(hard_write_failure(w) for w in result.warnings)
            )
            self.assertEqual(page.read_text(encoding="utf-8"), before)

    def test_a_page_refused_once_without_a_repair_stays_soft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, layout, page, block = self._refused_setup(tmp)
            result = write_file_blocks(
                layout,
                [block],
                source_identity="raw/two.md",
                merger=lambda *_: "body with no frontmatter\n",
                today=lambda: "2026-02-02",
            )
            self.assertEqual(result.written[0].mode, "merge-fallback")
            self.assertEqual(
                [w.code for w in result.warnings], ["merge_fallback"]
            )
            self.assertFalse(
                any(hard_write_failure(w) for w in result.warnings)
            )


class WriteFileBlocksLineEndingsTests(unittest.TestCase):
    """One line ending on disk: the project's, not the platform's."""

    def test_a_new_page_lands_as_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            block = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "---\n"
                    "type: concept\n"
                    "title: Demo\n"
                    'sources: ["raw/a.md"]\n'
                    "---\n"
                    "\n# Demo\r\n"
                    "\r\n"
                    "Body.\r\n"
                ),
            )
            result = write_file_blocks(layout, [block])

            page = layout.wiki / "concepts" / "demo.md"
            raw = page.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertIn("# Demo".encode("utf-8"), raw)
            # The count is the file's, so nothing has to guess what a text mode
            # would have translated.
            self.assertEqual(result.written[0].bytes_written, len(raw))

    def test_a_byte_order_mark_stays_off_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            block = FileBlock(
                path="wiki/concepts/demo.md",
                content=(
                    "\ufeff---\n"
                    "type: concept\n"
                    "title: Demo\n"
                    'sources: ["raw/a.md"]\n'
                    "---\n\n# Demo\n"
                ),
            )
            write_file_blocks(layout, [block])

            raw = (layout.wiki / "concepts" / "demo.md").read_bytes()
            self.assertFalse(raw.startswith("\ufeff".encode("utf-8")))
            self.assertTrue(raw.startswith(b"---\n"))

    def test_a_crlf_page_is_folded_when_written_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = layout.wiki / "concepts" / "demo.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            with page.open("w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "---\ntype: concept\ntitle: Demo\n"
                    'sources: ["raw/a.md"]\n'
                    "---\n\n# Old\r\n\r\nOld body.\r\n"
                )
            self.assertIn(b"\r", page.read_bytes())

            merged = (
                "---\ntype: concept\ntitle: Demo\n"
                'sources: ["raw/a.md", "raw/b.md"]\n'
                "---\n\n# New\n\nNew body.\n"
            )
            result = write_file_blocks(
                layout,
                [FileBlock(path="wiki/concepts/demo.md", content=merged)],
                merger=lambda *_: merged,
            )

            raw = page.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(result.written[0].mode, "merge")
            # Rewritten, not merely read: the union of both sources is on the page.
            self.assertIn(b"raw/b.md", raw)
            self.assertEqual(result.written[0].bytes_written, len(raw))

    def test_a_folded_crlf_page_keeps_the_sources_it_was_written_from(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = layout.wiki / "concepts" / "demo.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            with page.open("w", encoding="utf-8", newline="") as handle:
                handle.write(
                    "---\ntype: concept\ntitle: Demo\n"
                    'sources: ["raw/a.md"]\n'
                    "---\n\n# Old\r\n\r\nOld body.\r\n"
                )
            self.assertIn(b"\r", page.read_bytes())

            result = write_file_blocks(
                layout,
                [
                    FileBlock(
                        path="wiki/concepts/demo.md",
                        content=(
                            "---\ntype: concept\ntitle: Demo\n"
                            'sources: ["raw/a.md", "raw/b.md"]\n'
                            "---\n\n# New\n\nNew body.\n"
                        ),
                    )
                ],
                merger=None,
            )

            raw = page.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(result.written[0].mode, "merge-fallback")
            # Nothing was absorbed, so the page does not claim the new locator.
            self.assertNotIn(b"raw/b.md", raw)
            self.assertIn(b"raw/a.md", raw)

    def test_a_log_entry_lands_as_lf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            block = FileBlock(
                path="wiki/log.md",
                content="## [2026-08-12] ingest | demo\r\n\r\n- entry\r\n",
            )
            result = write_file_blocks(layout, [block])

            raw = layout.wiki_log.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertIn(b"- entry", raw)
            # An append reports the entry it added, not the whole file.
            entry = "## [2026-08-12] ingest | demo\n\n- entry"
            self.assertEqual(
                result.written[0].bytes_written, len(entry.encode("utf-8"))
            )

    def _rewrite_setup(self, tmp: str):
        root = Path(tmp) / "proj"
        layout = init_project(root)
        page = root / "wiki" / "entropy.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        old = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-01-01\n"
            'sources: ["raw/one.md"]\n---\n\n'
            "熵是不确定性的度量。\n\n见 [[kl-divergence]]。\n"
        )
        page.write_text(old, encoding="utf-8")
        return layout, page, old

    def test_a_page_marked_rewritten_is_written_as_a_rewrite(self) -> None:
        new = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/two.md"]\n---\n\n'
            "熵是不确定性的度量，也是编码长度的下界。\n\n见 [[kl-divergence]]。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout, page, _ = self._rewrite_setup(tmp)

            result = write_file_blocks(
                layout,
                [FileBlock(path="wiki/entropy.md", content=new)],
                source_identity="raw/two.md",
                rewrite_drafts={"wiki/entropy.md": new},
                merger=lambda *_: self.fail("a rewrite must not merge"),
                today=lambda: "2026-02-02",
            )

            self.assertEqual(result.written[0].mode, "rewrite")
            text = page.read_text(encoding="utf-8")
            self.assertIn("编码长度的下界", text)
            self.assertIn("updated: 2026-02-02", text)
            self.assertNotIn("updated: 2026-01-01", text)

    def test_a_rewrite_keeps_the_marker_only_the_draft_carried(self) -> None:
        # The draft earned this marker from the new source. R4b must accept it,
        # or every clip that lands on an existing page is refused as invented.
        draft = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/two.md"]\n---\n\n'
            "<!-- na-clip:two -->\n"
            "## 联合熵\n\n"
            "熵是不确定性的度量，也是编码长度的下界。\n"
        )
        answer = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/one.md", "raw/two.md"]\n---\n\n'
            "熵是不确定性的度量，见 [[kl-divergence]]。\n\n"
            "<!-- na-clip:two -->\n"
            "## 联合熵\n\n"
            "熵是不确定性的度量，也是编码长度的下界。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout, page, _ = self._rewrite_setup(tmp)

            result = write_file_blocks(
                layout,
                [FileBlock(path="wiki/entropy.md", content=answer)],
                source_identity="raw/two.md",
                rewrite_drafts={"wiki/entropy.md": draft},
                merger=lambda *_: self.fail("a rewrite must not merge"),
                today=lambda: "2026-02-02",
            )

            self.assertEqual(result.written[0].mode, "rewrite")
            self.assertIn("na-clip:two", page.read_text(encoding="utf-8"))

    def test_a_rewrite_that_drops_the_draft_s_marker_is_not_written(self) -> None:
        draft = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/two.md"]\n---\n\n'
            "<!-- na-clip:two -->\n"
            "## 联合熵\n\n"
            "熵是不确定性的度量，也是编码长度的下界。\n"
        )
        answer = (
            "---\ntitle: 熵\ncreated: 2026-01-01\nupdated: 2026-02-02\n"
            'sources: ["raw/one.md", "raw/two.md"]\n---\n\n'
            "熵是不确定性的度量，见 [[kl-divergence]]。\n\n"
            "也是编码长度的下界。\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout, page, old = self._rewrite_setup(tmp)

            result = write_file_blocks(
                layout,
                [FileBlock(path="wiki/entropy.md", content=answer)],
                source_identity="raw/two.md",
                rewrite_drafts={"wiki/entropy.md": draft},
                merger=lambda *_: self.fail("a rewrite must not merge"),
                today=lambda: "2026-02-02",
            )

            self.assertEqual(result.written[0].mode, "merge-fallback")
            self.assertEqual(page.read_text(encoding="utf-8"), old)
            self.assertIn("na-clip:two", result.warnings[0].message)


if __name__ == "__main__":
    unittest.main()
