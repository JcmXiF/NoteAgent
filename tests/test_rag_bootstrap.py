"""Paired bootstrap: deterministic intervals for metric deltas."""

from __future__ import annotations

import unittest

from evals.rag.bootstrap import (
    DEFAULT_RESAMPLES,
    paired_bootstrap_delta,
)


class BootstrapTests(unittest.TestCase):
    def test_a_constant_delta_has_a_zero_width_interval(self) -> None:
        delta = paired_bootstrap_delta(
            [1.0, 1.0, 1.0],
            [0.0, 0.0, 0.0],
            resamples=200,
            seed=7,
        )

        self.assertEqual(delta.mean, 1.0)
        self.assertEqual(delta.low, 1.0)
        self.assertEqual(delta.high, 1.0)
        self.assertEqual(delta.n, 3)
        self.assertEqual(delta.resamples, 200)
        self.assertEqual(delta.seed, 7)

    def test_the_interval_is_deterministic_for_a_seed(self) -> None:
        first = paired_bootstrap_delta(
            [1.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            resamples=500,
            seed=11,
        )
        again = paired_bootstrap_delta(
            [1.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            resamples=500,
            seed=11,
        )

        self.assertEqual(first, again)
        self.assertLessEqual(first.low, first.mean)
        self.assertLessEqual(first.mean, first.high)

    def test_an_empty_pair_is_a_zero_delta(self) -> None:
        delta = paired_bootstrap_delta([], [], seed=3)

        self.assertEqual(delta.n, 0)
        self.assertEqual(delta.mean, 0.0)
        self.assertEqual(delta.resamples, DEFAULT_RESAMPLES)

    def test_mismatched_lengths_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            paired_bootstrap_delta([1.0], [1.0, 0.0])


if __name__ == "__main__":
    unittest.main()
