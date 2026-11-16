"""The chunker: sections, stable ids, and the text a chunk carries.

The chunker is a pure function: the same page always yields the same chunks, so
a page's old rows can be deleted and replaced as a set without guessing which
ones changed.
"""

from __future__ import annotations

import unittest

from noteagent.ingest.frontmatter import parse_page
from noteagent.notes.store import Note
from noteagent.rag.chunker import ChunkLimits, chunk_note, chunk_wiki


#: Structural tests want one chunk per section, so they switch the merge off.
NO_MIN = ChunkLimits(min_chars=0)


def page(title: str, body: str) -> str:
    return f'---\ntitle: "{title}"\n---\n\n{body}\n'


class ShortPageTests(unittest.TestCase):
    def test_a_short_page_is_one_chunk_without_its_frontmatter(self) -> None:
        text = page("Entropy", "# 熵\n\n熵衡量不确定性。")

        chunks = chunk_wiki(text, slug="concepts/entropy")

        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk.chunk_id, "wiki:concepts/entropy#0")
        self.assertEqual(chunk.kind, "wiki")
        self.assertEqual(chunk.page_id, "concepts/entropy")
        self.assertEqual(chunk.chunk_index, 0)
        self.assertEqual(chunk.heading_path, "")
        self.assertEqual(chunk.chunk_text, "# 熵\n\n熵衡量不确定性。")
        self.assertEqual(
            chunk.embed_text, "Entropy\n# 熵\n\n熵衡量不确定性。"
        )
        self.assertNotIn("title:", chunk.chunk_text)
        body = parse_page(text).body
        self.assertEqual(
            body[chunk.start : chunk.end], chunk.chunk_text
        )

    def test_a_page_without_a_title_field_uses_the_slug_leaf(self) -> None:
        text = "---\nkind: note\n---\n\nbody text\n"

        (chunk,) = chunk_wiki(text, slug="folder/untitled-page")

        self.assertTrue(chunk.embed_text.startswith("untitled-page\n"))
        self.assertEqual(chunk.chunk_text, "body text")

    def test_an_empty_body_has_no_chunks(self) -> None:
        self.assertEqual(chunk_wiki(page("Empty", ""), slug="empty"), ())
        self.assertEqual(
            chunk_wiki(page("Blank", "   \n\n"), slug="blank"), ()
        )

    def test_a_heading_without_content_has_no_chunks(self) -> None:
        text = page("Only Headings", "## A\n\n### B\n")

        self.assertEqual(chunk_wiki(text, slug="only"), ())

    def test_the_same_input_yields_equal_chunks(self) -> None:
        text = page("Stable", "## A\n\nalpha\n\n## B\n\nbeta")

        first = chunk_wiki(text, slug="stable")
        second = chunk_wiki(text, slug="stable")

        self.assertEqual(first, second)


class HeadingTests(unittest.TestCase):
    def test_headings_become_sections_with_their_path(self) -> None:
        text = page(
            "Paths",
            "## A\n\nalpha\n\n### B\n\nbeta\n\n"
            "#### C\n\ngamma\n\n## D\n\ndelta",
        )

        chunks = chunk_wiki(text, slug="paths", limits=NO_MIN)

        self.assertEqual(
            [chunk.heading_path for chunk in chunks],
            ["A", "A > B", "A > B > C", "D"],
        )
        self.assertEqual(
            [chunk.chunk_text for chunk in chunks],
            ["alpha", "beta", "gamma", "delta"],
        )
        self.assertEqual(
            [chunk.chunk_id for chunk in chunks],
            [f"wiki:paths#{index}" for index in range(4)],
        )
        self.assertEqual(
            [chunk.chunk_index for chunk in chunks],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            chunks[1].embed_text, "Paths\nA > B\nbeta"
        )

    def test_a_level_five_heading_does_not_split(self) -> None:
        text = page(
            "Deep",
            "## A\n\nalpha\n\n##### E\n\nepsilon\n\n## B\n\nbeta",
        )

        chunks = chunk_wiki(text, slug="deep", limits=NO_MIN)

        self.assertEqual(
            [chunk.heading_path for chunk in chunks], ["A", "B"]
        )
        self.assertIn("##### E", chunks[0].chunk_text)
        self.assertIn("epsilon", chunks[0].chunk_text)

    def test_duplicate_headings_get_distinct_indices(self) -> None:
        text = page("Twice", "## A\n\nalpha\n\n## A\n\nbeta")

        chunks = chunk_wiki(text, slug="twice", limits=NO_MIN)

        self.assertEqual(
            [chunk.heading_path for chunk in chunks], ["A", "A"]
        )
        self.assertEqual(
            [chunk.chunk_text for chunk in chunks], ["alpha", "beta"]
        )
        self.assertNotEqual(chunks[0].chunk_id, chunks[1].chunk_id)

    def test_a_heading_inside_a_code_fence_does_not_split(self) -> None:
        text = page(
            "Fenced",
            "## A\n\n```markdown\n## not a heading\n```\n\n"
            "## B\n\nbeta",
        )

        chunks = chunk_wiki(text, slug="fenced", limits=NO_MIN)

        self.assertEqual(
            [chunk.heading_path for chunk in chunks], ["A", "B"]
        )
        self.assertIn("## not a heading", chunks[0].chunk_text)


class LongSectionTests(unittest.TestCase):
    def test_a_long_section_is_split_near_the_target(self) -> None:
        paragraph_a = "A" * 600
        paragraph_b = "B" * 600
        paragraph_c = "C" * 600
        paragraph_d = "D" * 600
        text = page(
            "Long",
            "## Long\n\n"
            + "\n\n".join(
                [paragraph_a, paragraph_b, paragraph_c, paragraph_d]
            ),
        )

        chunks = chunk_wiki(text, slug="long")

        self.assertEqual(len(chunks), 2)
        self.assertIn(paragraph_a, chunks[0].chunk_text)
        self.assertIn(paragraph_b, chunks[0].chunk_text)
        self.assertNotIn(paragraph_c, chunks[0].chunk_text)
        self.assertIn(paragraph_c, chunks[1].chunk_text)
        self.assertIn(paragraph_d, chunks[1].chunk_text)
        self.assertTrue(
            chunks[1].chunk_text.startswith(chunks[0].chunk_text[-200:])
        )
        for chunk in chunks:
            self.assertLessEqual(len(chunk.chunk_text), 1500)

    def test_a_long_english_paragraph_is_cut_at_sentences(self) -> None:
        sentence = "The quick brown fox jumps over the lazy dog. "
        text = page("English", "## English\n\n" + sentence * 50)

        chunks = chunk_wiki(text, slug="english")

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.chunk_text), 1500)
            self.assertTrue(chunk.chunk_text.endswith("."))

    def test_a_long_chinese_paragraph_is_cut_at_sentences(self) -> None:
        sentence = "这是一个用来测试中文分句的句子。"
        text = page("中文", "## 中文\n\n" + sentence * 100)

        chunks = chunk_wiki(text, slug="chinese")

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.chunk_text), 1500)
            self.assertTrue(chunk.chunk_text.endswith("。"))

    def test_overlap_is_part_of_the_reported_span(self) -> None:
        text = page(
            "Span",
            "## Long\n\n" + "\n\n".join(["X" * 600] * 4),
        )

        chunks = chunk_wiki(text, slug="span")
        body = parse_page(text).body

        for chunk in chunks:
            self.assertEqual(
                body[chunk.start : chunk.end], chunk.chunk_text
            )
        self.assertLess(chunks[1].start, chunks[0].end)

    def test_a_tiny_tail_is_merged_into_the_previous_chunk(self) -> None:
        text = page(
            "Tail",
            "## Long\n\n" + "M" * 1200 + "\n\n" + "N" * 60,
        )

        chunks = chunk_wiki(text, slug="tail")

        self.assertEqual(len(chunks), 1)
        self.assertIn("M" * 100, chunks[0].chunk_text)
        self.assertTrue(chunks[0].chunk_text.endswith("N" * 60))

    def test_a_tail_that_would_exceed_the_max_is_not_merged(self) -> None:
        text = page(
            "Tail",
            "## Long\n\n" + "M" * 1470 + "\n\n" + "N" * 60,
        )

        chunks = chunk_wiki(text, slug="tail")

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[1].chunk_text.endswith("N" * 60))


class MinimumTests(unittest.TestCase):
    def test_a_short_section_merges_with_its_neighbour(self) -> None:
        text = page(
            "Merge",
            "## A\n\n" + "A" * 80 + "\n\n## B\n\n" + "B" * 80,
        )

        chunks = chunk_wiki(text, slug="merge")

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading_path, "A")
        self.assertIn("A" * 80, chunks[0].chunk_text)
        self.assertIn("B" * 80, chunks[0].chunk_text)

    def test_sections_above_the_minimum_stay_separate(self) -> None:
        text = page(
            "Keep",
            "## A\n\n" + "A" * 250 + "\n\n## B\n\n" + "B" * 250,
        )

        chunks = chunk_wiki(text, slug="keep")

        self.assertEqual(
            [chunk.heading_path for chunk in chunks], ["A", "B"]
        )
        self.assertEqual(
            [chunk.chunk_text for chunk in chunks],
            ["A" * 250, "B" * 250],
        )


class AtomicBlockTests(unittest.TestCase):
    def test_a_code_fence_longer_than_max_stays_whole(self) -> None:
        code = "```python\n" + ("x = 1\n" * 400) + "```"
        text = page("Code", "## Code\n\n" + code)

        chunks = chunk_wiki(text, slug="code")

        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0].chunk_text), 1500)
        self.assertTrue(chunks[0].chunk_text.startswith("```python"))
        self.assertTrue(chunks[0].chunk_text.endswith("```"))

    def test_a_table_is_not_cut_by_a_chunk_boundary(self) -> None:
        rows = "".join(
            f"| row {index} | value {index} |\n" for index in range(80)
        )
        table = "| name | value |\n|---|---|\n" + rows
        text = page("Table", "## Table\n\n" + table)

        chunks = chunk_wiki(text, slug="table")

        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0].chunk_text), 1500)
        self.assertIn("| row 79 |", chunks[0].chunk_text)
        self.assertTrue(chunks[0].chunk_text.rstrip().endswith("|"))

    def test_a_table_without_leading_pipes_stays_whole(self) -> None:
        rows = "".join(
            f"row {index} | value {index}\n" for index in range(100)
        )
        table = "name | value\n--- | ---\n" + rows
        text = page("Table", "## Table\n\n" + table)

        chunks = chunk_wiki(text, slug="table")

        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0].chunk_text), 1500)
        self.assertIn("row 99 |", chunks[0].chunk_text)

    def test_overlap_does_not_start_inside_a_code_block(self) -> None:
        prose = "P" * 800
        code = "```python\n" + ("x = 1\n" * 100) + "```"
        tail = "Q" * 600
        text = page(
            "Overlap",
            "## A\n\n" + prose + "\n\n" + code + "\n\n" + tail,
        )

        chunks = chunk_wiki(text, slug="overlap")

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(chunks[1].chunk_text.startswith("Q"))
        self.assertNotIn("x = 1", chunks[1].chunk_text)

    def test_an_oversize_single_word_is_hard_split(self) -> None:
        text = page("Word", "## Word\n\n" + "Z" * 3200)

        chunks = chunk_wiki(text, slug="word")

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.chunk_text), 1500)
            self.assertEqual(set(chunk.chunk_text), {"Z"})
        body = parse_page(text).body
        self.assertEqual(
            body[chunks[0].start : chunks[-1].end].count("Z"), 3200
        )


class NoteChunkTests(unittest.TestCase):
    def test_a_note_is_one_chunk_with_its_page_metadata(self) -> None:
        note = Note(
            id="20261101-090000-abcd",
            page="wiki/concepts/entropy.md",
            body="复习计划：先读熵。",
            anchor="性质",
            quote="熵只依赖分布。",
        )

        chunk = chunk_note(note, page_title="Entropy")

        self.assertEqual(chunk.chunk_id, "note:20261101-090000-abcd#0")
        self.assertEqual(chunk.kind, "note")
        self.assertEqual(chunk.page_id, "concepts/entropy")
        self.assertEqual(chunk.chunk_index, 0)
        self.assertEqual(chunk.chunk_text, "复习计划：先读熵。")
        self.assertEqual(chunk.heading_path, "")
        self.assertEqual(chunk.anchor, "性质")
        self.assertEqual(chunk.quote, "熵只依赖分布。")
        self.assertEqual(
            chunk.embed_text,
            "Entropy\n性质\n熵只依赖分布。\n复习计划：先读熵。",
        )

    def test_a_note_without_a_page_title_uses_the_page_leaf(self) -> None:
        note = Note(
            id="20261101-090000-abcd",
            page="wiki/concepts/entropy.md",
            body="body",
        )

        chunk = chunk_note(note)

        self.assertTrue(chunk.embed_text.startswith("entropy\n"))

    def test_a_note_is_stable(self) -> None:
        note = Note(
            id="20261101-090000-abcd",
            page="wiki/concepts/entropy.md",
            body="body",
        )

        self.assertEqual(
            chunk_note(note, page_title="Entropy"),
            chunk_note(note, page_title="Entropy"),
        )


class LimitTests(unittest.TestCase):
    def test_custom_limits_are_honoured(self) -> None:
        text = page("Small", "## A\n\n" + "Q" * 600)
        limits = ChunkLimits(
            target=200, max_chars=300, overlap=50, min_chars=50
        )

        chunks = chunk_wiki(text, slug="small", limits=limits)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.chunk_text), 300)


if __name__ == "__main__":
    unittest.main()
