"""Vector retrieval over the stored chunks, aggregated back to pages.

A page can produce several chunks, and the search tool returns pages, so the
vector candidates are merged per page before they meet BM25: the best chunk's
cosine similarity leads, and the average of the remaining chunks adds a small
bonus, capped so the page can never score above a perfect cosine of one.

The query is embedded by the same client the index used, with the query-side
instruction prefix it applies; the stored document vectors are already L2
normalized. The table is read through LanceDB's cosine metric, so a distance
``d`` is the similarity ``1 - d``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import lancedb

from noteagent.rag.chunker import KIND_NOTE, KIND_WIKI
from noteagent.rag.config import EmbeddingError
from noteagent.rag.embedding import EmbeddingClient
from noteagent.rag.filters import sql_string
from noteagent.rag.index import CHUNKS_TABLE

__all__ = [
    "ChunkScore",
    "VectorHit",
    "VectorRetriever",
    "aggregate_pages",
]

#: The share of the rest-of-page average added to the best chunk's similarity.
DEFAULT_TAIL_WEIGHT = 0.3
#: The kinds the stored table may hold; production narrows this to wiki.
DEFAULT_KINDS = (KIND_WIKI, KIND_NOTE)


@dataclass(frozen=True, slots=True)
class ChunkScore:
    """One chunk's cosine similarity to the query."""

    chunk_id: str
    page_id: str
    kind: str
    chunk_index: int
    chunk_text: str
    heading_path: str
    similarity: float

    @property
    def page_key(self) -> tuple[str, str]:
        """A page is a (kind, page_id) pair; a note and a wiki page differ."""
        return (self.kind, self.page_id)


@dataclass(frozen=True, slots=True)
class VectorHit:
    """One page candidate, with the chunk that represented it."""

    page_id: str
    kind: str
    score: float
    best: ChunkScore


def aggregate_pages(
    chunks: Iterable[ChunkScore],
    *,
    tail_weight: float = DEFAULT_TAIL_WEIGHT,
) -> list[VectorHit]:
    """Merge chunk similarities into one score per page.

    ``score = best + min(tail_weight * mean(rest), 1 - best)``. A page with a
    single chunk therefore keeps that chunk's similarity, and the bonus can
    never push the page above a perfect cosine. A negative rest average lowers
    the page slightly, as the literal formula says.
    """
    if tail_weight < 0:
        raise ValueError("tail_weight cannot be negative")
    groups: dict[tuple[str, str], list[ChunkScore]] = {}
    for item in chunks:
        groups.setdefault(item.page_key, []).append(item)

    hits: list[VectorHit] = []
    for (kind, page_id), items in groups.items():
        items.sort(
            key=lambda item: (
                -item.similarity,
                item.chunk_index,
                item.chunk_id,
            )
        )
        best = items[0]
        best_similarity = _clamp_similarity(best.similarity)
        tail = (
            sum(item.similarity for item in items[1:]) / (len(items) - 1)
            if len(items) > 1
            else 0.0
        )
        bonus = min(
            tail_weight * tail,
            max(0.0, 1.0 - best_similarity),
        )
        hits.append(
            VectorHit(
                page_id=page_id,
                kind=kind,
                score=best_similarity + bonus,
                best=best,
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.page_id, hit.kind))
    return hits


class VectorRetriever:
    """Read the stored vectors for a query and return page candidates."""

    def __init__(
        self,
        path: Path | str,
        embedder: EmbeddingClient,
        *,
        tail_weight: float = DEFAULT_TAIL_WEIGHT,
    ) -> None:
        if embedder.dim <= 0:
            raise ValueError("the embedding dimension must be positive")
        if tail_weight < 0:
            raise ValueError("tail_weight cannot be negative")
        self._path = Path(path)
        self._embedder = embedder
        self._dim = embedder.dim
        self._tail_weight = tail_weight

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        over_fetch: int = 150,
        kinds: Sequence[str] | None = None,
    ) -> list[VectorHit]:
        """The best pages for *query*, with the chunks that represented them.

        ``kinds`` narrows the search; ``None`` means both stored kinds, which
        is the experiment scope. Production passes ``("wiki",)``.
        """
        if limit <= 0 or over_fetch <= 0:
            return []
        if not (query or "").strip():
            return []
        vector = self._embedder.embed([query], query=True)[0]
        if len(vector) != self._dim:
            raise EmbeddingError(
                f"expected {self._dim} query dimensions, got {len(vector)}"
            )
        rows = self._fetch(vector, over_fetch, kinds)
        scores = [_chunk_score(row) for row in rows]
        return aggregate_pages(
            scores, tail_weight=self._tail_weight
        )[:limit]

    def _fetch(
        self,
        vector: Sequence[float],
        over_fetch: int,
        kinds: Sequence[str] | None,
    ) -> list[dict]:
        if not self._path.is_dir():
            return []
        try:
            db = lancedb.connect(self._path)
            if CHUNKS_TABLE not in set(db.list_tables().tables):
                return []
            search = (
                db.open_table(CHUNKS_TABLE)
                .search(list(vector))
                .metric("cosine")
            )
            values = [
                kind
                for kind in (
                    DEFAULT_KINDS
                    if kinds is None
                    else _kind_values(kinds)
                )
                if kind
            ]
            if not values:
                return []
            escaped = ", ".join(sql_string(kind) for kind in values)
            search = search.where(f"kind IN ({escaped})")
            return search.limit(over_fetch).to_list()
        except Exception:  # noqa: BLE001 - a derived index can be missing
            return []


def _kind_values(kinds: Sequence[str] | str) -> tuple[str, ...]:
    """One kind or a sequence; a bare string is one kind, not its letters."""
    if isinstance(kinds, str):
        return (kinds,)
    return tuple(kinds)


def _chunk_score(row: dict) -> ChunkScore:
    similarity = 1.0 - float(row["_distance"])
    return ChunkScore(
        chunk_id=str(row["chunk_id"]),
        page_id=str(row["page_id"]),
        kind=str(row["kind"]),
        chunk_index=int(row["chunk_index"]),
        chunk_text=str(row["chunk_text"]),
        heading_path=str(row["heading_path"]),
        similarity=_clamp_similarity(similarity),
    )


def _clamp_similarity(value: float) -> float:
    return max(-1.0, min(1.0, value))
