"""The recovery eval's arithmetic: rates, counts and their denominators.

A ``missed`` or ``infra`` run is not a product sample: it is dropped from every
denominator, so an unreachable crash boundary neither inflates nor deflates a
metric. Ratios keep their numerator and denominator, so the report shows the
exact sample instead of a rounded score.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "EXCLUDED_STATUSES",
    "Metrics",
    "Ratio",
    "ScenarioRun",
    "compute_metrics",
    "percentile",
]

#: A run that never reached the product path: the kill missed its boundary, or
#: the harness itself failed. Neither says anything about recovery behaviour.
EXCLUDED_STATUSES = frozenset({"missed", "infra"})


@dataclass(frozen=True, slots=True)
class Ratio:
    """A share that keeps the sample it was computed from."""

    numerator: int
    denominator: int

    @property
    def present(self) -> bool:
        return self.denominator > 0

    @property
    def value(self) -> float | None:
        if not self.denominator:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    """One repetition of one scenario, as the metrics need it.

    Fields are optional because the recovery and rollback suites measure
    different things; the reducer reads only the fields a suite fills in.
    """

    scenario: str
    suite: str
    rep: int
    status: str = "passed"
    intended_boundary: str = ""
    observed_boundary: str = ""
    detail: str = ""

    # Recovery metrics.
    interrupted_expected: bool = False
    interrupted_detected: bool | None = None
    normal_completion: bool = False
    false_interrupted: bool | None = None
    preserved_entries: int | None = None
    committed_entries: int | None = None
    replayed_tools: int | None = None
    resume_attempted: bool = False
    resume_succeeded: bool | None = None
    budget_exceeded: bool | None = None
    terminal: bool | None = None
    recovery_seconds: float | None = None

    # Rollback metrics.
    classified_correct: int | None = None
    classified_total: int | None = None
    user_changed_pages: int | None = None
    user_pages_overwritten: int | None = None
    restore_attempted: bool = False
    restore_succeeded: bool | None = None
    repeat_restore: bool = False
    extra_undo_after_repeat: int | None = None

    @property
    def excluded(self) -> bool:
        return self.status in EXCLUDED_STATUSES


@dataclass(frozen=True, slots=True)
class Metrics:
    """Every product metric, each with its own sample."""

    detection: Ratio
    false_interrupt: Ratio
    preservation: Ratio
    replay_count: int
    replay_runs: int
    resume_success: Ratio
    recovery_p50_ms: float
    recovery_p95_ms: float
    recovery_samples: int
    classification: Ratio
    overwrite: Ratio
    idempotency: Ratio
    restore_success: Ratio
    budget_completion: Ratio


def compute_metrics(runs: Iterable[ScenarioRun]) -> Metrics:
    """Reduce runs to product metrics, dropping missed and infra runs."""
    product = [item for item in runs if not item.excluded]
    latencies = [
        item.recovery_seconds
        for item in product
        if item.recovery_seconds is not None
    ]
    return Metrics(
        detection=_ratio(
            product,
            sample=lambda item: (
                item.interrupted_expected
                and item.interrupted_detected is not None
            ),
            hit=lambda item: item.interrupted_detected is True,
        ),
        false_interrupt=_ratio(
            product,
            sample=lambda item: (
                item.normal_completion
                and item.false_interrupted is not None
            ),
            hit=lambda item: item.false_interrupted is True,
        ),
        preservation=Ratio(
            sum(
                item.preserved_entries
                for item in product
                if item.resume_attempted
                and item.preserved_entries is not None
                and item.committed_entries is not None
            ),
            sum(
                item.committed_entries
                for item in product
                if item.resume_attempted
                and item.preserved_entries is not None
                and item.committed_entries is not None
            ),
        ),
        replay_count=sum(item.replayed_tools or 0 for item in product),
        replay_runs=sum(1 for item in product if item.replayed_tools),
        resume_success=_ratio(
            product,
            sample=lambda item: (
                item.resume_attempted
                and item.resume_succeeded is not None
            ),
            hit=lambda item: (
                item.resume_succeeded is True
                and item.budget_exceeded is not True
            ),
        ),
        recovery_p50_ms=percentile(latencies, 50) * 1000.0,
        recovery_p95_ms=percentile(latencies, 95) * 1000.0,
        recovery_samples=len(latencies),
        classification=_sum_ratio(
            product, "classified_correct", "classified_total"
        ),
        overwrite=_sum_ratio(
            product, "user_pages_overwritten", "user_changed_pages"
        ),
        idempotency=_ratio(
            product,
            sample=lambda item: (
                item.repeat_restore
                and item.extra_undo_after_repeat is not None
            ),
            hit=lambda item: item.extra_undo_after_repeat == 0,
        ),
        restore_success=_ratio(
            product,
            sample=lambda item: (
                item.restore_attempted
                and item.restore_succeeded is not None
            ),
            hit=lambda item: item.restore_succeeded is True,
        ),
        budget_completion=_ratio(
            product,
            sample=lambda item: item.budget_exceeded is not None,
            hit=lambda item: (
                item.budget_exceeded is False and item.terminal is True
            ),
        ),
    )


def percentile(values: Sequence[float], p: float) -> float:
    """The nearest-rank percentile, so a small sample still has a p95."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil((max(0.0, min(100.0, p)) / 100.0) * len(ordered))
    return ordered[max(0, rank - 1)]


def _sum_ratio(
    runs: Sequence[ScenarioRun],
    numerator: str,
    denominator: str,
) -> Ratio:
    """A ratio of field totals over the runs where the denominator is set."""
    picked = [
        item for item in runs if getattr(item, denominator) is not None
    ]
    return Ratio(
        sum(getattr(item, numerator) or 0 for item in picked),
        sum(getattr(item, denominator) or 0 for item in picked),
    )


def _ratio(
    runs: Sequence[ScenarioRun],
    *,
    sample: Callable[[ScenarioRun], bool],
    hit: Callable[[ScenarioRun], bool],
) -> Ratio:
    picked = [item for item in runs if sample(item)]
    return Ratio(sum(1 for item in picked if hit(item)), len(picked))
