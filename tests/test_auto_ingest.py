from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from noteagent.ingest.auto_ingest import auto_ingest
from noteagent.project.init import init_project
from noteagent.prompts.generation import source_summary_path_for_identity
from noteagent.util.cancel import JobCancelled

FIXTURE = Path(__file__).parent / "fixtures" / "sample_generation_blocks.txt"
SOURCE_REL = Path("raw") / "web" / "bellman.html"


class AutoIngestTests(unittest.TestCase):
    def _seed_source(self, root: Path) -> Path:
        source = root / SOURCE_REL
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "# Bellman\n\nValue functions satisfy a recursive consistency.\n",
            encoding="utf-8",
        )
        return source

    def test_pipeline_writes_with_injected_llm_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            generation = FIXTURE.read_text(encoding="utf-8")

            result = auto_ingest(
                layout,
                source,
                analysis_text="## Subjects worth a page\n- Bellman equation\n",
                generation_text=generation,
            )

            self.assertEqual(result.source_identity, "raw/web/bellman.html")
            self.assertEqual(result.source_summary_path, "wiki/sources/bellman.md")
            paths = {w.relative_path for w in result.write.written}
            self.assertIn("wiki/bellman-equation.md", paths)
            self.assertIn("wiki/sources/bellman.md", paths)
            self.assertNotIn("wiki/index.md", paths)
            self.assertTrue(
                any("Ignored model-generated" in w for w in result.parse.warnings)
            )
            self.assertTrue((root / "wiki" / "bellman-equation.md").is_file())
            self.assertTrue((root / "wiki" / "sources" / "bellman.md").is_file())
            # deterministic index update (not from model FILE blocks)
            self.assertIsNotNone(result.index)
            assert result.index is not None
            self.assertGreaterEqual(len(result.index.added), 1)
            index_text = layout.wiki_index.read_text(encoding="utf-8")
            self.assertIn("[[bellman-equation]]", index_text)
            self.assertIsNotNone(result.doctor)

    def test_missing_source_page_is_reported_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            generation = (
                "---FILE: wiki/only-note.md---\n"
                "---\n"
                "title: Only Note\n"
                "created: 2026-07-20\n"
                "updated: 2026-07-20\n"
                'sources: ["raw/web/bellman.html"]\n'
                "---\n"
                "\n# Only Note\n"
                "---END FILE---\n"
            )

            result = auto_ingest(
                layout,
                source,
                analysis_text="Analysis about the Bellman equation.",
                generation_text=generation,
            )

            summary = root / "wiki" / "sources" / "bellman.md"
            self.assertFalse(summary.exists())
            self.assertEqual(
                result.source_summary_path,
                source_summary_path_for_identity(result.source_identity),
            )
            self.assertTrue(
                any("no source page" in w for w in result.parse.warnings),
                result.parse.warnings,
            )

    def test_dry_run_skips_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            generation = FIXTURE.read_text(encoding="utf-8")

            result = auto_ingest(
                layout,
                source,
                analysis_text="analysis",
                generation_text=generation,
                dry_run=True,
            )

            self.assertTrue(result.write.written)
            self.assertTrue(
                all(w.mode.startswith("dry-run:") for w in result.write.written)
            )
            self.assertFalse((root / "wiki" / "bellman-equation.md").exists())
            self.assertIsNotNone(result.index)
            assert result.index is not None
            self.assertTrue(result.index.dry_run)
            self.assertNotIn(
                "[[bellman-equation]]",
                layout.wiki_index.read_text(encoding="utf-8"),
            )
            self.assertIsNone(result.doctor)

    def test_cancel_before_write_does_not_write_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                auto_ingest(
                    layout,
                    source,
                    analysis_text="analysis",
                    generation_text=FIXTURE.read_text(encoding="utf-8"),
                    cancel=cancel,
                )
            self.assertFalse((root / "wiki" / "bellman-equation.md").exists())

    def test_a_page_the_merge_refused_is_repaired_once(self) -> None:
        identity = "raw/web/bellman.html"
        summary = source_summary_path_for_identity(identity)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            page = root / "wiki" / "bellman-equation.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-01-01\n"
                'sources: ["raw/web/other.html"]\n---\n\n'
                "The Bellman equation relates a value to its successor.\n",
                encoding="utf-8",
            )
            generation = (
                "---FILE: wiki/bellman-equation.md---\n"
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-02-02\n"
                f'sources: ["{identity}"]\n---\n\n'
                "Value functions satisfy a recursive consistency.\n"
                "---END FILE---\n"
                f"---FILE: {summary}---\n"
                "---\ntype: source\ntitle: Bellman\ncreated: 2026-01-01\n"
                "updated: 2026-01-01\n"
                f'sources: ["{identity}"]\n---\n\n'
                "A source page.\n"
                "---END FILE---\n"
            )
            good = (
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-02-02\n"
                f'sources: ["{identity}"]\n---\n\n'
                "The Bellman equation relates a value to its successor.\n\n"
                "Value functions satisfy a recursive consistency.\n"
            )
            reasons: list[str] = []

            def repair(existing, incoming, source_identity, reason):
                reasons.append(reason)
                return good

            result = auto_ingest(
                layout,
                source,
                analysis_text="## Subjects worth a page\n- Bellman equation\n",
                generation_text=generation,
                merger=lambda *_: "a body with no frontmatter\n",
                repair=repair,
            )

            modes = {w.relative_path: w.mode for w in result.write.written}
            self.assertEqual(modes["wiki/bellman-equation.md"], "repair")
            self.assertEqual(len(reasons), 1)
            self.assertIn("frontmatter", reasons[0])
            text = page.read_text(encoding="utf-8")
            self.assertIn("recursive consistency", text)
            self.assertIn("relates a value to its successor", text)
    def test_a_long_existing_page_is_rewritten_with_itself_in_hand(self) -> None:
        identity = "raw/web/bellman.html"
        summary = source_summary_path_for_identity(identity)
        body = "The Bellman equation relates a value to its successor. " * 100
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            layout = init_project(root)
            source = self._seed_source(root)
            page = root / "wiki" / "bellman-equation.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-01-01\n"
                'sources: ["raw/web/other.html"]\n---\n\n'
                f"{body}\n"
                "See [[dynamic-programming]].\n",
                encoding="utf-8",
            )
            generation = (
                "---FILE: wiki/bellman-equation.md---\n"
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-02-02\n"
                f'sources: ["{identity}"]\n---\n\n'
                "<!-- na-clip:bellman -->\n"
                "## Recursive consistency\n\n"
                "Value functions satisfy a recursive consistency.\n"
                "---END FILE---\n"
                f"---FILE: {summary}---\n"
                "---\ntype: source\ntitle: Bellman\ncreated: 2026-01-01\n"
                "updated: 2026-01-01\n"
                f'sources: ["{identity}"]\n---\n\n'
                "A source page.\n"
                "---END FILE---\n"
            )
            answer = (
                "---\ntype: concept\ntitle: Bellman Equation\n"
                "created: 2026-01-01\nupdated: 2026-02-02\n"
                f'sources: ["raw/web/other.html", "{identity}"]\n---\n\n'
                f"{body}\n"
                "<!-- na-clip:bellman -->\n"
                "## Recursive consistency\n\n"
                "Value functions satisfy a recursive consistency.\n\n"
                "See [[dynamic-programming]].\n"
            )
            seen: list[tuple[str, str, str]] = []

            def rewrite(page_path, existing, draft):
                seen.append((page_path, existing, draft))
                return answer

            result = auto_ingest(
                layout,
                source,
                analysis_text="## Subjects worth a page\n- Bellman equation\n",
                generation_text=generation,
                rewrite=rewrite,
            )

            modes = {w.relative_path: w.mode for w in result.write.written}
            self.assertEqual(modes["wiki/bellman-equation.md"], "rewrite")
            # One call, for the page that exists and is long, with both texts.
            self.assertEqual([item[0] for item in seen], ["wiki/bellman-equation.md"])
            self.assertIn("See [[dynamic-programming]].", seen[0][1])
            self.assertIn("recursive consistency", seen[0][2].lower())
            text = page.read_text(encoding="utf-8")
            self.assertIn("recursive consistency", text)
            self.assertIn("na-clip:bellman", text)
            self.assertIn(identity, text)
            self.assertIn("raw/web/other.html", text)


if __name__ == "__main__":
    unittest.main()
