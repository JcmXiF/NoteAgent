"""Reciprocal-rank fusion of the lexical and vector page candidates.

RRF asks only for each list's order, never for the two score scales to agree.
With ``k = 60`` and a one-based rank, a page at rank 1 contributes 1/61; a page
in both lists contributes both terms. An exact slug or title is a page-level
fact from BM25, and it is a tier of its own: no amount of RRF score puts a
merely similar page above one the reader named exactly.

Fusion returns up to fifty page candidates. The search backend shows the first
twenty when reranking is off; the optional reranker consumes the fifty.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from noteagent.rag.bm25 import DEFAULT_EXACT_BONUS, LexicalHit
from noteagent.rag.types import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_RESULT_LIMIT,
    Mode,
    PageKey,
)
from noteagent.rag.vector import VectorHit

__all__ = [
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_EXACT_BONUS",
    "DEFAULT_K",
    "DEFAULT_RESULT_LIMIT",
    "FusedHit",
    "FusionResult",
    "PageKey",
    "fuse",
]

DEFAULT_K = 60


class _Ranked(Protocol):
    kind: str
    page_id: str


_HitT = TypeVar("_HitT", bound=_Ranked)


def _add_ranked(
    scores: dict[PageKey, float],
    seen: dict[PageKey, _HitT],
    hits: Sequence[_HitT],
    k: int,
) -> None:
    for rank, hit in enumerate(hits, 1):
        key = (hit.kind, hit.page_id)
        if key in seen:
            continue
        seen[key] = hit
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)


@dataclass(frozen=True, slots=True)
class FusedHit:
    """One page after fusion, with whichever side(s) found it."""

    page_id: str
    kind: str
    score: float
    exact: bool
    lexical: LexicalHit | None = None
    vector: VectorHit | None = None

    @property
    def document_text(self) -> str:
        """What the optional reranker reads: the page name and its evidence."""
        parts: list[str] = [self.page_id]
        heading = ""
        texts: list[str] = []
        if self.lexical is not None:
            heading = self.lexical.heading_path
            texts.append(self.lexical.chunk_text)
        if self.vector is not None:
            if not heading:
                heading = self.vector.best.heading_path
            if self.vector.best.chunk_text not in texts:
                texts.append(self.vector.best.chunk_text)
        if heading:
            parts.append(heading)
        parts.extend(text for text in texts if text)
        return "\n".join(part for part in parts if part)

    @property
    def primary_text(self) -> str:
        """The best evidence for this page, for the tool's excerpt."""
        if self.lexical is not None:
            return self.lexical.chunk_text
        if self.vector is not None:
            return self.vector.best.chunk_text
        return ""

    @property
    def match_label(self) -> str:
        """How the search line names this hit's match."""
        if self.lexical is not None:
            if self.exact:
                return "title"
            if self.lexical.matched:
                return "+".join(self.lexical.matched)
            return "body"
        if self.vector is not None:
            return "semantic"
        return "hybrid"


@dataclass(frozen=True, slots=True)
class FusionResult:
    """The fused candidates and which lists contributed."""

    hits: tuple[FusedHit, ...]
    mode: Mode
    lexical_hits: int = 0
    vector_hits: int = 0

    def top(
        self, limit: int = DEFAULT_RESULT_LIMIT
    ) -> tuple[FusedHit, ...]:
        """What the search tool shows when no reranker takes the candidates."""
        return self.hits[: max(0, limit)]


def fuse(
    lexical: Sequence[LexicalHit],
    vector: Sequence[VectorHit],
    *,
    k: int = DEFAULT_K,
    candidates: int = DEFAULT_CANDIDATE_LIMIT,
    exact_bonus: float = DEFAULT_EXACT_BONUS,
) -> FusionResult:
    """Fuse two ranked page lists into one, exact pages first."""
    if k <= 0:
        raise ValueError("RRF k must be positive")
    if candidates < 0:
        raise ValueError("the candidate count cannot be negative")
    if exact_bonus < 0:
        raise ValueError("the exact bonus cannot be negative")

    lexical_list = list(lexical)[:candidates]
    vector_list = list(vector)[:candidates]
    scores: dict[PageKey, float] = {}
    lexical_by_key: dict[PageKey, LexicalHit] = {}
    vector_by_key: dict[PageKey, VectorHit] = {}
    _add_ranked(scores, lexical_by_key, lexical_list, k)
    _add_ranked(scores, vector_by_key, vector_list, k)

    exact_keys = {
        key for key, hit in lexical_by_key.items() if hit.exact
    }
    ordered = sorted(
        scores,
        key=lambda key: (
            -int(key in exact_keys),
            -scores[key],
            key[1].casefold(),
            key[0],
        ),
    )[:candidates]
    hits = tuple(
        FusedHit(
            page_id=key[1],
            kind=key[0],
            score=round(
                scores[key]
                + (exact_bonus if key in exact_keys else 0.0),
                6,
            ),
            exact=key in exact_keys,
            lexical=lexical_by_key.get(key),
            vector=vector_by_key.get(key),
        )
        for key in ordered
    )
    return FusionResult(
        hits=hits,
        mode="hybrid" if vector_list else "lexical",
        lexical_hits=len(lexical_list),
        vector_hits=len(vector_list),
    )
