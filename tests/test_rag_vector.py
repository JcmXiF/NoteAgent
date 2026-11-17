"""Vector retrieval: cosine ranking, page aggregation and the kind filter."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import lancedb

from noteagent.rag.embedding import EmbeddingError
from noteagent.rag.vector import (
    ChunkScore,
    VectorRetriever,
    aggregate_pages,
)

DIM = 4


def chunk(
    chunk_id: str,
    page_id: str,
    similarity: float,
    *,
    kind: str = "wiki",
    index: int = 0,
    text: str = "text",
    heading: str = "",
) -> ChunkScore:
    return ChunkScore(
        chunk_id=chunk_id,
        page_id=page_id,
        kind=kind,
        chunk_index=index,
        chunk_text=text,
        heading_path=heading,
        similarity=similarity,
    )


def row(
    chunk_id: str,
    page_id: str,
    vector: list[float],
    *,
    kind: str = "wiki",
    index: int = 0,
    text: str = "text",
    heading: str = "",
) -> dict:
    return {
        "chunk_id": chunk_id,
        "kind": kind,
        "page_id": page_id,
        "chunk_index": index,
        "chunk_text": text,
        "heading_path": heading,
        "vector": vector,
    }


class FixedEmbedder:
    """Returns one fixed query vector and records that it was a query."""

    def __init__(self, vector: list[float], *, dim: int | None = None) -> None:
        self.vector = list(vector)
        self.dim = dim if dim is not None else len(vector)
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    def embed(self, texts, *, query: bool = False):
        items = tuple(texts)
        self.calls.append((items, query))
        return [tuple(self.vector) for _ in items]


class AggregationTests(unittest.TestCase):
    def test_a_single_chunk_page_keeps_its_similarity(self) -> None:
        hits = aggregate_pages(
            [chunk("wiki:a#0", "a", 0.8, text="alpha", heading="A")]
        )

        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].score, 0.8)
        self.assertEqual(hits[0].page_id, "a")
        self.assertEqual(hits[0].best.chunk_id, "wiki:a#0")
        self.assertEqual(hits[0].best.chunk_text, "alpha")
        self.assertEqual(hits[0].best.heading_path, "A")

    def test_the_tail_adds_a_bonus(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:a#0", "a", 0.8),
                chunk("wiki:a#1", "a", 0.6, index=1),
            ]
        )

        self.assertAlmostEqual(hits[0].score, 0.8 + 0.3 * 0.6)

    def test_the_bonus_is_capped_by_the_room_left_to_one(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:a#0", "a", 0.9),
                chunk("wiki:a#1", "a", 0.8, index=1),
            ]
        )

        self.assertAlmostEqual(hits[0].score, 1.0)

    def test_a_negative_tail_lowers_the_best_chunk(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:a#0", "a", 0.5),
                chunk("wiki:a#1", "a", -0.2, index=1),
            ]
        )

        self.assertAlmostEqual(hits[0].score, 0.5 - 0.3 * 0.2)

    def test_the_best_chunk_represents_the_page(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:a#0", "a", 0.4, text="weak"),
                chunk(
                    "wiki:a#1",
                    "a",
                    0.7,
                    index=1,
                    text="strong",
                    heading="B",
                ),
            ]
        )

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].best.chunk_id, "wiki:a#1")
        self.assertEqual(hits[0].best.chunk_text, "strong")
        self.assertEqual(hits[0].best.heading_path, "B")
        self.assertAlmostEqual(hits[0].score, 0.7 + 0.3 * 0.4)

    def test_pages_are_ordered_by_score_then_name(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:b#0", "b", 0.6),
                chunk("wiki:a#0", "a", 0.8),
                chunk("wiki:c#0", "c", 0.8),
            ]
        )

        self.assertEqual(
            [hit.page_id for hit in hits], ["a", "c", "b"]
        )

    def test_a_wiki_and_a_note_with_the_same_page_id_stay_apart(self) -> None:
        hits = aggregate_pages(
            [
                chunk("wiki:a#0", "a", 0.8, kind="wiki"),
                chunk("note:n#0", "a", 0.5, kind="note"),
            ]
        )

        self.assertEqual(
            [(hit.kind, hit.page_id) for hit in hits],
            [("wiki", "a"), ("note", "a")],
        )


class RetrieverCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "rag" / "lancedb"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_index(self, rows: list[dict]) -> Path:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(self.root)
        if rows:
            db.create_table("chunks", data=rows)
        else:
            db.create_table(
                "chunks",
                data=[row("dummy", "dummy", [1.0, 0.0, 0.0, 0.0])],
            )
            db.open_table("chunks").delete("chunk_id = 'dummy'")
        return self.root

    def retriever(
        self,
        rows: list[dict],
        vector: list[float],
        *,
        dim: int | None = None,
    ) -> tuple[VectorRetriever, FixedEmbedder]:
        path = self.make_index(rows)
        embedder = FixedEmbedder(vector, dim=dim)
        return VectorRetriever(path, embedder), embedder


class RetrieverTests(RetrieverCase):
    def test_cosine_similarity_is_converted_and_pages_are_merged(self) -> None:
        retriever, embedder = self.retriever(
            [
                row("wiki:a#0", "a", [0.8, 0.6, 0.0, 0.0]),
                row(
                    "wiki:a#1",
                    "a",
                    [0.6, 0.8, 0.0, 0.0],
                    index=1,
                ),
                row("wiki:b#0", "b", [0.5, 0.5, 0.5, 0.5]),
            ],
            [1.0, 0.0, 0.0, 0.0],
        )

        hits = retriever.search("anything")

        self.assertEqual([hit.page_id for hit in hits], ["a", "b"])
        self.assertAlmostEqual(hits[0].score, 0.98, places=4)
        self.assertAlmostEqual(hits[1].score, 0.5, places=4)
        self.assertEqual(hits[0].best.chunk_id, "wiki:a#0")
        self.assertEqual(embedder.calls, [(("anything",), True)])

    def test_the_default_kind_filter_covers_wiki_and_notes(self) -> None:
        retriever, _embedder = self.retriever(
            [
                row("wiki:a#0", "a", [1.0, 0.0, 0.0, 0.0]),
                row(
                    "note:n#0",
                    "n",
                    [1.0, 0.0, 0.0, 0.0],
                    kind="note",
                ),
            ],
            [1.0, 0.0, 0.0, 0.0],
        )

        hits = retriever.search("anything")

        self.assertEqual(
            {hit.kind for hit in hits}, {"wiki", "note"}
        )

    def test_the_kind_filter_limits_the_rows(self) -> None:
        retriever, _embedder = self.retriever(
            [
                row("wiki:a#0", "a", [1.0, 0.0, 0.0, 0.0]),
                row(
                    "note:n#0",
                    "a",
                    [1.0, 0.0, 0.0, 0.0],
                    kind="note",
                ),
            ],
            [1.0, 0.0, 0.0, 0.0],
        )

        hits = retriever.search("anything", kinds=("wiki",))
        bare = retriever.search("anything", kinds="wiki")

        self.assertEqual([hit.kind for hit in hits], ["wiki"])
        self.assertEqual([hit.kind for hit in bare], ["wiki"])

    def test_the_over_fetch_limits_the_chunks_fetched(self) -> None:
        rows = [
            row(f"wiki:p{index}#0", f"p{index}", [1.0, 0.0, 0.0, 0.0])
            for index in range(5)
        ]
        retriever, _embedder = self.retriever(
            rows, [1.0, 0.0, 0.0, 0.0]
        )

        hits = retriever.search("anything", over_fetch=2)

        self.assertEqual(len(hits), 2)

    def test_the_limit_caps_the_pages(self) -> None:
        rows = [
            row(f"wiki:p{index}#0", f"p{index}", [1.0, 0.0, 0.0, 0.0])
            for index in range(4)
        ]
        retriever, _embedder = self.retriever(
            rows, [1.0, 0.0, 0.0, 0.0]
        )

        hits = retriever.search("anything", limit=2)

        self.assertEqual(len(hits), 2)

    def test_an_empty_index_returns_nothing(self) -> None:
        retriever, _embedder = self.retriever(
            [], [1.0, 0.0, 0.0, 0.0]
        )

        self.assertEqual(retriever.search("anything"), [])

    def test_a_missing_index_returns_nothing(self) -> None:
        embedder = FixedEmbedder([1.0, 0.0, 0.0, 0.0])
        retriever = VectorRetriever(self.root / "absent", embedder)

        self.assertEqual(retriever.search("anything"), [])

    def test_an_empty_query_returns_nothing(self) -> None:
        retriever, embedder = self.retriever(
            [row("wiki:a#0", "a", [1.0, 0.0, 0.0, 0.0])],
            [1.0, 0.0, 0.0, 0.0],
        )

        self.assertEqual(retriever.search("   "), [])
        self.assertEqual(embedder.calls, [])

    def test_no_kind_matches_returns_nothing(self) -> None:
        retriever, _embedder = self.retriever(
            [row("wiki:a#0", "a", [1.0, 0.0, 0.0, 0.0])],
            [1.0, 0.0, 0.0, 0.0],
        )

        self.assertEqual(retriever.search("anything", kinds=("note",)), [])

    def test_a_dimension_mismatch_is_refused(self) -> None:
        retriever, _embedder = self.retriever(
            [row("wiki:a#0", "a", [1.0, 0.0, 0.0, 0.0])],
            [1.0, 0.0, 0.0],
            dim=DIM,
        )

        with self.assertRaises(EmbeddingError):
            retriever.search("anything")


if __name__ == "__main__":
    unittest.main()
