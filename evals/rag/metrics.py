"""Retrieval metrics: Recall@5, Hit@5, nDCG@10, MRR, and their aggregate.

Relevance is binary and set-based: a query names the pages (or notes) that
answer it, and a ranked list is scored by where those ids appear. Only queries
with at least one expected result are scored; a gap query is measured by
whether the system abstained, not by a metric that divides by zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "MetricSet",
    "Score",
    "hit_at_k",
    "ndcg_at_k",
    "percentile",
    "recall_at_k",
    "reciprocal_rank",
]


def recall_at_k(
    ranked: Sequence[str], relevant: Iterable[str], k: int
) -> float:
    """The share of expected ids found in the first *k* results."""
    wanted = _wanted(relevant, "recall")
    found = len(set(ranked[: max(0, k)]) & wanted)
    return found / len(wanted)


def hit_at_k(
    ranked: Sequence[str], relevant: Iterable[str], k: int
) -> float:
    """1 when any expected id is in the first *k* results, else 0."""
    wanted = _wanted(relevant, "a hit")
    return 1.0 if set(ranked[: max(0, k)]) & wanted else 0.0


def ndcg_at_k(
    ranked: Sequence[str], relevant: Iterable[str], k: int
) -> float:
    """Binary nDCG: position discounts gains, and the ideal puts them first."""
    wanted = _wanted(relevant, "nDCG")
    cut = max(0, k)
    gains = [
        1.0 if item in wanted else 0.0 for item in ranked[:cut]
    ]
    dcg = sum(
        gain / math.log2(position + 2)
        for position, gain in enumerate(gains)
    )
    ideal = min(len(wanted), cut)
    idcg = sum(
        1.0 / math.log2(position + 2) for position in range(ideal)
    )
    return dcg / idcg if idcg else 0.0


def reciprocal_rank(
    ranked: Sequence[str], relevant: Iterable[str], *, depth: int = 20
) -> float:
    """1 / the rank of the first expected id, or 0 when none is in depth."""
    wanted = _wanted(relevant, "MRR")
    for position, item in enumerate(ranked[: max(0, depth)], 1):
        if item in wanted:
            return 1.0 / position
    return 0.0


def _wanted(relevant: Iterable[str], metric: str) -> frozenset[str]:
    wanted = frozenset(relevant)
    if not wanted:
        raise ValueError(f"{metric} needs at least one relevant id")
    return wanted


def percentile(values: Sequence[float], p: float) -> float:
    """The nearest-rank percentile, so a small sample still has a p95."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil((max(0.0, min(100.0, p)) / 100.0) * len(ordered))
    return ordered[max(0, rank - 1)]


@dataclass(frozen=True, slots=True)
class Score:
    """One scored query's four metrics."""

    recall: float
    hit: float
    ndcg: float
    mrr: float


@dataclass(frozen=True, slots=True)
class MetricSet:
    """What a bucket of scored queries concluded, as averages."""

    count: int
    recall_at_5: float
    hit_at_5: float
    ndcg_at_10: float
    mrr: float

    @classmethod
    def of(cls, scores: Sequence[Score]) -> "MetricSet":
        count = len(scores)
        if not count:
            return cls(0, 0.0, 0.0, 0.0, 0.0)
        return cls(
            count,
            sum(score.recall for score in scores) / count,
            sum(score.hit for score in scores) / count,
            sum(score.ndcg for score in scores) / count,
            sum(score.mrr for score in scores) / count,
        )

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "recallAt5": round(self.recall_at_5, 3),
            "hitAt5": round(self.hit_at_5, 3),
            "ndcgAt10": round(self.ndcg_at_10, 3),
            "mrr": round(self.mrr, 3),
        }
