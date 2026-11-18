"""The in-memory retrieval index the channel rows share.

The production index lives in LanceDB and narrows itself to wiki pages. The
ablation needs one index that holds both wiki chunks and notes, so this module
builds the same chunk records in memory from the reference chunker, embeds them
once with the configured embedding client, and exposes the two retrieval sides
the channel variants need: BM25F and vector similarity. Page aggregation is the
reference formula, reused from the production vector module.

The index is deliberately a value, not a service: the harness caches it by
configuration and reports its build cost separately from query latency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from noteagent.ingest.update_index import iter_wiki_content_pages
from noteagent.notes.store import list_notes
from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import Bm25Index, LexicalDoc
from noteagent.rag.chunker import (
    KIND_NOTE,
    KIND_WIKI,
    chunk_note,
    chunk_wiki,
    note_page_slug,
    wiki_title,
)
from noteagent.rag.embedding import EmbeddingClient
from noteagent.rag.vector import ChunkScore, VectorHit, aggregate_pages
from noteagent.wiki.links import wiki_slug_for_path

__all__ = ["BaseIndex", "EvalChunk", "build_base_index"]


@dataclass(frozen=True, slots=True)
class EvalChunk:
    """One embedded chunk, with what the lexical and vector sides need."""

    chunk_id: str
    page_id: str
    kind: str
    chunk_index: int
    chunk_text: str
    heading_path: str
    title: str
    embed_text: str
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _PendingChunk:
    """A chunk before its embedding arrives."""

    chunk_id: str
    page_id: str
    kind: str
    chunk_index: int
    chunk_text: str
    heading_path: str
    title: str
    embed_text: str


@dataclass(frozen=True, slots=True)
class BaseIndex:
    """One corpus as the channel variants see it."""

    chunks: tuple[EvalChunk, ...]
    bm25: Bm25Index
    pages: tuple[tuple[str, str], ...]
    dim: int
    size_bytes: int

    def exact_pages(self, query: str) -> set[str]:
        """Wiki pages whose slug, slug leaf, or title equals the query."""
        wanted = _normalize(query)
        if not wanted:
            return set()
        found: set[str] = set()
        for slug, title in self.pages:
            names = {
                _normalize(slug),
                _normalize(slug.rsplit("/", 1)[-1]),
                _normalize(title),
            }
            if wanted in names:
                found.add(slug)
        return found

    def vector_hits(
        self,
        query: str,
        embedder: EmbeddingClient,
        *,
        limit: int = 50,
    ) -> list[VectorHit]:
        """Page candidates by cosine similarity, aggregated like production."""
        if limit <= 0 or not (query or "").strip() or not self.chunks:
            return []
        vector = embedder.embed([query], query=True)[0]
        if len(vector) != self.dim:
            raise ValueError(
                f"expected {self.dim} query dimensions, got {len(vector)}"
            )
        scores = [
            ChunkScore(
                chunk_id=chunk.chunk_id,
                page_id=chunk.page_id,
                kind=chunk.kind,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                heading_path=chunk.heading_path,
                similarity=_dot(vector, chunk.vector),
            )
            for chunk in self.chunks
        ]
        return aggregate_pages(scores)[:limit]


def build_base_index(
    layout: ProjectLayout, embedder: EmbeddingClient
) -> BaseIndex:
    """Chunk, embed, and index the corpus's wiki pages and notes."""
    if embedder.dim <= 0:
        raise ValueError("the embedding dimension must be positive")

    pages: list[tuple[str, str]] = []
    titles: dict[str, str] = {}
    docs: list[LexicalDoc] = []
    pending: list[_PendingChunk] = []

    for relative, text in iter_wiki_content_pages(layout):
        try:
            slug = wiki_slug_for_path(relative)
        except ValueError:
            continue
        title = wiki_title(text, slug=slug)
        pages.append((slug, title))
        titles[slug] = title
        for chunk in chunk_wiki(text, slug=slug):
            docs.append(
                LexicalDoc(
                    chunk_id=chunk.chunk_id,
                    page_id=slug,
                    kind=KIND_WIKI,
                    chunk_text=chunk.chunk_text,
                    heading_path=chunk.heading_path,
                    title=title,
                    chunk_index=chunk.chunk_index,
                )
            )
            pending.append(
                _PendingChunk(
                    chunk_id=chunk.chunk_id,
                    page_id=slug,
                    kind=KIND_WIKI,
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.chunk_text,
                    heading_path=chunk.heading_path,
                    title=title,
                    embed_text=chunk.embed_text,
                )
            )

    for note in list_notes(layout):
        page_slug = note_page_slug(note.page)
        title = titles.get(page_slug, "")
        chunk = chunk_note(note, page_title=title)
        docs.append(
            LexicalDoc(
                chunk_id=chunk.chunk_id,
                page_id=note.id,
                kind=KIND_NOTE,
                chunk_text=chunk.chunk_text,
                heading_path=chunk.heading_path,
                title=title,
                chunk_index=0,
            )
        )
        pending.append(
            _PendingChunk(
                chunk_id=chunk.chunk_id,
                page_id=note.id,
                kind=KIND_NOTE,
                chunk_index=0,
                chunk_text=chunk.chunk_text,
                heading_path=chunk.heading_path,
                title=title,
                embed_text=chunk.embed_text,
            )
        )

    vectors = (
        embedder.embed(
            [item.embed_text for item in pending], query=False
        )
        if pending
        else []
    )
    if len(vectors) != len(pending):
        raise ValueError(
            f"expected {len(pending)} document vectors, got {len(vectors)}"
        )

    chunks = tuple(
        EvalChunk(
            chunk_id=item.chunk_id,
            page_id=item.page_id,
            kind=item.kind,
            chunk_index=item.chunk_index,
            chunk_text=item.chunk_text,
            heading_path=item.heading_path,
            title=item.title,
            embed_text=item.embed_text,
            vector=tuple(vector),
        )
        for item, vector in zip(pending, vectors)
    )
    size_bytes = sum(
        len(chunk.chunk_text.encode("utf-8"))
        + len(chunk.vector) * 8
        for chunk in chunks
    )
    return BaseIndex(
        chunks=chunks,
        bm25=Bm25Index(docs),
        pages=tuple(pages),
        dim=embedder.dim,
        size_bytes=size_bytes,
    )


def _normalize(value: str) -> str:
    return (value or "").strip().casefold()


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
