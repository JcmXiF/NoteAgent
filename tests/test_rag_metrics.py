"""The retrieval metrics: cut-offs, ideal gains and aggregation."""

from __future__ import annotations

import math
import unittest

from evals.rag.metrics import (
    MetricSet,
    Score,
    hit_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)


class RecallTests(unittest.TestCase):
    def test_recall_counts_the_share_of_expected_pages_found(self) -> None:
        ranked = ["x", "a", "b", "y"]
        relevant = frozenset({"a", "b"})

        self.assertEqual(recall_at_k(ranked, relevant, 4), 1.0)
        self.assertEqual(recall_at_k(ranked, relevant, 1), 0.0)
        self.assertEqual(recall_at_k(ranked, relevant, 3), 1.0)

    def test_a_duplicate_result_is_counted_once(self) -> None:
        ranked = ["a", "a"]

        self.assertEqual(
            recall_at_k(ranked, frozenset({"a", "b"}), 2), 0.5
        )

    def test_no_relevant_items_is_a_contract_error(self) -> None:
        with self.assertRaises(ValueError):
            recall_at_k(["a"], frozenset(), 5)


class HitTests(unittest.TestCase):
    def test_hit_is_one_when_any_expected_page_is_in_the_cut(self) -> None:
        relevant = frozenset({"a"})

        self.assertEqual(hit_at_k(["x", "a"], relevant, 2), 1.0)
        self.assertEqual(hit_at_k(["x", "a"], relevant, 1), 0.0)


class NdcgTests(unittest.TestCase):
    def test_a_relevant_first_is_a_perfect_score(self) -> None:
        self.assertAlmostEqual(
            ndcg_at_k(["a", "x"], frozenset({"a"}), 10), 1.0
        )

    def test_a_relevant_second_is_discounted_by_its_position(self) -> None:
        expected = 1 / math.log2(3)

        self.assertAlmostEqual(
            ndcg_at_k(["x", "a"], frozenset({"a"}), 10), expected
        )

    def test_the_ideal_ranks_every_expected_page_first(self) -> None:
        # DCG = 1 + 0 + 1/log2(4) = 1.5; IDCG = 1 + 1/log2(3).
        expected = 1.5 / (1 + 1 / math.log2(3))

        self.assertAlmostEqual(
            ndcg_at_k(["a", "x", "b"], frozenset({"a", "b"}), 10),
            expected,
        )

    def test_the_cut_limits_both_gain_and_ideal(self) -> None:
        relevant = frozenset(str(index) for index in range(12))

        score = ndcg_at_k(
            [str(index) for index in range(12)], relevant, 10
        )

        self.assertAlmostEqual(score, 1.0)


class ReciprocalRankTests(unittest.TestCase):
    def test_the_first_relevant_position_is_reciprocated(self) -> None:
        relevant = frozenset({"a"})

        self.assertEqual(reciprocal_rank(["x", "y", "a"], relevant), 1 / 3)
        self.assertEqual(reciprocal_rank(["x", "y"], relevant), 0.0)

    def test_the_depth_bounds_the_search(self) -> None:
        self.assertEqual(
            reciprocal_rank(["x", "a"], frozenset({"a"}), depth=1), 0.0
        )


class PercentileTests(unittest.TestCase):
    def test_the_nearest_rank_is_used(self) -> None:
        values = [float(index) for index in range(1, 21)]

        self.assertEqual(percentile(values, 50), 10.0)
        self.assertEqual(percentile(values, 95), 19.0)
        self.assertEqual(percentile(values, 100), 20.0)

    def test_an_empty_sample_is_zero(self) -> None:
        self.assertEqual(percentile([], 95), 0.0)

    def test_one_value_is_its_own_percentile(self) -> None:
        self.assertEqual(percentile([2.5], 50), 2.5)
        self.assertEqual(percentile([2.5], 95), 2.5)


class MetricSetTests(unittest.TestCase):
    def test_the_set_averages_each_metric(self) -> None:
        scores = [
            Score(recall=1.0, hit=1.0, ndcg=1.0, mrr=1.0),
            Score(recall=0.0, hit=0.0, ndcg=0.5, mrr=0.0),
        ]

        summary = MetricSet.of(scores)

        self.assertEqual(summary.count, 2)
        self.assertEqual(summary.recall_at_5, 0.5)
        self.assertEqual(summary.hit_at_5, 0.5)
        self.assertEqual(summary.ndcg_at_10, 0.75)
        self.assertEqual(summary.mrr, 0.5)

    def test_an_empty_set_has_no_queries(self) -> None:
        summary = MetricSet.of([])

        self.assertEqual(summary.count, 0)
        self.assertEqual(summary.recall_at_5, 0.0)

    def test_the_report_payload_rounds_for_reading(self) -> None:
        summary = MetricSet.of(
            [Score(recall=2 / 3, hit=1.0, ndcg=0.12345, mrr=0.5)]
        )

        payload = summary.as_dict()

        self.assertEqual(
            payload,
            {
                "count": 1,
                "recallAt5": 0.667,
                "hitAt5": 1.0,
                "ndcgAt10": 0.123,
                "mrr": 0.5,
            },
        )


if __name__ == "__main__":
    unittest.main()
