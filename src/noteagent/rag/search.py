"""The retrieval backend behind the existing ``search`` tool.

The tool keeps its name, schema and description; only its results change when
RAG is on. The index is per project root and kept in memory between runs. A
search against a cold or stale index syncs it once and answers from the old
lexical path for that call; the next search sees the fresh index. Any failure
on the way leaves the tool with lexical results and a warning, never an error.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import Bm25Index
from noteagent.rag.chunker import ChunkLimits
from noteagent.rag.chunker import KIND_WIKI
from noteagent.rag.embedding import EmbeddingClient, OpenAIEmbeddingClient
from noteagent.rag.fusion import FusedHit, fuse
from noteagent.rag.index import (
    SCOPE_WIKI,
    STATUS_READY,
    IndexStatus,
    RetrievalIndex,
    SyncReport,
)
from noteagent.rag.rerank import (
    REASON_DISABLED,
    HttpReranker,
    Reranker,
    rerank_candidates,
)
from noteagent.rag.result import RagHit, RagSearchResult
from noteagent.rag.settings import RagSettings
from noteagent.rag.types import (
    DEFAULT_CANDIDATE_LIMIT,
    DEFAULT_RESULT_LIMIT,
)
from noteagent.rag.vector import VectorRetriever

__all__ = [
    "RagSearchBackend",
    "get_backend",
    "reset_backends",
]

#: How long a failed or stale index waits before the next lazy sync attempt.
SYNC_INTERVAL_S = 30.0


class RagSearchBackend:
    """One project's retrieval stack, cached for the server's lifetime."""

    def __init__(
        self,
        layout: ProjectLayout,
        settings: RagSettings,
        *,
        embedder: EmbeddingClient | None = None,
        reranker: Reranker | None = None,
        clock: Callable[[], float] | None = None,
        sync_interval_s: float = SYNC_INTERVAL_S,
        sync_runner: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._layout = layout
        self._settings = settings
        self._clock = clock or time.monotonic
        self._sync_interval_s = sync_interval_s
        self._sync_runner = sync_runner or _run_in_thread
        self._embedder = embedder or OpenAIEmbeddingClient(
            settings.embedding
        )
        self._index = RetrievalIndex(
            layout,
            self._embedder,
            model=settings.embedding.model,
            scope=SCOPE_WIKI,
            limits=ChunkLimits(),
            query_prefix=settings.embedding.query_prefix,
        )
        self._reranker = (
            reranker
            if reranker is not None
            else (
                HttpReranker(settings.rerank)
                if settings.rerank.enabled
                else None
            )
        )
        self._vector = VectorRetriever(
            self._index.path, self._embedder
        )
        self._bm25: Bm25Index | None = None
        self._lock = threading.Lock()
        self._last_sync = 0.0
        self._synced = False
        self._syncing = False
        self._last_sync_warnings: tuple[str, ...] = ()

    @property
    def settings(self) -> RagSettings:
        return self._settings

    def status(self) -> IndexStatus:
        return self._index.status()

    def rebuild(self) -> SyncReport:
        """Drop the index and sync everything again."""
        with self._lock:
            report = self._index.rebuild()
            self._bm25 = None
            self._synced = True
            self._last_sync = self._clock()
            self._last_sync_warnings = _failure_warnings(report)
        return report

    def search(
        self, query: str, *, limit: int = DEFAULT_RESULT_LIMIT
    ) -> RagSearchResult:
        text = (query or "").strip()
        if not text:
            return RagSearchResult((), "lexical")
        status = self._index.status()
        if status.state != STATUS_READY:
            warnings = [f"index {status.state}"]
            self._schedule_sync()
            warnings.extend(self._last_sync_warnings)
            return RagSearchResult(
                (), "lexical", True, _unique(warnings)
            )

        try:
            lexical = self._lexical().search(
                text,
                limit=DEFAULT_CANDIDATE_LIMIT,
                kind=KIND_WIKI,
            )
        except Exception as err:  # noqa: BLE001 - fallback is the contract
            return RagSearchResult(
                (), "lexical", True,
                (f"lexical index failed: {err}",),
            )
        try:
            vector = self._vector.search(
                text,
                limit=DEFAULT_CANDIDATE_LIMIT,
                over_fetch=150,
                kinds=(KIND_WIKI,),
            )
        except Exception as err:  # noqa: BLE001 - fallback is the contract
            return RagSearchResult(
                (), "lexical", True,
                (f"vector search failed: {err}",),
            )

        fused = fuse(lexical, vector)
        warnings: list[str] = []
        selected = fused.top(DEFAULT_RESULT_LIMIT)
        if self._settings.rerank.enabled and self._reranker is not None:
            reranked = rerank_candidates(
                text,
                fused.hits,
                self._reranker,
                candidates=self._settings.rerank.candidates,
                top_n=self._settings.rerank.top_n,
            )
            if reranked.reranked:
                selected = reranked.hits
            else:
                selected = fused.top(DEFAULT_RESULT_LIMIT)
                if reranked.reason != REASON_DISABLED:
                    detail = (
                        f"{reranked.reason}: {reranked.message}"
                        if reranked.message
                        else reranked.reason
                    )
                    warnings.append(f"rerank {detail}")

        hits = tuple(
            _rag_hit(hit) for hit in selected[: max(0, limit)]
        )
        return RagSearchResult(
            hits,
            fused.mode,
            bool(warnings),
            _unique(warnings),
        )

    def _schedule_sync(self) -> None:
        """Start one background sync; the search itself never waits for it."""
        if self._syncing:
            return
        self._syncing = True
        try:
            self._sync_runner(self._sync_once)
        except Exception as err:  # noqa: BLE001 - fallback is the contract
            self._syncing = False
            self._last_sync_warnings = (
                f"index sync could not start: {err}",
            )

    def _sync_once(self) -> None:
        try:
            report = self._sync_if_due()
            self._last_sync_warnings = (
                _failure_warnings(report)
                if report is not None
                else ()
            )
        except Exception as err:  # noqa: BLE001 - fallback is the contract
            self._last_sync_warnings = (
                f"index sync failed: {err}",
            )
        finally:
            self._syncing = False

    def _sync_if_due(self) -> SyncReport | None:
        now = self._clock()
        if self._synced and now - self._last_sync < self._sync_interval_s:
            return None
        with self._lock:
            now = self._clock()
            if (
                self._synced
                and now - self._last_sync < self._sync_interval_s
            ):
                return None
            try:
                report = self._index.sync()
            finally:
                self._synced = True
                self._last_sync = now
                self._bm25 = None
            return report

    def _lexical(self) -> Bm25Index:
        with self._lock:
            if self._bm25 is None:
                self._bm25 = self._index.lexical_index()
            return self._bm25


def _rag_hit(hit: FusedHit) -> RagHit:
    return RagHit(
        slug=hit.page_id,
        excerpt=hit.primary_text,
        match=hit.match_label,
        score=hit.score,
    )


def _failure_warnings(report: SyncReport) -> tuple[str, ...]:
    return tuple(
        f"{failure.unit_id}: {failure.reason}"
        for failure in report.failures
    )


def _unique(warnings: list[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(warning for warning in warnings if warning)
    )


def _run_in_thread(work: Callable[[], None]) -> None:
    threading.Thread(
        target=work,
        daemon=True,
        name="noteagent-rag-sync",
    ).start()


_BACKENDS: dict[Path, RagSearchBackend] = {}
_BACKENDS_LOCK = threading.Lock()


def get_backend(
    layout: ProjectLayout, settings: RagSettings
) -> RagSearchBackend | None:
    """The cached backend for this project, or None when RAG is off/unusable."""
    if not settings.enabled:
        return None
    key = layout.root.resolve()
    with _BACKENDS_LOCK:
        backend = _BACKENDS.get(key)
        if backend is not None and backend.settings == settings:
            return backend
        try:
            backend = RagSearchBackend(layout, settings)
        except Exception:  # noqa: BLE001 - an unusable config is lexical
            _BACKENDS.pop(key, None)
            return None
        _BACKENDS[key] = backend
        return backend


def reset_backends() -> None:
    """Drop every cached backend. Tests call this between projects."""
    with _BACKENDS_LOCK:
        _BACKENDS.clear()
