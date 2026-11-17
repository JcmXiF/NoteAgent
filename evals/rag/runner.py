"""Run a retrieval variant over the eval set and score what came back.

The run is deliberately thin: one call per query, timed, and scored against the
annotated expectation. Nothing here reads the network or a model, so the only
thing a difference between runs can come from is the variant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from evals.rag.dataset import (
    Dataset,
    QUERY_TYPES,
    RagQuery,
    check_corpus,
)
from evals.rag.metrics import (
    MetricSet,
    Score,
    hit_at_k,
    ndcg_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
)
from evals.rag.variants import Variant, get_variant
from evals.report import utc_now

from noteagent.project.layout import ProjectLayout

__all__ = [
    "Evaluation",
    "GapSummary",
    "Latency",
    "QueryResult",
    "evaluate",
    "run_queries",
]

#: Where each metric cuts the ranked list.
RECALL_K = 5
NDCG_K = 10


@dataclass(frozen=True, slots=True)
class QueryResult:
    """One query's ranked result, its latency, and how it scored."""

    query: RagQuery
    ranked: tuple[str, ...]
    latency_ms: float

    @property
    def relevant(self) -> frozenset[str]:
        return frozenset(self.query.expected)

    def score(self) -> Score:
        """The four metrics for a query that expects at least one result."""
        return Score(
            recall=recall_at_k(self.ranked, self.relevant, RECALL_K),
            hit=hit_at_k(self.ranked, self.relevant, RECALL_K),
            ndcg=ndcg_at_k(self.ranked, self.relevant, NDCG_K),
            mrr=reciprocal_rank(
                self.ranked, self.relevant, depth=len(self.ranked)
            ),
        )

    @property
    def first_rank(self) -> int | None:
        """The 1-based rank of the first expected result, if any."""
        for position, item in enumerate(self.ranked, 1):
            if item in self.relevant:
                return position
        return None

    @property
    def abstained(self) -> bool | None:
        """For a gap query, whether the system returned nothing."""
        if not self.query.is_gap:
            return None
        return not self.ranked

    @property
    def outcome(self) -> str:
        """One word for the per-query report: what the query concluded."""
        if self.query.is_gap:
            return "abstained" if not self.ranked else "false_positive"
        return "hit" if self.first_rank is not None else "miss"


@dataclass(frozen=True, slots=True)
class GapSummary:
    """How a set of unanswerable queries was handled."""

    count: int
    abstained: int

    @property
    def rate(self) -> float:
        return self.abstained / self.count if self.count else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "abstained": self.abstained,
            "rate": round(self.rate, 3),
        }


@dataclass(frozen=True, slots=True)
class Latency:
    """The run's per-query latency, as two percentiles."""

    p50_ms: float
    p95_ms: float

    def as_dict(self) -> dict[str, float]:
        return {
            "p50Ms": round(self.p50_ms, 3),
            "p95Ms": round(self.p95_ms, 3),
        }


@dataclass(frozen=True, slots=True)
class Evaluation:
    """A finished run: its variant, its results, and the metrics over them."""

    variant: Variant
    dataset: Dataset
    corpus: Path
    results: tuple[QueryResult, ...]
    started_at: str
    duration_s: float

    @property
    def scored(self) -> tuple[QueryResult, ...]:
        return tuple(
            result
            for result in self.results
            if not result.query.is_gap
        )

    @property
    def metrics(self) -> MetricSet:
        return MetricSet.of([result.score() for result in self.scored])

    def metrics_by_type(self) -> dict[str, MetricSet]:
        buckets: dict[str, list[Score]] = {}
        for result in self.scored:
            buckets.setdefault(result.query.type, []).append(
                result.score()
            )
        return {
            name: MetricSet.of(buckets[name])
            for name in QUERY_TYPES
            if name in buckets
        }

    @property
    def gap(self) -> GapSummary:
        gaps = [
            result
            for result in self.results
            if result.query.is_gap
        ]
        return GapSummary(
            count=len(gaps),
            abstained=sum(1 for result in gaps if result.abstained),
        )

    @property
    def latency(self) -> Latency:
        values = [result.latency_ms for result in self.results]
        return Latency(
            p50_ms=percentile(values, 50),
            p95_ms=percentile(values, 95),
        )


def run_queries(
    queries: tuple[RagQuery, ...] | list[RagQuery],
    corpus_root: Path | str,
    variant: Variant,
) -> tuple[QueryResult, ...]:
    """Ask *variant* every query against the corpus, timing each call."""
    layout = ProjectLayout(root=Path(corpus_root).resolve())
    results: list[QueryResult] = []
    for query in queries:
        started = time.perf_counter()
        retrieval = variant.retrieve(layout, query.query)
        elapsed_ms = (time.perf_counter() - started) * 1_000.0
        results.append(
            QueryResult(
                query=query,
                ranked=retrieval.for_type(query.type),
                latency_ms=elapsed_ms,
            )
        )
    return tuple(results)


def evaluate(
    dataset: Dataset,
    corpus_root: Path | str,
    variant_name: str = "lexical",
) -> Evaluation:
    """Check the corpus, run the variant, and return the scored run."""
    variant = get_variant(variant_name)
    corpus = Path(corpus_root).resolve()
    check_corpus(dataset, corpus)
    started_at = utc_now()
    started = time.perf_counter()
    results = run_queries(dataset.queries, corpus, variant)
    return Evaluation(
        variant=variant,
        dataset=dataset,
        corpus=corpus,
        results=results,
        started_at=started_at,
        duration_s=time.perf_counter() - started,
    )
