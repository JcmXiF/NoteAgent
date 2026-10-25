from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.auto_ingest import auto_ingest
from noteagent.ingest.compile_cache import lookup, read_records, record_ingest
from noteagent.project.init import init_project

FIXTURE = Path(__file__).parent / "fixtures" / "sample_generation_blocks.txt"


class CompileCacheTests(unittest.TestCase):
    def _seed_source(self, root: Path) -> Path:
        source = root / "raw" / "demo" / "bellman.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Bellman\n\nbody\n", encoding="utf-8")
        return source

    def test_cache_hit_skips_second_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            generation = FIXTURE.read_text(encoding="utf-8")

            first = auto_ingest(
                layout,
                source,
                analysis_text="analysis",
                generation_text=generation,
            )
            self.assertFalse(first.cache_hit)
            self.assertTrue(first.write.written)
            self.assertTrue(layout.compile_cache.is_file())

            second = auto_ingest(
                layout,
                source,
                analysis_text="SHOULD NOT RUN",
                generation_text="---FILE: wiki/concepts/nope.md---\nx\n---END FILE---",
            )
            self.assertTrue(second.cache_hit)
            self.assertEqual(second.write.written, ())
            self.assertEqual(second.analysis, "")

            forced = auto_ingest(
                layout,
                source,
                analysis_text="analysis again",
                generation_text=generation,
                force=True,
            )
            self.assertFalse(forced.cache_hit)
            self.assertTrue(forced.write.written)

    def test_cache_miss_when_output_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            doc_text = source.read_text(encoding="utf-8")
            record_ingest(
                layout,
                "raw/demo/bellman.md",
                doc_text,
                ["wiki/concepts/missing.md"],
            )
            answer = lookup(layout, "raw/demo/bellman.md", doc_text)
            self.assertFalse(answer.hit)
            self.assertIn("gone", answer.why)

    def test_a_record_with_no_output_cannot_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            doc_text = source.read_text(encoding="utf-8")
            record_ingest(layout, "raw/demo/bellman.md", doc_text, [])
            answer = lookup(layout, "raw/demo/bellman.md", doc_text)
            self.assertFalse(answer.hit)
            self.assertIn("names no output", answer.why)

    def test_a_newer_format_reads_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            layout.meta.mkdir(parents=True, exist_ok=True)
            layout.compile_cache.write_text(
                json.dumps({"format": 99, "sources": {"raw/a.md": {"digest": "x"}}}),
                encoding="utf-8",
            )
            self.assertEqual(read_records(layout), {})

    def test_dry_run_does_not_record_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            auto_ingest(
                layout,
                source,
                analysis_text="analysis",
                generation_text=FIXTURE.read_text(encoding="utf-8"),
                dry_run=True,
            )
            self.assertFalse(layout.compile_cache.exists())


if __name__ == "__main__":
    unittest.main()
