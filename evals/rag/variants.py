"""The retrieval variants an eval can run, and what each one returns.

A variant is a two-phase thing: ``prepare`` builds or looks up whatever it needs
(an index, usually) and returns a :class:`Retriever`; the harness then asks that
retriever one query at a time. Keeping the phases apart is what lets an index be
built once per configuration and its cost be reported separately from query
latency.

The Stage 0 baseline is ``lexical``: the search the project already runs, so the
number it produces is the number a reader gets today, not a reimplementation that
might drift from it. It needs no index and returns through the same lifecycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol, cast

from evals.rag.base_index import BaseIndex, build_base_index
from evals.rag.dataset import note_ref, page_ref

from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import DEFAULT_EXACT_BONUS
from noteagent.rag.chunker import KIND_NOTE, KIND_WIKI
from noteagent.rag.embedding import EmbeddingClient
from noteagent.rag.fusion import DEFAULT_K, fuse
from noteagent.wiki.local_search import search_project

__all__ = [
    "CHANNEL_INDEX_KEY",
    "DEPTH",
    "EvalContext",
    "FnRetriever",
    "IndexBuild",
    "IndexBuildRecord",
    "IndexCache",
    "IndexKey",
    "Retrieval",
    "Retriever",
    "Scope",
    "UnknownVariant",
    "VARIANTS",
    "Variant",
    "base_index_builder",
    "filter_scope",
    "get_variant",
]

#: How many results a variant may hand back, mirroring the ``search`` tool's
#: own cap. Metrics cut at 5 and 10; MRR looks through this whole list.
DEPTH = 20


class Scope(str, Enum):
    """Which kinds of result a run is allowed to return."""

    WIKI = "wiki"
    WIKI_NOTES = "wiki+notes"


@dataclass(frozen=True, slots=True)
class Retrieval:
    """One variant's answer: ranked page slugs and ranked note ids."""

    pages: tuple[str, ...]
    notes: tuple[str, ...]

    def for_type(self, query_type: str) -> tuple[str, ...]:
        """The side of the result a query is scored on, as namespaced ids."""
        if query_type == "notes":
            return tuple(note_ref(note_id) for note_id in self.notes)
        return tuple(page_ref(slug) for slug in self.pages)


def filter_scope(retrieval: Retrieval, scope: Scope) -> Retrieval:
    """Drop the kinds the run's scope does not allow."""
    scope = Scope(scope)
    if scope is Scope.WIKI_NOTES:
        return retrieval
    return Retrieval(pages=retrieval.pages, notes=())


@dataclass(frozen=True, slots=True)
class IndexKey:
    """The configuration that decides whether an index can be reused."""

    chunking: str
    embedding_model: str
    embedding_dim: int


#: The reference index configuration the channel rows share.
CHANNEL_INDEX_KEY = IndexKey(
    chunking="heading",
    embedding_model="qwen3-embedding:0.6b",
    embedding_dim=1024,
)


@dataclass(frozen=True, slots=True)
class IndexBuild:
    """One index build and the cost it incurred.

    ``index`` is opaque to the harness: the eval knows how to ask a variant for
    it, not what shape it has. ``seconds`` may be zero when the builder does not
    measure itself; the cache fills it in.
    """

    index: object
    seconds: float
    embedding_calls: int
    embedding_tokens: int
    chunk_count: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class IndexBuildRecord:
    """The cost of one index build, without the index payload."""

    key: IndexKey
    seconds: float
    embedding_calls: int
    embedding_tokens: int
    chunk_count: int
    size_bytes: int

    @classmethod
    def of(cls, key: IndexKey, build: IndexBuild) -> IndexBuildRecord:
        """The record for one build, without carrying its index payload."""
        return cls(
            key=key,
            seconds=build.seconds,
            embedding_calls=build.embedding_calls,
            embedding_tokens=build.embedding_tokens,
            chunk_count=build.chunk_count,
            size_bytes=build.size_bytes,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "chunking": self.key.chunking,
            "embeddingModel": self.key.embedding_model,
            "embeddingDim": self.key.embedding_dim,
            "seconds": round(self.seconds, 3),
            "embeddingCalls": self.embedding_calls,
            "embeddingTokens": self.embedding_tokens,
            "chunkCount": self.chunk_count,
            "sizeBytes": self.size_bytes,
        }


class IndexCache:
    """Builds an index at most once per key, and remembers what each build cost."""

    def __init__(
        self,
        builder: Callable[[IndexKey, ProjectLayout], IndexBuild] | None = None,
    ) -> None:
        self._builder = builder
        self._entries: dict[IndexKey, IndexBuild] = {}

    def get(self, key: IndexKey, layout: ProjectLayout) -> IndexBuild:
        """The index for *key*, building it on first use."""
        found = self._entries.get(key)
        if found is not None:
            return found
        if self._builder is None:
            raise RuntimeError(f"no index builder is registered for {key}")
        started = time.perf_counter()
        build = self._builder(key, layout)
        elapsed = time.perf_counter() - started
        if build.seconds <= 0:
            build = replace(build, seconds=elapsed)
        self._entries[key] = build
        return build

    def records(self) -> tuple[IndexBuildRecord, ...]:
        """The builds this cache performed, in first-use order."""
        return tuple(
            IndexBuildRecord.of(key, build)
            for key, build in self._entries.items()
        )

    def mark(self) -> int:
        """A snapshot for attributing only the builds a later run adds."""
        return len(self._entries)

    def records_since(self, mark: int) -> tuple[IndexBuildRecord, ...]:
        """The builds added after *mark*, for one run's cost report."""
        return self.records()[max(0, mark):]


def base_index_builder(
    embedder: EmbeddingClient,
) -> Callable[[IndexKey, ProjectLayout], IndexBuild]:
    """A cache builder that chunks, embeds, and indexes one corpus."""

    def build(key: IndexKey, layout: ProjectLayout) -> IndexBuild:
        if key.embedding_dim != embedder.dim:
            raise ValueError(
                f"the index key says {key.embedding_dim} dimensions, "
                f"the embedder says {embedder.dim}"
            )
        before = _embedding_usage(embedder)
        base = build_base_index(layout, embedder)
        after = _embedding_usage(embedder)
        return IndexBuild(
            index=base,
            seconds=0.0,
            embedding_calls=max(0, after[0] - before[0]),
            embedding_tokens=max(0, after[1] - before[1]),
            chunk_count=len(base.chunks),
            size_bytes=base.size_bytes,
        )

    return build


def _embedding_usage(embedder: EmbeddingClient) -> tuple[int, int]:
    """How many embedding requests and tokens the client has spent so far."""
    usage = getattr(embedder, "usage", None)
    if usage is None:
        return 0, 0
    return int(usage.calls), int(usage.tokens)


class Retriever(Protocol):
    """What a prepared variant hands the harness: one query at a time."""

    def retrieve(self, query: str) -> Retrieval:
        """The ranked result for *query*."""


@dataclass(frozen=True, slots=True)
class FnRetriever:
    """A retriever built from a plain function."""

    fn: Callable[[str], Retrieval]

    def retrieve(self, query: str) -> Retrieval:
        return self.fn(query)


@dataclass(frozen=True, slots=True)
class EvalContext:
    """What a variant needs to prepare itself for one run."""

    layout: ProjectLayout
    scope: Scope
    indexes: IndexCache
    index_key: IndexKey | None = None
    embedder: EmbeddingClient | None = None


@dataclass(frozen=True, slots=True)
class Variant:
    """A named retrieval strategy the harness can prepare and compare."""

    name: str
    description: str
    prepare: Callable[[EvalContext], Retriever]
    index_key: IndexKey | None = None


class UnknownVariant(ValueError):
    """No variant has the requested name."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"unknown variant {name!r}; try: {', '.join(VARIANTS)}"
        )


def _lexical(context: EvalContext) -> Retriever:
    layout = context.layout

    def retrieve(query: str) -> Retrieval:
        found = search_project(layout, query, per_kind=DEPTH)
        pages = cast(list[dict[str, object]], found["pages"])
        notes = cast(list[dict[str, object]], found["notes"])
        return Retrieval(
            pages=tuple(str(item["slug"]) for item in pages),
            notes=tuple(str(item["id"]) for item in notes),
        )

    return FnRetriever(retrieve)


def _base(context: EvalContext) -> BaseIndex:
    if context.index_key is None:
        raise RuntimeError("this variant needs an index key")
    build = context.indexes.get(context.index_key, context.layout)
    return cast(BaseIndex, build.index)


class _Ranked(Protocol):
    """Any hit with the two fields the vocabulary split needs."""

    kind: str
    page_id: str


def _retrieval(hits: Sequence[_Ranked]) -> Retrieval:
    return Retrieval(
        pages=tuple(
            hit.page_id for hit in hits if hit.kind == KIND_WIKI
        ),
        notes=tuple(
            hit.page_id for hit in hits if hit.kind == KIND_NOTE
        ),
    )


def _bm25(context: EvalContext) -> Retriever:
    base = _base(context)

    def retrieve(query: str) -> Retrieval:
        return _retrieval(base.bm25.search(query, limit=DEPTH))

    return FnRetriever(retrieve)


def _vector(context: EvalContext) -> Retriever:
    base = _base(context)
    embedder = context.embedder
    if embedder is None:
        raise RuntimeError(
            "the vector variant needs an embedding client"
        )

    def retrieve(query: str) -> Retrieval:
        exact = base.exact_pages(query)
        # An exact page must be considered before the result cap: a named
        # page outside the vector top-N is still the page the reader asked
        # for, the way BM25's rank-independent exact candidates are.
        hits = base.vector_hits(
            query,
            embedder,
            limit=max(DEPTH, len(base.chunks)),
        )
        adjusted = [
            replace(
                hit,
                score=hit.score
                + (
                    DEFAULT_EXACT_BONUS
                    if hit.kind == KIND_WIKI
                    and hit.page_id in exact
                    else 0.0
                ),
            )
            for hit in hits
        ]
        adjusted.sort(
            key=lambda hit: (
                0
                if hit.kind == KIND_WIKI and hit.page_id in exact
                else 1,
                -hit.score,
                hit.page_id,
            )
        )
        return _retrieval(adjusted[:DEPTH])

    return FnRetriever(retrieve)


def _hybrid(context: EvalContext) -> Retriever:
    base = _base(context)
    embedder = context.embedder
    if embedder is None:
        raise RuntimeError(
            "the hybrid variant needs an embedding client"
        )

    def retrieve(query: str) -> Retrieval:
        lexical = base.bm25.search(query, limit=DEPTH)
        vector = base.vector_hits(query, embedder, limit=DEPTH)
        fused = fuse(
            lexical, vector, k=DEFAULT_K, candidates=DEPTH
        )
        return _retrieval(fused.hits)

    return FnRetriever(retrieve)


VARIANTS: dict[str, Variant] = {
    "lexical": Variant(
        name="lexical",
        description=(
            "the project's local search over wiki pages and notes: "
            "rank_text scores slug/title exact 4, contained 3, all words 2, "
            "body 1; ties by slug or note id"
        ),
        prepare=_lexical,
    ),
    "bm25": Variant(
        name="bm25",
        description=(
            "BM25F over heading chunks, exact tier retained; the vector "
            "index is still built and its cost reported"
        ),
        prepare=_bm25,
        index_key=CHANNEL_INDEX_KEY,
    ),
    "vector": Variant(
        name="vector",
        description=(
            "Qwen3 chunk cosine, reference page aggregation, and an "
            "independent exact-title tier"
        ),
        prepare=_vector,
        index_key=CHANNEL_INDEX_KEY,
    ),
    "hybrid": Variant(
        name="hybrid",
        description=(
            "BM25F + vector chunks fused by RRF k=60 with the exact tier; "
            "the reference pipeline"
        ),
        prepare=_hybrid,
        index_key=CHANNEL_INDEX_KEY,
    ),
}


def get_variant(name: str) -> Variant:
    """The variant called *name*, or a refusal naming the ones that exist."""
    try:
        return VARIANTS[name]
    except KeyError:
        raise UnknownVariant(name) from None
