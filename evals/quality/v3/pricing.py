"""Approximate USD per million tokens, by model name.

A starting point for the cost column. Pass the CLI's
--input-cost-per-mtok / --output-cost-per-mtok when the model or the
provider's price differs; an unknown model reports cost as n/a instead of
guessing.
"""

from __future__ import annotations

#: (input, output) USD per million tokens.
PRICES: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def price_for(model: str) -> tuple[float, float] | None:
    return PRICES.get((model or "").strip().lower())


__all__ = ["PRICES", "price_for"]
