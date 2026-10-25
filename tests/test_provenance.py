"""The provenance note has to stay a record, not a gesture.

The layout plan retires, rewrites or attributes every item in the audit it was
written from. Attribution is the one part no other check can see: a deleted row
still renders, and a row that says nothing still parses. These tests hold the
two things that make the note worth having — the concepts it must cover, and the
shape that makes each entry a claim (a concept, and one sentence of difference).
"""

from __future__ import annotations

import unittest
from pathlib import Path

NOTE = Path(__file__).resolve().parent.parent / "docs" / "provenance.md"

#: Concepts the audit keeps rather than retires, so each needs a sentence here.
#: A new row is welcome; a missing one means a shared idea went unrecorded.
KEPT_CONCEPTS = (
    "A project folder the app owns",
    "The human gate",
    "Citations and source identity",
    "Deterministic index",
    "Append-only log",
    "The wiki graph",
    "Chat over the wiki",
    "Notes beside the wiki",
    "Deep Search",
    "Activity panel",
    "Compile cache",
    "Page merging",
    "FILE-block parsing",
    "Prompt text",
)


def difference_rows(text: str) -> dict[str, str]:
    """The concept → difference pairs from the one table that has that header."""
    rows: dict[str, str] = {}
    in_table = False
    for line in text.split("\n"):
        if line.startswith("|") and "How this project differs" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 2 or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = cells[1]
    return rows


class ProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = NOTE.read_text(encoding="utf-8")
        self.rows = difference_rows(self.text)

    def test_the_note_exists_where_the_readme_points(self) -> None:
        self.assertTrue(NOTE.is_file())
        readme = (NOTE.parent.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/provenance.md", readme)

    def test_every_kept_concept_has_a_difference_sentence(self) -> None:
        missing = [name for name in KEPT_CONCEPTS if name not in self.rows]
        self.assertEqual(missing, [], "add a row for each shared concept")

    def test_each_difference_is_a_finished_sentence(self) -> None:
        for concept, difference in self.rows.items():
            with self.subTest(concept=concept):
                self.assertTrue(difference, "a row with no sentence claims nothing")
                self.assertTrue(
                    difference.endswith("."),
                    "a difference has to read as a sentence",
                )

    def test_the_note_states_the_adoption_and_the_licence(self) -> None:
        lowered = self.text.lower()
        self.assertIn("gpl-3.0", lowered)
        self.assertIn("mit", lowered)
        self.assertIn("d-043", lowered)

    def test_the_reference_project_is_attributed(self) -> None:
        # The GPL-3.0 project this design was compared against belongs to someone
        # else. Naming it, its holder and its licence is the whole point of the note.
        self.assertIn("Yong Su", self.text)
        self.assertIn("nashsu/llm_wiki", self.text)
        self.assertIn("different author", self.text)

    def test_the_note_does_not_claim_the_reference_as_our_own(self) -> None:
        self.assertNotIn("written by the same author", self.text)
        for line in self.text.split("\n"):
            if "llm_wiki" in line and "same author" in line:
                self.fail("llm_wiki is a separate project by a different author")

    def test_the_note_does_not_claim_what_it_cannot_know(self) -> None:
        # The reference project's internals are outside this repository; the note
        # says what this project chose instead of describing code it cannot show.
        self.assertIn("What is not claimed", self.text)
        self.assertIn("do not assert what `llm_wiki`'s", self.text)


if __name__ == "__main__":
    unittest.main()
