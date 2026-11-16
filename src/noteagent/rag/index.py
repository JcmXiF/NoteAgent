"""The derived retrieval index: chunks and their page-level lifecycle.

Nothing here is a source of truth. The index lives under ``.noteagent/rag`` and
can be deleted or rebuilt at any time; the wiki and the notes are read again to
make it. A page is embedded whole or not at all: every vector for the page must
arrive before its old rows are replaced, so an embedding failure leaves the
previous page searchable and marks the page failed for the next sync.

A page or note is identified by ``unit_id`` (``wiki:<slug>`` or
``note:<id>``). Its content hash is the revision; the embedding model,
dimension and chunker settings are the config hash. A change to either the
revision or the config makes the unit stale, and a config change rebuilds the
whole table rather than mixing vector spaces.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import lancedb
import pyarrow as pa

from noteagent.ingest.update_index import iter_wiki_content_pages
from noteagent.notes.store import list_notes
from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import Bm25Index, Bm25Params, LexicalDoc
from noteagent.rag.chunker import (
    KIND_NOTE,
    KIND_WIKI,
    Chunk,
    ChunkLimits,
    chunk_note,
    chunk_wiki,
    note_page_slug,
    wiki_title,
)
from noteagent.rag.embedding import EmbeddingClient, EmbeddingError
from noteagent.rag.filters import sql_string
from noteagent.wiki.links import wiki_slug_for_path

__all__ = [
    "CHUNKS_TABLE",
    "MAX_CHUNKS_PER_UNIT",
    "PAGES_TABLE",
    "SCOPE_ALL",
    "SCOPE_WIKI",
    "STATUS_COLD",
    "STATUS_DISABLED",
    "STATUS_FAILED",
    "STATUS_READY",
    "STATUS_STALE",
    "IndexStatus",
    "RetrievalIndex",
    "SyncFailure",
    "SyncReport",
]

CHUNKS_TABLE = "chunks"
PAGES_TABLE = "pages"
SCHEMA_VERSION = 1
#: One page or note may not exceed this many chunks in one embedding phase.
MAX_CHUNKS_PER_UNIT = 512

SCOPE_WIKI = "wiki"
SCOPE_ALL = "all"
_SCOPES = (SCOPE_WIKI, SCOPE_ALL)

STATUS_READY = "ready"
STATUS_COLD = "cold"
STATUS_STALE = "stale"
STATUS_DISABLED = "disabled"
#: A page row's own state when its vectors could not be produced.
STATUS_FAILED = "failed"


def _chunk_schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            ("chunk_id", pa.string()),
            ("kind", pa.string()),
            ("page_id", pa.string()),
            ("chunk_index", pa.int32()),
            ("chunk_text", pa.string()),
            ("heading_path", pa.string()),
            ("vector", pa.list_(pa.float32(), dim)),
        ]
    )


_PAGE_SCHEMA = pa.schema(
    [
        ("unit_id", pa.string()),
        ("kind", pa.string()),
        ("page_id", pa.string()),
        ("revision", pa.string()),
        ("model", pa.string()),
        ("dim", pa.int32()),
        ("config_hash", pa.string()),
        ("chunk_count", pa.int32()),
        ("updated_at", pa.string()),
        ("status", pa.string()),
    ]
)


@dataclass(frozen=True, slots=True)
class IndexStatus:
    """What the index currently is, for a caller deciding whether to search."""

    state: str
    units: int = 0
    chunks: int = 0
    failed: int = 0
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "units": self.units,
            "chunks": self.chunks,
            "failed": self.failed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SyncFailure:
    """One page or note whose vectors could not be produced."""

    unit_id: str
    page_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class SyncReport:
    """What one sync changed, and what it could not."""

    scope: str = SCOPE_WIKI
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    embedded_chunks: int = 0
    failures: tuple[SyncFailure, ...] = ()
    rebuilt: bool = False
    disabled: bool = False
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
            "embeddedChunks": self.embedded_chunks,
            "failures": [
                {
                    "unitId": item.unit_id,
                    "pageId": item.page_id,
                    "reason": item.reason,
                }
                for item in self.failures
            ],
            "rebuilt": self.rebuilt,
            "disabled": self.disabled,
            "durationSeconds": round(self.duration_s, 3),
        }


@dataclass(frozen=True, slots=True)
class _Unit:
    """One page or note as it should look in the index right now."""

    unit_id: str
    kind: str
    page_id: str
    revision: str
    chunks: tuple[Chunk, ...]


class RetrievalIndex:
    """The project's derived index, built and kept current by :meth:`sync`.

    ``scope`` is ``wiki`` for production, where notes are never searched, and
    ``all`` for offline experiments that also index the reader's notes. The
    index is disabled by its caller when RAG is off; it then reports
    ``disabled`` and writes nothing.
    """

    def __init__(
        self,
        layout: ProjectLayout,
        embedder: EmbeddingClient,
        *,
        model: str,
        scope: str = SCOPE_WIKI,
        enabled: bool = True,
        limits: ChunkLimits | None = None,
        query_prefix: str = "",
    ) -> None:
        if scope not in _SCOPES:
            raise ValueError(f"unknown index scope: {scope}")
        if not model.strip():
            raise ValueError("the embedding model must not be empty")
        if embedder.dim <= 0:
            raise ValueError("the embedding dimension must be positive")
        self._layout = layout
        self._embedder = embedder
        self._model = model
        self._scope = scope
        self._enabled = enabled
        self._limits = limits or ChunkLimits()
        self._dim = embedder.dim
        self._config_hash = _config_hash(
            model, self._dim, self._limits, query_prefix
        )

    @property
    def path(self) -> Path:
        return self._layout.meta / "rag" / "lancedb"

    @property
    def scope(self) -> str:
        return self._scope

    @property
    def embedder(self) -> EmbeddingClient:
        return self._embedder

    @property
    def config_hash(self) -> str:
        return self._config_hash

    def status(self) -> IndexStatus:
        """Report ``ready``, ``cold``, ``stale`` or ``disabled`` without syncing."""
        if not self._enabled:
            return IndexStatus(
                STATUS_DISABLED, reason="retrieval is disabled"
            )
        units = self._current_units()
        try:
            return self._status_from_tables(units)
        except Exception as err:  # noqa: BLE001 - a derived index can be rebuilt
            return IndexStatus(
                STATUS_STALE,
                units=len(units),
                reason=f"the index cannot be read: {err}",
            )

    def _status_from_tables(self, units: tuple[_Unit, ...]) -> IndexStatus:
        db = self._connect(create=False)
        if db is None:
            return IndexStatus(STATUS_COLD, units=len(units))
        tables = _table_names(db)
        if CHUNKS_TABLE not in tables or PAGES_TABLE not in tables:
            return IndexStatus(STATUS_COLD, units=len(units))
        pages = db.open_table(PAGES_TABLE)
        chunks = db.open_table(CHUNKS_TABLE)
        rows = _rows(pages)
        if not rows:
            return IndexStatus(STATUS_COLD, units=len(units))

        scoped = [row for row in rows if self._in_scope(row["kind"])]
        failed = sum(1 for row in scoped if row["status"] != STATUS_READY)
        chunk_count = sum(int(row["chunk_count"]) for row in scoped)
        for row in scoped:
            if not self._config_matches(row):
                return IndexStatus(
                    STATUS_STALE,
                    units=len(units),
                    chunks=chunk_count,
                    failed=failed,
                    reason="the embedding config changed",
                )
        indexed = {row["unit_id"]: row for row in scoped}
        current = {unit.unit_id: unit for unit in units}
        if set(indexed) != set(current):
            return IndexStatus(
                STATUS_STALE,
                units=len(current),
                chunks=chunk_count,
                failed=failed,
                reason="pages changed since the last sync",
            )
        for unit_id, unit in current.items():
            if indexed[unit_id]["revision"] != unit.revision:
                return IndexStatus(
                    STATUS_STALE,
                    units=len(current),
                    chunks=chunk_count,
                    failed=failed,
                    reason="a page changed since the last sync",
                )
        if failed:
            return IndexStatus(
                STATUS_STALE,
                units=len(current),
                chunks=chunk_count,
                failed=failed,
                reason="some pages failed to embed",
            )
        actual = (
            chunks.count_rows(filter=f"kind = '{KIND_WIKI}'")
            if self._scope == SCOPE_WIKI
            else chunks.count_rows()
        )
        if actual != chunk_count:
            return IndexStatus(
                STATUS_STALE,
                units=len(current),
                chunks=chunk_count,
                failed=0,
                reason="the chunk store is incomplete",
            )
        return IndexStatus(
            STATUS_READY,
            units=len(current),
            chunks=chunk_count,
        )

    def lexical_index(
        self, *, params: Bm25Params | None = None
    ) -> Bm25Index:
        """Build the in-memory BM25F index from the stored chunks.

        A cold, disabled or unreadable index yields an empty lexical index; the
        caller syncs first, then builds this once and searches it many times.
        """
        rows = self._stored_chunks()
        titles = self._titles()
        docs = [
            LexicalDoc.from_row(
                row, title=titles.get(str(row["page_id"]), "")
            )
            for row in rows
            if self._in_scope(str(row["kind"]))
        ]
        return Bm25Index(docs, params=params)

    def _stored_chunks(self) -> list[dict]:
        if not self._enabled:
            return []
        try:
            db = self._connect(create=False)
            if db is None or CHUNKS_TABLE not in _table_names(db):
                return []
            return _rows(db.open_table(CHUNKS_TABLE))
        except Exception:  # noqa: BLE001 - a derived index can be missing
            return []

    def _titles(self) -> dict[str, str]:
        return {
            slug: title for slug, _text, title in self._wiki_pages()
        }

    def _wiki_pages(self) -> list[tuple[str, str, str]]:
        pages: list[tuple[str, str, str]] = []
        for relative, text in iter_wiki_content_pages(self._layout):
            slug = wiki_slug_for_path(relative)
            pages.append((slug, text, wiki_title(text, slug=slug)))
        return pages

    def sync(self) -> SyncReport:
        """Bring the index in line with the project, page by page."""
        started = time.perf_counter()
        if not self._enabled:
            return SyncReport(
                scope=self._scope,
                disabled=True,
                duration_s=time.perf_counter() - started,
            )

        units = self._current_units()
        try:
            db = self._connect(create=True)
            chunks, pages, existing, rebuilt = self._open_for_sync(db)
        except Exception:  # noqa: BLE001 - the index is derived and rebuildable
            self._reset()
            db = self._connect(create=True)
            chunks, pages, existing, rebuilt = self._open_for_sync(db)
            rebuilt = True

        deleted = 0
        current_ids = {unit.unit_id for unit in units}
        for unit_id, row in list(existing.items()):
            if unit_id in current_ids or not self._in_scope(row["kind"]):
                continue
            self._delete_unit(chunks, pages, unit_id, int(row["chunk_count"]))
            deleted += 1

        added = updated = unchanged = embedded = 0
        failures: list[SyncFailure] = []
        for unit in units:
            row = existing.get(unit.unit_id)
            if (
                row is not None
                and row["status"] == STATUS_READY
                and row["revision"] == unit.revision
            ):
                unchanged += 1
                continue
            if row is None:
                added += 1
            else:
                updated += 1
            try:
                vectors = self._embed(unit)
            except EmbeddingError as err:
                failures.append(
                    SyncFailure(unit.unit_id, unit.page_id, str(err))
                )
                self._write_page_row(
                    pages, unit, row, STATUS_FAILED
                )
                continue
            self._replace_chunks(
                chunks,
                unit,
                vectors,
                int(row["chunk_count"]) if row is not None else 0,
            )
            self._write_page_row(pages, unit, row, STATUS_READY)
            embedded += len(vectors)

        return SyncReport(
            scope=self._scope,
            added=added,
            updated=updated,
            unchanged=unchanged,
            deleted=deleted,
            embedded_chunks=embedded,
            failures=tuple(failures),
            rebuilt=rebuilt,
            duration_s=time.perf_counter() - started,
        )

    def _open_for_sync(self, db) -> tuple:
        tables = _table_names(db)
        rebuilt = False
        if CHUNKS_TABLE in tables and PAGES_TABLE in tables:
            chunks = db.open_table(CHUNKS_TABLE)
            pages = db.open_table(PAGES_TABLE)
            existing = {row["unit_id"]: row for row in _rows(pages)}
            if not self._needs_rebuild(existing, chunks):
                return chunks, pages, existing, False
            rebuilt = True
            db.drop_table(CHUNKS_TABLE, ignore_missing=True)
            db.drop_table(PAGES_TABLE, ignore_missing=True)
        elif tables:
            # A half-built index is not a state to reason about; rebuild it.
            rebuilt = True
            for name in (CHUNKS_TABLE, PAGES_TABLE):
                if name in tables:
                    db.drop_table(name, ignore_missing=True)
        chunks = db.create_table(
            CHUNKS_TABLE, schema=_chunk_schema(self._dim)
        )
        pages = db.create_table(PAGES_TABLE, schema=_PAGE_SCHEMA)
        return chunks, pages, {}, rebuilt

    def _needs_rebuild(self, existing: dict[str, dict], chunks) -> bool:
        if not existing:
            if chunks.count_rows() > 0:
                return True
            # An empty table from another dimension cannot accept this one's
            # vectors; the schema, not a page row, is the only evidence left.
            return _vector_dim(chunks) not in (None, self._dim)
        return any(
            not self._config_matches(row)
            for row in existing.values()
        )

    def _config_matches(self, row: dict) -> bool:
        return (
            row["model"] == self._model
            and int(row["dim"]) == self._dim
            and row["config_hash"] == self._config_hash
        )

    def _embed(self, unit: _Unit) -> list[tuple[float, ...]]:
        if len(unit.chunks) > MAX_CHUNKS_PER_UNIT:
            raise EmbeddingError(
                f"{unit.unit_id}: {len(unit.chunks)} chunks exceed the "
                f"per-page limit of {MAX_CHUNKS_PER_UNIT}"
            )
        texts = [chunk.embed_text for chunk in unit.chunks]
        if not texts:
            return []
        vectors = self._embedder.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"{unit.unit_id}: the embedder returned "
                f"{len(vectors)} vectors for {len(texts)} chunks"
            )
        for vector in vectors:
            if len(vector) != self._dim:
                raise EmbeddingError(
                    f"{unit.unit_id}: the embedder returned "
                    f"{len(vector)} dimensions, expected {self._dim}"
                )
        return list(vectors)

    def _write_page_row(
        self,
        pages,
        unit: _Unit,
        old: dict | None,
        status: str,
    ) -> None:
        ready = status == STATUS_READY
        revision = (
            unit.revision if ready or old is None else old["revision"]
        )
        chunk_count = (
            len(unit.chunks)
            if ready
            else (int(old["chunk_count"]) if old is not None else 0)
        )
        pages.delete(f"unit_id = {sql_string(unit.unit_id)}")
        pages.add(
            [
                {
                    "unit_id": unit.unit_id,
                    "kind": unit.kind,
                    "page_id": unit.page_id,
                    "revision": revision,
                    "model": self._model,
                    "dim": self._dim,
                    "config_hash": self._config_hash,
                    "chunk_count": chunk_count,
                    "updated_at": _utc_now(),
                    "status": status,
                }
            ]
        )

    def _replace_chunks(
        self,
        chunks,
        unit: _Unit,
        vectors: list[tuple[float, ...]],
        old_count: int,
    ) -> None:
        """Upsert the new rows, then drop only the tail the page no longer has.

        An upsert over ``chunk_id`` keeps every old row until its replacement
        exists, so a failed insert cannot erase a page that was searchable.
        """
        rows = _chunk_rows(unit, vectors)
        if rows:
            (
                chunks.merge_insert("chunk_id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(rows)
            )
        if len(unit.chunks) < old_count:
            self._delete_chunks(
                chunks,
                unit.unit_id,
                old_count,
                start=len(unit.chunks),
            )

    def _delete_unit(
        self, chunks, pages, unit_id: str, chunk_count: int
    ) -> None:
        self._delete_chunks(chunks, unit_id, chunk_count)
        pages.delete(f"unit_id = {sql_string(unit_id)}")

    def _delete_chunks(
        self,
        chunks,
        unit_id: str,
        chunk_count: int,
        *,
        start: int = 0,
    ) -> None:
        if chunk_count <= start:
            return
        ids = ", ".join(
            sql_string(f"{unit_id}#{index}")
            for index in range(start, chunk_count)
        )
        chunks.delete(f"chunk_id IN ({ids})")

    def _reset(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _current_units(self) -> tuple[_Unit, ...]:
        wiki: list[_Unit] = []
        titles: dict[str, str] = {}
        for slug, text, title in self._wiki_pages():
            titles[slug] = title
            chunks = chunk_wiki(
                text, slug=slug, limits=self._limits
            )
            wiki.append(
                _Unit(
                    unit_id=f"{KIND_WIKI}:{slug}",
                    kind=KIND_WIKI,
                    page_id=slug,
                    revision=_digest(text),
                    chunks=tuple(chunks),
                )
            )
        if self._scope == SCOPE_WIKI:
            return tuple(wiki)

        notes: list[_Unit] = []
        for note in list_notes(self._layout):
            page_id = note_page_slug(note.page)
            title = titles.get(page_id, "")
            chunk = chunk_note(note, page_title=title)
            revision = _digest(
                "\0".join(
                    [
                        note.page,
                        note.anchor,
                        note.quote,
                        note.body,
                        title,
                    ]
                )
            )
            notes.append(
                _Unit(
                    unit_id=f"{KIND_NOTE}:{note.id}",
                    kind=KIND_NOTE,
                    page_id=page_id,
                    revision=revision,
                    chunks=(chunk,),
                )
            )
        return tuple(wiki + notes)

    def _in_scope(self, kind: str) -> bool:
        return kind == KIND_WIKI or self._scope == SCOPE_ALL

    def _connect(self, *, create: bool):
        if not self.path.is_dir():
            if not create:
                return None
            self.path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(self.path)


def _config_hash(
    model: str,
    dim: int,
    limits: ChunkLimits,
    query_prefix: str,
) -> str:
    payload = json.dumps(
        {
            "schema": SCHEMA_VERSION,
            "model": model,
            "dim": dim,
            "normalization": "l2",
            "queryPrefix": query_prefix,
            "chunk": asdict(limits),
        },
        sort_keys=True,
    )
    return _digest(payload)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_rows(
    unit: _Unit, vectors: list[tuple[float, ...]]
) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": chunk.chunk_id,
            "kind": chunk.kind,
            "page_id": chunk.page_id,
            "chunk_index": chunk.chunk_index,
            "chunk_text": chunk.chunk_text,
            "heading_path": chunk.heading_path,
            "vector": list(vector),
        }
        for chunk, vector in zip(unit.chunks, vectors, strict=True)
    ]


def _table_names(db) -> set[str]:
    return set(db.list_tables().tables)


def _vector_dim(table) -> int | None:
    """The fixed vector width a chunks table was created with, if readable."""
    try:
        field = table.schema.field("vector")
    except KeyError:
        return None
    if pa.types.is_fixed_size_list(field.type):
        return field.type.list_size
    return None


def _rows(table) -> list[dict]:
    return table.to_arrow().to_pylist()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
