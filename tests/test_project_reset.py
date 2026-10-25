from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.compile_cache import record_ingest
from noteagent.ingest.update_index import rebuild_wiki_index, update_wiki_index
from noteagent.project.init import init_project
from noteagent.project.reset import reset_wiki_content
from noteagent.util.run_log import RunLogger, run_log_path


class ProjectResetTests(unittest.TestCase):
    def test_reset_wipes_pages_restores_aggregates_clears_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            concept = layout.wiki / "concepts" / "ghost.md"
            concept.parent.mkdir(parents=True, exist_ok=True)
            concept.write_text(
                "---\ntype: concept\ntitle: Ghost\n---\n\n# Ghost\n",
                encoding="utf-8",
            )
            layout.wiki_index.write_text(
                "# Wiki Index\n\n## Concepts\n\n- [[concepts/ghost]] — Ghost\n",
                encoding="utf-8",
            )
            layout.wiki_log.write_text("## old log\n", encoding="utf-8")
            record_ingest(layout, "id:1", "source body abc", ("wiki/concepts/ghost.md",))
            self.assertTrue(layout.compile_cache.is_file())

            raw = layout.raw / "keep.md"
            raw.write_text("# keep\n", encoding="utf-8")

            result = reset_wiki_content(layout, clear_cache=True)
            self.assertFalse(result.dry_run)
            self.assertTrue(result.cleared_cache)
            self.assertFalse(concept.exists())
            self.assertFalse(layout.compile_cache.is_file())
            self.assertTrue(raw.is_file())
            self.assertTrue(layout.wiki_index.is_file())
            index = layout.wiki_index.read_text(encoding="utf-8")
            self.assertNotIn("[[concepts/ghost]]", index)
            self.assertIn("# Wiki Index", index)
            self.assertFalse((layout.wiki / "concepts").exists())
            self.assertFalse((layout.wiki / "sources").exists())

    def test_reset_dry_run_leaves_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            concept = layout.wiki / "concepts" / "x.md"
            concept.parent.mkdir(parents=True, exist_ok=True)
            concept.write_text("# x\n", encoding="utf-8")
            before_index = layout.wiki_index.read_text(encoding="utf-8")
            result = reset_wiki_content(layout, dry_run=True)
            self.assertTrue(result.dry_run)
            self.assertTrue(concept.is_file())
            self.assertEqual(layout.wiki_index.read_text(encoding="utf-8"), before_index)

    def test_reset_optional_run_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            logger = RunLogger.for_layout(layout)
            logger.append("test", ok=True)
            self.assertTrue(run_log_path(layout).is_file())
            reset_wiki_content(layout, clear_run_log=False)
            self.assertTrue(run_log_path(layout).is_file())
            reset_wiki_content(layout, clear_run_log=True)
            self.assertFalse(run_log_path(layout).is_file())

    def test_rebuild_index_drops_ghosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = (
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n",
            )
            update_wiki_index(layout, [page])
            # Write the page, then leave an index entry with nothing behind it.
            (layout.wiki / "concepts").mkdir(parents=True, exist_ok=True)
            (layout.wiki / "concepts" / "alive.md").write_text(page[1], encoding="utf-8")
            layout.wiki_index.write_text(
                "# Wiki Index\n\n## Concepts\n\n"
                "- [[concepts/alive]] — Alive\n"
                "- [[concepts/ghost]] — Ghost\n",
                encoding="utf-8",
            )
            result = rebuild_wiki_index(layout)
            self.assertEqual(len(result.added), 1)
            text = layout.wiki_index.read_text(encoding="utf-8")
            self.assertIn("[[concepts/alive]]", text)
            self.assertNotIn("[[concepts/ghost]]", text)


if __name__ == "__main__":
    unittest.main()
