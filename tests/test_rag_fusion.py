"""RRF fusion: the two page lists, the exact tier, and the final slice."""

from __future__ import annotations

import unittest

from noteagent.rag.bm25 import LexicalHit
from noteagent.rag.fusion import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_EXACT_BONUS,
    DEFAULT_K,
    DEFAULT_RESULT_LIMIT,
    fuse,
)
from noteagent.rag.vector import ChunkScore, VectorHit, aggregate_pages


def lexical(
    page_id: str,
    *,
    exact: bool = False,
    kind: str = "wiki",
) -> LexicalHit:
    return LexicalHit(
        chunk_id=f"{kind}:{page_id}#0",
        page_id=page_id,
        kind=kind,
        chunk_index=0,
        chunk_text=f"{page_id} text",
        heading_path="",
        score=1.0,
        matched=("body",),
        exact=exact,
    )


def vector(
    page_id: str,
    *,
    score: float = 0.9,
    kind: str = "wiki",
) -> VectorHit:
    best = ChunkScore(
        chunk_id=f"{kind}:{page_id}#0",
        page_id=page_id,
        kind=kind,
        chunk_index=0,
        chunk_text=f"{page_id} text",
        heading_path="",
        similarity=score,
    )
    return VectorHit(
        page_id=page_id, kind=kind, score=score, best=best
    )


def score_at(rank: int) -> float:
    return 1.0 / (DEFAULT_K + rank)


class RrfTests(unittest.TestCase):
    def test_the_two_rank_lists_are_summed(self) -> None:
        result = fuse(
            [lexical("a"), lexical("b")],
            [vector("b"), vector("c")],
        )

        self.assertEqual(
            [hit.page_id for hit in result.hits], ["b", "a", "c"]
        )
        self.assertAlmostEqual(
            result.hits[0].score,
            score_at(2) + score_at(1),
            places=5,
        )
        self.assertAlmostEqual(
            result.hits[1].score, score_at(1), places=5
        )
        self.assertAlmostEqual(
            result.hits[2].score, score_at(2), places=5
        )
        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(result.lexical_hits, 2)
        self.assertEqual(result.vector_hits, 2)

    def test_a_page_in_both_lists_is_one_candidate(self) -> None:
        result = fuse(
            [lexical("a")],
            [vector("a")],
        )

        self.assertEqual(len(result.hits), 1)
        hit = result.hits[0]
        self.assertAlmostEqual(
            hit.score, score_at(1) * 2, places=5
        )
        self.assertIsNotNone(hit.lexical)
        self.assertIsNotNone(hit.vector)

    def test_only_the_first_fifty_of_each_list_contribute(self) -> None:
        pages = [lexical(f"p{index:02d}") for index in range(51)]

        result = fuse(pages, [])

        self.assertEqual(len(result.hits), DEFAULT_CANDIDATE_LIMIT)
        self.assertNotIn(
            "p50", [hit.page_id for hit in result.hits]
        )

    def test_only_the_first_fifty_vector_hits_contribute(self) -> None:
        pages = [vector(f"p{index:02d}") for index in range(51)]

        result = fuse([], pages)

        self.assertEqual(len(result.hits), DEFAULT_CANDIDATE_LIMIT)
        self.assertNotIn(
            "p50", [hit.page_id for hit in result.hits]
        )

    def test_the_design_values_are_pinned(self) -> None:
        self.assertEqual(DEFAULT_K, 60)
        self.assertEqual(DEFAULT_CANDIDATE_LIMIT, 50)
        self.assertEqual(DEFAULT_RESULT_LIMIT, 20)

    def test_ties_break_by_slug(self) -> None:
        result = fuse([lexical("b")], [vector("a")])

        self.assertAlmostEqual(
            result.hits[0].score, result.hits[1].score
        )
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )

    def test_a_wiki_page_and_a_note_are_different_candidates(self) -> None:
        result = fuse(
            [lexical("a")],
            [vector("a", kind="note")],
        )

        self.assertEqual(
            {(hit.kind, hit.page_id) for hit in result.hits},
            {("wiki", "a"), ("note", "a")},
        )


class ExactTierTests(unittest.TestCase):
    def test_an_exact_page_ranks_above_a_higher_rrf_score(self) -> None:
        result = fuse(
            [lexical("noisy"), lexical("exact", exact=True)],
            [vector("noisy")],
            exact_bonus=0.0,
        )

        self.assertEqual(result.hits[0].page_id, "exact")
        self.assertTrue(result.hits[0].exact)
        self.assertGreater(
            result.hits[1].score, result.hits[0].score
        )

    def test_the_exact_bonus_is_added_to_the_score(self) -> None:
        result = fuse([lexical("exact", exact=True)], [])

        self.assertAlmostEqual(
            result.hits[0].score,
            score_at(1) + DEFAULT_EXACT_BONUS,
            places=5,
        )


class ModeTests(unittest.TestCase):
    def test_lexical_only_says_lexical(self) -> None:
        result = fuse([lexical("a"), lexical("b")], [])

        self.assertEqual(result.mode, "lexical")
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )

    def test_vector_only_still_says_hybrid(self) -> None:
        result = fuse([], [vector("a"), vector("b")])

        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )

    def test_both_sides_empty_return_nothing(self) -> None:
        result = fuse([], [])

        self.assertEqual(result.hits, ())
        self.assertEqual(result.mode, "lexical")
        self.assertEqual(result.lexical_hits, 0)
        self.assertEqual(result.vector_hits, 0)


class SliceTests(unittest.TestCase):
    def test_the_default_final_slice_is_twenty(self) -> None:
        pages = [lexical(f"p{index:02d}") for index in range(30)]

        result = fuse(pages, [])

        self.assertEqual(
            len(result.top()), DEFAULT_RESULT_LIMIT
        )
        self.assertEqual(len(result.top(5)), 5)


class AggregationFlowTests(unittest.TestCase):
    def test_aggregated_vector_pages_enter_the_fusion_once(self) -> None:
        vector_hits = aggregate_pages(
            [
                ChunkScore(
                    "wiki:a#0", "a", "wiki", 0, "one", "", 0.8
                ),
                ChunkScore(
                    "wiki:a#1", "a", "wiki", 1, "two", "", 0.6
                ),
                ChunkScore(
                    "wiki:b#0", "b", "wiki", 0, "three", "", 0.5
                ),
            ]
        )

        result = fuse([], vector_hits)

        self.assertEqual(
            [hit.page_id for hit in result.hits], ["a", "b"]
        )
        self.assertEqual(len(result.hits), 2)


if __name__ == "__main__":
    unittest.main()
