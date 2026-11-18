"""Paired bootstrap confidence intervals for metric deltas.

Queries are the resampling unit: a variant and its reference are scored on the
same queries, so resampling query indices keeps the pairing. The interval is a
percentile interval over the mean delta; no p-value is produced, because the
sample is small and a p-value would read as more certainty than the data has.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from evals.rag.metrics import percentile

__all__ = [
    "BootstrapDelta",
    "DEFAULT_LEVEL",
    "DEFAULT_RESAMPLES",
    "DEFAULT_SEED",
    "paired_bootstrap_delta",
]

DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 20261119
DEFAULT_LEVEL = 0.95


@dataclass(frozen=True, slots=True)
class BootstrapDelta:
    """The mean paired delta and its percentile interval."""

    mean: float
    low: float
    high: float
    n: int
    resamples: int
    seed: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "mean": round(self.mean, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
            "n": self.n,
            "resamples": self.resamples,
            "seed": self.seed,
        }

    def cell(self) -> str:
        """A compact `mean [low, high]` cell for the Markdown report."""
        return (
            f"{self.mean:+.3f} "
            f"[{self.low:+.3f}, {self.high:+.3f}]"
        )


def paired_bootstrap_delta(
    values: Sequence[float],
    baseline: Sequence[float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_LEVEL,
) -> BootstrapDelta:
    """The mean of *values* − *baseline*, with a paired percentile CI."""
    if len(values) != len(baseline):
        raise ValueError(
            "paired bootstrap needs the same number of values and baseline"
        )
    if resamples <= 0:
        raise ValueError("resamples must be positive")
    if not 0.0 < level < 1.0:
        raise ValueError("level must be between 0 and 1")
    count = len(values)
    if count == 0:
        return BootstrapDelta(
            mean=0.0,
            low=0.0,
            high=0.0,
            n=0,
            resamples=resamples,
            seed=seed,
        )
    deltas = [
        float(value) - float(base)
        for value, base in zip(values, baseline)
    ]
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += deltas[rng.randrange(count)]
        means.append(total / count)
    tail = (1.0 - level) / 2.0 * 100.0
    return BootstrapDelta(
        mean=sum(deltas) / count,
        low=percentile(means, tail),
        high=percentile(means, 100.0 - tail),
        n=count,
        resamples=resamples,
        seed=seed,
    )
