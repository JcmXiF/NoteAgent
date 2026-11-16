"""What the retrieval backend hands the search tool.

Kept free of LanceDB and HTTP imports so the agent's tool module can speak this
shape without loading the index at startup, and so a degraded result and a
healthy one travel through the same type.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RagHit", "RagSearchResult"]


@dataclass(frozen=True, slots=True)
class RagHit:
    """One page locator and the excerpt that represents it."""

    slug: str
    excerpt: str
    match: str
    score: float


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    """The hybrid or lexical result, and why it is lexical when it is."""

    hits: tuple[RagHit, ...]
    mode: str
    degraded: bool = False
    warnings: tuple[str, ...] = ()
