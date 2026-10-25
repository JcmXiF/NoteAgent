from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.acquire.adapters.pdf_source import derived_sidecar_path
from noteagent.api.files import delete_project_file
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    load_clip_store,
    save_clip_store,
)
from noteagent.project.init import init_project


class DeleteProjectFileTests(unittest.TestCase):
    def test_deletes_wiki_page_and_rebuilds_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            page = layout.wiki / "concepts" / "kl.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: KL\n---\n\n# KL\n\nbody\n",
                encoding="utf-8",
            )
            layout.wiki_index.write_text(
                "# Wiki Index\n\n## Concepts\n\n- [[concepts/kl]]\n",
                encoding="utf-8",
            )
            result = delete_project_file(layout, "wiki/concepts/kl.md")
            self.assertEqual(result["deleted"], "wiki/concepts/kl.md")
            self.assertFalse(page.is_file())
            index = layout.wiki_index.read_text(encoding="utf-8")
            self.assertNotIn("concepts/kl", index)

    def test_rejects_system_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            legacy = layout.legacy_overview
            legacy.write_text("# leftover\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                delete_project_file(layout, "wiki/overview.md")
            self.assertTrue(legacy.is_file())

    def test_deletes_raw_and_drops_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            raw = layout.raw / "web" / "example.com" / "kl.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("<p>kl</p>", encoding="utf-8")
            sidecar = derived_sidecar_path(layout, "raw/web/example.com/kl.html")
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text("{}", encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(
                        ClipCollection(
                            id="c1",
                            source_key="raw/web/example.com/kl.html",
                            source_label="KL",
                            block_ids=("b1",),
                            blocks=(
                                DerivedBlock(
                                    id="b1",
                                    tag="p",
                                    text="kl",
                                    preview="kl",
                                ),
                            ),
                        ),
                    ),
                ),
            )
            result = delete_project_file(
                layout,
                "raw/web/example.com/kl.html",
            )
            self.assertEqual(result["deleted"], "raw/web/example.com/kl.html")
            self.assertEqual(result["droppedCollections"], 1)
            self.assertFalse(raw.is_file())
            self.assertFalse(sidecar.is_file())
            self.assertFalse((layout.raw / "web" / "example.com").is_dir())
            self.assertEqual(load_clip_store(layout).collections, ())


if __name__ == "__main__":
    unittest.main()
