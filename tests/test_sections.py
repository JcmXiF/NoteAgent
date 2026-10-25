from __future__ import annotations

import json
import unittest
from pathlib import Path

from noteagent.ingest.sections import Section, SectionedBody, read_sections

#: The cases both languages are held to, so "the two agree" is a file rather
#: than a claim. Written by hand and reviewed; `web/src/lib/sections.test.ts`
#: reads the same file.
CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "section_ranges_cases.json").read_text(
        encoding="utf-8"
    )
)

#: Which stamped marker owns a heading, and what may stand in the gap between
#: the two. Held apart from the range cases because it answers a different
#: question about the same text; both languages read this file.
MARKER_CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "section_marker_cases.json").read_text(
        encoding="utf-8"
    )
)


def _shape(section: Section) -> dict[str, object]:
    return {
        "level": section.level,
        "heading": section.heading,
        "start": section.start,
        "markerStart": section.marker_start,
        "headingStart": section.heading_start,
        "headingEnd": section.heading_end,
        "bodyStart": section.body_start,
        "end": section.end,
        "collectionId": section.collection_id,
        "blockIds": list(section.block_ids),
    }


def _rebuilt(text: str, sectioned: SectionedBody) -> str:
    """The preamble plus every section, in order, as text."""
    start, end = sectioned.preamble
    return text[start:end] + "".join(
        text[item.start : item.end] for item in sectioned.sections
    )


class SharedCasesTests(unittest.TestCase):
    def test_ranges_match_the_shared_cases(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                sectioned = read_sections(case["text"])
                self.assertEqual(list(sectioned.preamble), case["preamble"])
                self.assertEqual(
                    [_shape(item) for item in sectioned.sections],
                    case["sections"],
                )

    def test_regions_cover_every_shared_case_exactly(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                sectioned = read_sections(case["text"])
                self.assertEqual(_rebuilt(case["text"], sectioned), case["text"])


class SharedMarkerCasesTests(unittest.TestCase):
    """A heading a clip marker owns starts at that marker's line; others do not."""

    def test_ownership_matches_the_shared_cases(self) -> None:
        for case in MARKER_CASES:
            with self.subTest(case=case["name"]):
                sectioned = read_sections(case["text"])
                self.assertEqual(
                    [
                        {
                            "heading": item.heading,
                            "start": item.start,
                            "markerStart": item.marker_start,
                        }
                        for item in sectioned.sections
                    ],
                    case["sections"],
                )


class CoverTests(unittest.TestCase):
    """The preamble and the sections never overlap and never leave a gap."""

    TEXTS = [
        "",
        "\n",
        "\n\n\n",
        "只有一行",
        "# 页名\n\n引言\n",
        "## 甲\n\n正文\n\n## 乙\n\ny\n",
        "<!-- na-clip:c-1 -->\n## 甲\n\nx\n",
        "<!-- na-clip:c-1 -->\ntext\n\n## 甲\n\nx\n",
        "# 页名\r\n\r\n## 甲\r\n\r\nx\r\n",
        "## 甲",
        "## 甲\n\n正文🙂\n\n## 乙\n\n尾🌍\n",
        "<!-- na-clip:c-1 b1 -->\n## 定义🙂\n\nx\n",
        "<!-- na-clip:c-9 -->\n<!-- na-note -->\n## Defined\n\nbody\n",
    ]

    def test_regions_are_contiguous_and_ordered(self) -> None:
        for text in self.TEXTS:
            with self.subTest(text=text):
                sectioned = read_sections(text)
                start, end = sectioned.preamble
                self.assertEqual(start, 0)
                cursor = end
                for section in sectioned.sections:
                    self.assertEqual(section.start, cursor)
                    self.assertLess(section.start, section.end)
                    cursor = section.end
                self.assertEqual(cursor, len(text))

    def test_a_section_body_starts_after_its_heading(self) -> None:
        for case in CASES:
            for section in read_sections(case["text"]).sections:
                with self.subTest(case=case["name"], heading=section.heading):
                    self.assertLessEqual(section.heading_end, section.body_start)
                    self.assertLessEqual(section.heading_start, section.body_start)
                    self.assertLessEqual(section.body_start, section.end)
                    if section.marker_start is not None:
                        self.assertLessEqual(section.marker_start, section.heading_start)
                    self.assertGreaterEqual(section.level, 2)
                    self.assertLessEqual(section.level, 6)


#: The same sections measured on text with a character outside the basic plane.
#:
#: A shared case cannot carry the offsets here: Python counts code points and the
#: TypeScript mirror counts UTF-16 code units, so the two produce different numbers
#: for the same section. What both must agree on is the text each range cuts out,
#: which is what this file holds.
UNIT_CASES = json.loads(
    (
        Path(__file__).resolve().parent / "fixtures" / "section_ranges_units_cases.json"
    ).read_text(encoding="utf-8")
)


class SharedUnitCasesTests(unittest.TestCase):
    def test_the_same_text_is_cut_no_matter_how_it_is_counted(self) -> None:
        for case in UNIT_CASES:
            with self.subTest(case=case["name"]):
                text = case["text"]
                sectioned = read_sections(text)
                start, end = sectioned.preamble
                slices = [text[start:end]] + [
                    text[item.start : item.end] for item in sectioned.sections
                ]
                self.assertEqual(slices, case["slices"])
                self.assertEqual(
                    [item.heading for item in sectioned.sections], case["headings"]
                )
                self.assertEqual(
                    [item.level for item in sectioned.sections], case["levels"]
                )
                # A wrong count would show up as a cut that does not cover the text.
                self.assertEqual("".join(slices), text)


class ReadMarkerTests(unittest.TestCase):
    """The marker fields come from the same rule as the reading side."""

    def test_owns_the_next_heading_only(self) -> None:
        sectioned = read_sections("<!-- na-clip:c-1 b1,b2 -->\n## 定义\n\nx\n")
        self.assertEqual(len(sectioned.sections), 1)
        section = sectioned.sections[0]
        self.assertEqual(section.collection_id, "c-1")
        self.assertEqual(section.block_ids, ("b1", "b2"))
        self.assertEqual(section.marker_start, 0)

    def test_a_marker_with_prose_under_it_owns_nothing(self) -> None:
        sectioned = read_sections("<!-- na-clip:c-1 -->\n\n一句正文。\n\n## 定义\n\nx\n")
        self.assertIsNone(sectioned.sections[0].marker_start)
        self.assertEqual(sectioned.sections[0].collection_id, "")
        self.assertTrue(sectioned.preamble[1] > 0)

    def test_an_ecg_marker_line_stays_with_the_section_above_it(self) -> None:
        # A `na-clip` marker is the only thing a range follows, so this heading
        # starts at its own line and the compile's marker line above it lies
        # inside the range of the section before it.
        text = "## 甲\n\nx\n\n<!-- na-ecg -->\n## 相关\n\n- y\n"
        sectioned = read_sections(text)
        self.assertEqual([item.heading for item in sectioned.sections], ["甲", "相关"])
        above, related = sectioned.sections
        self.assertEqual(text[above.start : above.end], "## 甲\n\nx\n\n<!-- na-ecg -->\n")
        self.assertIsNone(related.marker_start)
        self.assertEqual(related.start, text.index("## 相关"))
        self.assertEqual(related.heading_start, text.index("## 相关"))


if __name__ == "__main__":
    unittest.main()
