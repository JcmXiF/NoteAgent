"""The RAG retrieval eval set: what it accepts, refuses and checks.

The set is a measurement input, not a gate: a typo in a page name would quietly
score zero and look like a retrieval regression, so every expected page and
note is checked against the corpus before a query runs.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.rag.dataset import (
    MIN_QUERIES,
    QUERY_TYPES,
    DatasetError,
    check_corpus,
    load_dataset,
)


def write_dataset(root: Path, rows: list[dict]) -> Path:
    path = root / "queries.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def row(
    *,
    id: str = "exact-01",
    query: str = "entropy",
    type: str = "exact",
    pages: tuple[str, ...] = ("concepts/entropy",),
    notes: tuple[str, ...] = (),
    rationale: str = "",
) -> dict:
    payload = {
        "id": id,
        "query": query,
        "type": type,
        "expected_pages": list(pages),
        "expected_notes": list(notes),
    }
    if rationale:
        payload["rationale"] = rationale
    return payload


def five_types() -> list[dict]:
    return [
        row(),
        row(id="para-01", query="两个分布差多少", type="paraphrase"),
        row(
            id="cross-01",
            query="熵与 KL 散度",
            type="cross_page",
            pages=("concepts/entropy", "concepts/kl-divergence"),
        ),
        row(
            id="gap-01",
            query="如何烤酸面包",
            type="gap",
            pages=(),
        ),
        row(
            id="notes-01",
            query="复习计划",
            type="notes",
            pages=(),
            notes=("20261101-090000-abcd",),
        ),
    ]


def make_corpus(root: Path) -> None:
    page = root / "wiki" / "concepts" / "entropy.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        '---\ntitle: "Entropy"\n---\n\n熵衡量不确定性。\n',
        encoding="utf-8",
    )
    other = root / "wiki" / "concepts" / "kl-divergence.md"
    other.write_text(
        '---\ntitle: "KL 散度"\n---\n\n两个概率分布的差异。\n',
        encoding="utf-8",
    )
    note = (
        root
        / "notes"
        / "concepts"
        / "entropy"
        / "20261101-090000-abcd.md"
    )
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\n"
        "page: wiki/concepts/entropy.md\n"
        'created: "2026-11-01T09:00:00.000000"\n'
        'updated: "2026-11-01T09:00:00.000000"\n'
        "---\n"
        "\n复习计划：先读熵。\n",
        encoding="utf-8",
    )


class ValidDatasetTests(unittest.TestCase):
    def test_every_query_type_loads_with_its_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp), five_types())

            dataset = load_dataset(path, require_full=False)

        self.assertEqual(len(dataset.queries), 5)
        self.assertEqual(
            {query.type for query in dataset.queries}, set(QUERY_TYPES)
        )
        gap = next(
            query for query in dataset.queries if query.type == "gap"
        )
        self.assertEqual(gap.expected, ())
        notes = next(
            query for query in dataset.queries if query.type == "notes"
        )
        self.assertEqual(
            notes.expected, ("note:20261101-090000-abcd",)
        )
        self.assertEqual(
            dataset.counts,
            {
                "exact": 1,
                "paraphrase": 1,
                "cross_page": 1,
                "gap": 1,
                "notes": 1,
            },
        )
        self.assertEqual(len(dataset.sha256), 64)

    def test_an_id_is_optional_and_derived_from_the_line(self) -> None:
        bare = row()
        del bare["id"]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp), [bare])

            dataset = load_dataset(path, require_full=False)

        self.assertEqual(dataset.queries[0].id, "q01")

    def test_blank_lines_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queries.jsonl"
            line = json.dumps(row(), ensure_ascii=False)
            path.write_text(f"\n{line}\n\n", encoding="utf-8")

            dataset = load_dataset(path, require_full=False)

        self.assertEqual(len(dataset.queries), 1)

    def test_a_full_dataset_needs_twenty_queries_and_every_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(
                Path(tmp),
                [
                    row(id=f"exact-{index:02d}")
                    for index in range(MIN_QUERIES)
                ],
            )
            with self.assertRaises(DatasetError):
                load_dataset(path)

    def test_a_full_dataset_without_notes_is_refused(self) -> None:
        rows = [
            row(id=f"exact-{index:02d}")
            for index in range(5)
        ] + [
            row(
                id=f"para-{index:02d}",
                query=f"同义改写 {index}",
                type="paraphrase",
            )
            for index in range(5)
        ] + [
            row(
                id=f"cross-{index:02d}",
                query=f"跨页 {index}",
                type="cross_page",
            )
            for index in range(5)
        ] + [
            row(
                id=f"gap-{index:02d}",
                query=f"缺口 {index}",
                type="gap",
                pages=(),
            )
            for index in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp), rows)

            with self.assertRaises(DatasetError):
                load_dataset(path)


class InvalidDatasetTests(unittest.TestCase):
    def load_bad(self, bad: dict) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp), [bad])
            with self.assertRaises(DatasetError) as caught:
                load_dataset(path, require_full=False)
        return str(caught.exception)

    def test_a_missing_field_is_refused(self) -> None:
        bad = row()
        del bad["query"]

        self.assertIn("query", self.load_bad(bad))

    def test_an_unknown_field_is_refused(self) -> None:
        bad = row()
        bad["expected"] = ["concepts/entropy"]

        self.assertIn("expected", self.load_bad(bad))

    def test_an_unknown_type_is_refused(self) -> None:
        bad = row(type="semantic")

        self.assertIn("semantic", self.load_bad(bad))

    def test_a_duplicate_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_dataset(Path(tmp), [row(), row()])
            with self.assertRaises(DatasetError) as caught:
                load_dataset(path, require_full=False)

        self.assertIn("duplicate", str(caught.exception).lower())

    def test_a_gap_query_cannot_expect_results(self) -> None:
        bad = row(type="gap", pages=("concepts/entropy",))

        self.assertIn("gap", self.load_bad(bad))

    def test_only_a_notes_query_may_expect_notes(self) -> None:
        bad = row(notes=("20261101-090000-abcd",))

        self.assertIn("notes", self.load_bad(bad))

    def test_a_notes_query_must_expect_a_note(self) -> None:
        bad = row(type="notes", pages=(), notes=())

        self.assertIn("notes", self.load_bad(bad))

    def test_a_non_gap_query_must_expect_a_page(self) -> None:
        bad = row(pages=())

        self.assertIn("page", self.load_bad(bad))

    def test_a_missing_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(DatasetError):
                load_dataset(
                    Path(tmp) / "absent.jsonl", require_full=False
                )


class CorpusCheckTests(unittest.TestCase):
    def test_expected_pages_and_notes_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_corpus(root)
            dataset = load_dataset(
                write_dataset(root, five_types()), require_full=False
            )

            check_corpus(dataset, root)

    def test_a_missing_page_names_the_query_and_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_corpus(root)
            rows = [
                row(
                    id="exact-missing",
                    pages=("concepts/absent",),
                )
            ]
            dataset = load_dataset(
                write_dataset(root, rows), require_full=False
            )

            with self.assertRaises(DatasetError) as caught:
                check_corpus(dataset, root)

        message = str(caught.exception)
        self.assertIn("exact-missing", message)
        self.assertIn("concepts/absent", message)

    def test_a_missing_note_names_the_query_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_corpus(root)
            rows = [
                row(
                    id="notes-missing",
                    type="notes",
                    pages=(),
                    notes=("20261101-090000-ffff",),
                )
            ]
            dataset = load_dataset(
                write_dataset(root, rows), require_full=False
            )

            with self.assertRaises(DatasetError) as caught:
                check_corpus(dataset, root)

        message = str(caught.exception)
        self.assertIn("notes-missing", message)
        self.assertIn("20261101-090000-ffff", message)

    def test_a_corpus_without_a_wiki_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = load_dataset(
                write_dataset(root, [row()]), require_full=False
            )

            with self.assertRaises(DatasetError) as caught:
                check_corpus(dataset, root)

        self.assertIn("wiki", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
