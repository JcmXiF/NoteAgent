from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.update_index import (
    section_for_page,
    update_wiki_index,
    wiki_slug_for_path,
)
from noteagent.project.init import init_project


class UpdateIndexTests(unittest.TestCase):
    def test_slug_and_section(self) -> None:
        self.assertEqual(
            wiki_slug_for_path("wiki/concepts/贝尔曼方程.md"),
            "concepts/贝尔曼方程",
        )
        # A retired folder keeps its name in the slug and joins the notes.
        self.assertEqual(section_for_page("wiki/concepts/a.md"), "Notes")
        self.assertEqual(section_for_page("wiki/sources/a.md"), "Sources")
        self.assertEqual(section_for_page("wiki/custom/x.md"), "Notes")
        self.assertEqual(section_for_page("wiki/entropy.md"), "Notes")

    def test_appends_under_two_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            pages = [
                (
                    "wiki/concepts/bellman.md",
                    "---\ntype: concept\ntitle: Bellman\n---\n\n# Bellman\n",
                ),
                (
                    "wiki/sources/src.md",
                    "---\ntype: source\ntitle: Src\n---\n\n# Src\n",
                ),
                (
                    "wiki/log.md",
                    "## log entry\n",
                ),
            ]
            result = update_wiki_index(layout, pages)
            self.assertEqual(len(result.added), 2)
            text = layout.wiki_index.read_text(encoding="utf-8")
            self.assertIn("- [[concepts/bellman]] — Bellman", text)
            self.assertIn("- [[sources/src]] — Src", text)
            # Source pages and notes land under their own headings.
            sources_at = text.index("## Sources")
            notes_at = text.index("## Notes")
            self.assertGreater(text.index("[[sources/src]]"), sources_at)
            self.assertLess(text.index("[[sources/src]]"), notes_at)
            self.assertGreater(text.index("[[concepts/bellman]]"), notes_at)
            self.assertNotIn("## Concepts", text)

    def test_dedupes_existing_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = (
                "wiki/concepts/bellman.md",
                "---\ntype: concept\ntitle: Bellman\n---\n\n# Bellman\n",
            )
            first = update_wiki_index(layout, [page])
            second = update_wiki_index(layout, [page])
            self.assertEqual(len(first.added), 1)
            self.assertEqual(len(second.added), 0)
            self.assertTrue(any("already indexed" in s for s in second.skipped))
            text = layout.wiki_index.read_text(encoding="utf-8")
            self.assertEqual(text.count("[[concepts/bellman]]"), 1)

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            before = layout.wiki_index.read_text(encoding="utf-8")
            result = update_wiki_index(
                layout,
                [
                    (
                        "wiki/concepts/x.md",
                        "---\ntype: concept\ntitle: X\n---\n\n# X\n",
                    )
                ],
                dry_run=True,
            )
            self.assertEqual(len(result.added), 1)
            self.assertTrue(result.dry_run)
            self.assertEqual(layout.wiki_index.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
