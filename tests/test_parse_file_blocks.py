from __future__ import annotations

import unittest
from pathlib import Path

from noteagent.ingest.parse_file_blocks import (
    is_safe_ingest_path,
    parse_file_blocks,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_file_blocks.txt"


class ParseFileBlocksTests(unittest.TestCase):
    def test_fixture_extracts_safe_blocks_and_warnings(self) -> None:
        text = FIXTURE.read_text(encoding="utf-8")
        result = parse_file_blocks(text)

        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(result.blocks[0].path, "wiki/concepts/bellman-equation.md")
        self.assertIn("贝尔曼方程", result.blocks[0].content)
        # A ---END FILE--- inside a fence must not end the block.
        self.assertIn("```markdown", result.blocks[0].content)
        self.assertIn("---END FILE---", result.blocks[0].content)
        self.assertEqual(result.blocks[1].path, "wiki/concepts/value-function.md")

        warning_text = "\n".join(result.warnings)
        self.assertIn("was refused", warning_text)
        self.assertIn("never closed", warning_text)

    def test_crlf_normalized(self) -> None:
        text = (
            "---FILE: wiki/concepts/a.md---\r\n"
            "hello\r\n"
            "---END FILE---\r\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual(len(result.blocks), 1)
        self.assertEqual(result.blocks[0].content, "hello")

    def test_empty_path_warns(self) -> None:
        text = "---FILE: ---\nbody\n---END FILE---\n"
        result = parse_file_blocks(text)
        self.assertEqual(result.blocks, ())
        self.assertTrue(any("named no path" in w for w in result.warnings))

    def test_opener_without_trailing_dashes(self) -> None:
        """
        Models often emit opener then a frontmatter --- on the next line.
        """
        text = (
            "---FILE: wiki/concepts/bellman.md\n"
            "---\n"
            "type: concept\n"
            "title: Bellman\n"
            "---\n"
            "\n"
            "# Bellman\n"
            "---END FILE---\n"
            "---FILE: wiki/concepts/other.md---\n"
            "# Other\n"
            "---END FILE---\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual(len(result.blocks), 2)
        self.assertEqual(result.blocks[0].path, "wiki/concepts/bellman.md")
        self.assertTrue(result.blocks[0].content.startswith("---\n"))
        self.assertIn("type: concept", result.blocks[0].content)
        self.assertEqual(result.blocks[1].path, "wiki/concepts/other.md")

    def test_an_opener_inside_prose_is_not_a_block(self) -> None:
        text = (
            "The answer explains the markers: `---FILE: wiki/a.md---` opens one.\n"
            "Then it says how to close it.\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual(result.blocks, ())
        self.assertEqual(result.warnings, ())

    def test_a_longer_fence_keeps_the_shorter_close_inside_the_sample(self) -> None:
        text = (
            "---FILE: wiki/a.md---\n"
            "````markdown\n"
            "```\n"
            "---END FILE---\n"
            "```\n"
            "````\n"
            "---END FILE---\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("---END FILE---\n```\n````", result.blocks[0].content)

    def test_a_tilde_sample_is_not_closed_by_backticks(self) -> None:
        text = (
            "---FILE: wiki/a.md---\n"
            "~~~\n"
            "---END FILE---\n"
            "```\n"
            "~~~\n"
            "---END FILE---\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual(len(result.blocks), 1)
        self.assertIn("---END FILE---\n```", result.blocks[0].content)

    def test_an_unclosed_block_costs_only_itself(self) -> None:
        text = (
            "---FILE: wiki/cut-off.md---\n"
            "# Half a page\n"
            "---FILE: wiki/whole.md---\n"
            "# Whole page\n"
            "---END FILE---\n"
        )
        result = parse_file_blocks(text)
        self.assertEqual([block.path for block in result.blocks], ["wiki/whole.md"])
        self.assertTrue(any("cut-off.md" in w for w in result.warnings))

    def test_is_safe_ingest_path(self) -> None:
        self.assertTrue(is_safe_ingest_path("wiki/concepts/foo.md"))
        self.assertFalse(is_safe_ingest_path("../wiki/foo.md"))
        self.assertFalse(is_safe_ingest_path("/etc/passwd"))
        self.assertFalse(is_safe_ingest_path("C:/Windows/a.md"))
        self.assertFalse(is_safe_ingest_path("raw/a.md"))
        self.assertFalse(is_safe_ingest_path("wiki/con.md"))


if __name__ == "__main__":
    unittest.main()
