"""BM25F lexical retrieval over the chunks an index stored.

The index is built in memory from chunk records after a sync and thrown away
when the next one replaces it; it is derived data like the chunk table itself.
Two fields carry the terms: the page title, slug and heading path together, and
the chunk body. Chinese is indexed as overlapping bigrams because there are no
spaces to split on; English is indexed as lower-cased words.

A query that exactly names a page's slug or title is put ahead of every BM25
score. The old search behaved that way, and a page someone can name exactly
should not be buried by a long page that merely repeats the word.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

__all__ = [
    "Bm25Index",
    "Bm25Params",
    "LexicalDoc",
    "LexicalHit",
    "tokenize",
]

#: CJK ideographs (the common block and extension A).
_CJK = "㐀-䶿一-鿿"
_CJK_CHAR = re.compile(rf"[{_CJK}]")
#: CJK runs first, so they are not swallowed by the general word pattern.
_TOKEN_RUN = re.compile(rf"[{_CJK}]+|[^\W_]+")


def tokenize(text: str) -> tuple[str, ...]:
    """Lower-case words and overlapping CJK bigrams, in text order.

    A one-character CJK run is kept as itself; it has no bigram to produce,
    and dropping it would make a one-character query match nothing. Non-ASCII
    letters (``café``) stay whole letters rather than being cut at the accent.
    """
    folded = (text or "").casefold()
    tokens: list[str] = []
    for match in _TOKEN_RUN.finditer(folded):
        run = match.group(0)
        if not _CJK_CHAR.fullmatch(run[0]):
            tokens.append(run)
        elif len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(
                run[index : index + 2]
                for index in range(len(run) - 1)
            )
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class Bm25Params:
    """The ranking knobs; the defaults are the ones the design starts from."""

    k1: float = 1.2
    b: float = 0.75
    #: A hit in title/slug/heading is worth twice one in the body by default.
    title_weight: float = 2.0
    body_weight: float = 1.0
    #: Added for visibility; the exact tier, not this number, guarantees order.
    exact_bonus: float = 10.0

    def __post_init__(self) -> None:
        if self.k1 < 0:
            raise ValueError("k1 cannot be negative")
        if not 0.0 <= self.b <= 1.0:
            raise ValueError("b must be between 0 and 1")
        if self.title_weight <= 0 or self.body_weight <= 0:
            raise ValueError("field weights must be positive")
        if self.exact_bonus < 0:
            raise ValueError("the exact bonus cannot be negative")


@dataclass(frozen=True, slots=True)
class LexicalDoc:
    """One chunk as the lexical index needs it.

    The title is not stored in the chunks table; the caller supplies it from the
    page, or leaves it empty when only the slug and heading path are known.
    """

    chunk_id: str
    page_id: str
    kind: str
    chunk_text: str
    heading_path: str = ""
    title: str = ""
    chunk_index: int = 0

    @classmethod
    def from_row(
        cls, row: Mapping[str, object], *, title: str = ""
    ) -> "LexicalDoc":
        """Build from one ``chunks`` table row plus the page's title."""
        return cls(
            chunk_id=str(row["chunk_id"]),
            page_id=str(row["page_id"]),
            kind=str(row["kind"]),
            chunk_index=int(row.get("chunk_index", 0) or 0),
            chunk_text=str(row.get("chunk_text", "")),
            heading_path=str(row.get("heading_path", "")),
            title=title,
        )


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """One page candidate, represented by its best chunk.

    The search tool returns pages, and the later fusion joins BM25 and vector
    candidates by page; a page contributes its highest-scoring chunk rather
    than one candidate per chunk.
    """

    chunk_id: str
    page_id: str
    kind: str
    chunk_index: int
    chunk_text: str
    heading_path: str
    score: float
    matched: tuple[str, ...]
    exact: bool


@dataclass(frozen=True, slots=True)
class _TermFreq:
    title: int
    body: int


@dataclass(frozen=True, slots=True)
class _Doc:
    lexical: LexicalDoc
    title_factor: float
    body_factor: float

    @property
    def kind(self) -> str:
        return self.lexical.kind


class Bm25Index:
    """An immutable in-memory BM25F index over chunk records.

    Queries return one candidate per page, represented by the page's best
    chunk; this keeps a long page with many chunks from filling the candidate
    list on its own.
    """

    def __init__(
        self,
        docs: Iterable[LexicalDoc],
        *,
        params: Bm25Params | None = None,
    ) -> None:
        self._params = params or Bm25Params()
        collected = list(docs)
        counts: list[tuple[LexicalDoc, Counter[str], Counter[str]]] = []
        seen: set[str] = set()
        title_lengths: list[int] = []
        body_lengths: list[int] = []
        for lexical in collected:
            if lexical.chunk_id in seen:
                raise ValueError(
                    f"duplicate chunk id: {lexical.chunk_id}"
                )
            seen.add(lexical.chunk_id)
            title_counts = Counter(tokenize(_title_text(lexical)))
            body_counts = Counter(tokenize(lexical.chunk_text))
            title_lengths.append(sum(title_counts.values()))
            body_lengths.append(sum(body_counts.values()))
            counts.append((lexical, title_counts, body_counts))

        total = len(collected)
        average_title = (
            sum(title_lengths) / total if total else 0.0
        )
        average_body = sum(body_lengths) / total if total else 0.0
        self._total = total
        self._docs: dict[str, _Doc] = {}
        self._postings: dict[str, dict[str, _TermFreq]] = {}
        self._by_slug: dict[str, set[str]] = {}
        self._by_title: dict[str, set[str]] = {}
        b = self._params.b
        for lexical, title_counts, body_counts in counts:
            self._docs[lexical.chunk_id] = _Doc(
                lexical=lexical,
                title_factor=_length_factor(
                    sum(title_counts.values()), average_title, b
                ),
                body_factor=_length_factor(
                    sum(body_counts.values()), average_body, b
                ),
            )
            for term in set(title_counts) | set(body_counts):
                self._postings.setdefault(term, {})[
                    lexical.chunk_id
                ] = _TermFreq(
                    title=title_counts.get(term, 0),
                    body=body_counts.get(term, 0),
                )
            _add_exact(self._by_slug, lexical.page_id, lexical.chunk_id)
            leaf = lexical.page_id.rsplit("/", 1)[-1]
            if leaf != lexical.page_id:
                _add_exact(self._by_slug, leaf, lexical.chunk_id)
            _add_exact(self._by_title, lexical.title, lexical.chunk_id)

    @property
    def size(self) -> int:
        return self._total

    @property
    def params(self) -> Bm25Params:
        return self._params

    def search(
        self,
        query: str,
        *,
        limit: int = 50,
        kind: str | None = None,
    ) -> list[LexicalHit]:
        """The best chunks for *query*, exact slug/title matches first."""
        if limit <= 0:
            return []
        terms = sorted(set(tokenize(query)))
        query_norm = _normalize(query)
        exact_candidates = self._exact_candidates(query_norm, kind)
        if not terms and not exact_candidates:
            return []

        scores: dict[str, float] = {}
        matched: dict[str, set[str]] = {}
        k1 = self._params.k1
        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (self._total - df + 0.5) / (df + 0.5))
            for chunk_id, tf in postings.items():
                doc = self._docs[chunk_id]
                if kind is not None and doc.kind != kind:
                    continue
                weighted = 0.0
                for count, factor, weight in (
                    (tf.title, doc.title_factor, self._params.title_weight),
                    (tf.body, doc.body_factor, self._params.body_weight),
                ):
                    if count and factor > 0:
                        weighted += weight * count / factor
                if weighted <= 0:
                    continue
                scores[chunk_id] = scores.get(chunk_id, 0.0) + (
                    idf * weighted * (k1 + 1) / (weighted + k1)
                )
                fields = matched.setdefault(chunk_id, set())
                if tf.title:
                    fields.add("title")
                if tf.body:
                    fields.add("body")

        exact_ids = self._exact_winners(exact_candidates, scores)
        for chunk_id in exact_ids:
            scores[chunk_id] = (
                scores.get(chunk_id, 0.0) + self._params.exact_bonus
            )

        best_per_page: dict[tuple[str, str], str] = {}
        for chunk_id in scores:
            lexical = self._docs[chunk_id].lexical
            key = (lexical.kind, lexical.page_id)
            current = best_per_page.get(key)
            if current is None or self._candidate_key(
                chunk_id, scores, exact_ids
            ) > self._candidate_key(current, scores, exact_ids):
                best_per_page[key] = chunk_id
        ordered = sorted(
            best_per_page.values(),
            key=lambda chunk_id: (
                -int(chunk_id in exact_ids),
                -scores[chunk_id],
                chunk_id,
            ),
        )[:limit]
        return [
            self._hit(chunk_id, scores, matched, exact_ids)
            for chunk_id in ordered
        ]

    def _candidate_key(
        self,
        chunk_id: str,
        scores: dict[str, float],
        exact_ids: set[str],
    ) -> tuple[int, float, int, str]:
        return (
            int(chunk_id in exact_ids),
            scores[chunk_id],
            -self._docs[chunk_id].lexical.chunk_index,
            chunk_id,
        )

    def _exact_candidates(
        self, query_norm: str, kind: str | None
    ) -> set[str]:
        if not query_norm:
            return set()
        found = set(self._by_slug.get(query_norm, ()))
        found.update(self._by_title.get(query_norm, ()))
        if kind is not None:
            found = {
                chunk_id
                for chunk_id in found
                if self._docs[chunk_id].kind == kind
            }
        return found

    def _exact_winners(
        self, candidates: set[str], scores: dict[str, float]
    ) -> set[str]:
        """One representative chunk per exactly named page.

        A page has an exact name, not each of its chunks; marking all of them
        exact would let one long page fill the whole candidate list.
        """
        groups: dict[tuple[str, str], list[str]] = {}
        for chunk_id in candidates:
            lexical = self._docs[chunk_id].lexical
            groups.setdefault(
                (lexical.kind, lexical.page_id), []
            ).append(chunk_id)
        winners: set[str] = set()
        for group in groups.values():
            scored = [
                chunk_id for chunk_id in group if chunk_id in scores
            ]
            if scored:
                winners.add(
                    max(
                        scored,
                        key=lambda chunk_id: (
                            scores[chunk_id],
                            -self._docs[chunk_id].lexical.chunk_index,
                            chunk_id,
                        ),
                    )
                )
            else:
                winners.add(
                    min(
                        group,
                        key=lambda chunk_id: (
                            self._docs[chunk_id].lexical.chunk_index,
                            chunk_id,
                        ),
                    )
                )
        return winners

    def _hit(
        self,
        chunk_id: str,
        scores: dict[str, float],
        matched: dict[str, set[str]],
        exact_ids: set[str],
    ) -> LexicalHit:
        lexical = self._docs[chunk_id].lexical
        return LexicalHit(
            chunk_id=lexical.chunk_id,
            page_id=lexical.page_id,
            kind=lexical.kind,
            chunk_index=lexical.chunk_index,
            chunk_text=lexical.chunk_text,
            heading_path=lexical.heading_path,
            score=round(scores[chunk_id], 6),
            matched=tuple(sorted(matched.get(chunk_id, ()))),
            exact=chunk_id in exact_ids,
        )


def _title_text(lexical: LexicalDoc) -> str:
    return " ".join(
        part
        for part in (
            lexical.title,
            lexical.page_id,
            lexical.heading_path,
        )
        if part
    )


def _length_factor(length: int, average: float, b: float) -> float:
    """BM25's length-normalization factor for one field of one document."""
    if average <= 0:
        return 1.0
    return (1 - b) + b * (length / average)


def _normalize(value: str | None) -> str:
    return (value or "").strip().casefold()


def _add_exact(
    mapping: dict[str, set[str]], value: str, chunk_id: str
) -> None:
    key = _normalize(value)
    if key:
        mapping.setdefault(key, set()).add(chunk_id)
