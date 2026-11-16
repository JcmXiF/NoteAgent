"""The search backend: lazy sync, hybrid answers, degradation and the cache."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from noteagent.notes.store import create_note
from noteagent.project.layout import ProjectLayout
from noteagent.rag.config import EmbeddingConfig
from noteagent.rag.embedding import EmbeddingError
from noteagent.rag.faux import FakeEmbedder, FakeReranker
from noteagent.rag.rerank import RerankConfig, RerankError
from noteagent.rag.search import (
    RagSearchBackend,
    get_backend,
    reset_backends,
)
from noteagent.rag.settings import RagSettings

DIM = 8


def write_page(
    layout: ProjectLayout, slug: str, title: str, body: str
) -> None:
    path = layout.wiki / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{title}"\n---\n\n{body}\n',
        encoding="utf-8",
    )


def settings(**rerank: object) -> RagSettings:
    return RagSettings(
        enabled=True,
        embedding=EmbeddingConfig(
            endpoint="http://fake", model="fake-model", dim=DIM
        ),
        rerank=RerankConfig(**rerank),  # type: ignore[arg-type]
    )


class FailingEmbedder:
    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim

    def embed(self, texts, *, query: bool = False):
        raise EmbeddingError("the fake endpoint is down")


class BackendCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.layout = ProjectLayout(Path(self._tmp.name) / "project")
        self.layout.wiki.mkdir(parents=True)
        self.embedder = FakeEmbedder(dim=DIM)
        self.now = [0.0]
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def backend(
        self,
        *,
        embedder=None,
        reranker=None,
        intervals: float = 0.0,
        rag_settings: RagSettings | None = None,
        sync_runner=None,
    ) -> RagSearchBackend:
        return RagSearchBackend(
            self.layout,
            rag_settings or settings(),
            embedder=embedder or self.embedder,
            reranker=reranker,
            clock=lambda: self.now[0],
            sync_interval_s=intervals,
            sync_runner=(
                sync_runner
                if sync_runner is not None
                else lambda work: work()
            ),
        )


class LazySyncTests(BackendCase):
    def test_the_first_search_syncs_and_answers_lexically(self) -> None:
        write_page(
            self.layout,
            "concepts/entropy",
            "Entropy",
            "entropy measures uncertainty",
        )
        backend = self.backend()

        cold = backend.search("entropy")

        self.assertEqual(cold.hits, ())
        self.assertEqual(cold.mode, "lexical")
        self.assertTrue(cold.degraded)
        self.assertTrue(
            any("index cold" in warning for warning in cold.warnings)
        )
        self.assertEqual(backend.status().state, "ready")

        warm = backend.search("entropy")

        self.assertFalse(warm.degraded)
        self.assertEqual(warm.mode, "hybrid")
        self.assertIn(
            "concepts/entropy", [hit.slug for hit in warm.hits]
        )

    def test_a_changed_page_is_synced_on_the_next_search(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        backend = self.backend()
        backend.search("entropy")
        backend.search("entropy")

        write_page(
            self.layout,
            "concepts/entropy",
            "Entropy",
            "entropy changed",
        )
        stale = backend.search("entropy")

        self.assertTrue(stale.degraded)
        warm = backend.search("entropy")
        self.assertFalse(warm.degraded)
        self.assertEqual(warm.mode, "hybrid")

    def test_a_cold_search_does_not_wait_for_the_sync(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        scheduled: list[object] = []
        backend = self.backend(
            sync_runner=lambda work: scheduled.append(work)
        )

        result = backend.search("entropy")

        self.assertTrue(result.degraded)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(backend.status().state, "cold")
        scheduled[0]()  # run the scheduled sync
        self.assertEqual(backend.status().state, "ready")

    def test_a_sync_failure_degrades_without_raising(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        backend = self.backend(embedder=FailingEmbedder())

        result = backend.search("entropy")

        self.assertTrue(result.degraded)
        self.assertEqual(result.mode, "lexical")
        self.assertTrue(
            any("down" in warning for warning in result.warnings)
        )
        self.assertEqual(backend.status().state, "stale")

    def test_a_stale_sync_is_throttled(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        backend = self.backend(intervals=30.0)
        backend.search("entropy")  # cold: syncs at t=0
        self.now[0] = 10.0

        write_page(
            self.layout, "concepts/entropy", "Entropy", "changed"
        )
        throttled = backend.search("entropy")

        self.assertTrue(throttled.degraded)
        self.assertEqual(backend.status().state, "stale")

    def test_an_empty_query_does_nothing(self) -> None:
        backend = self.backend()

        result = backend.search("   ")

        self.assertEqual(result.hits, ())
        self.assertEqual(result.mode, "lexical")
        self.assertFalse(result.degraded)
        self.assertEqual(self.embedder.calls, [])


class ScopeTests(BackendCase):
    def test_a_note_is_not_returned_in_production(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        create_note(
            self.layout,
            page="wiki/concepts/entropy.md",
            body="private note marker",
            note_id="20261101-090000-abcd",
        )
        backend = self.backend()
        backend.search("entropy")

        result = backend.search("private note marker")

        self.assertFalse(
            any("note:" in hit.slug for hit in result.hits)
        )


class RerankTests(BackendCase):
    def test_a_failed_rerank_keeps_the_rrf_order(self) -> None:
        for index in range(4):
            write_page(
                self.layout,
                f"concepts/p{index}",
                f"Page {index}",
                "entropy " * (index + 1),
            )
        reranker = FakeReranker(error=RerankError("down"))
        backend = self.backend(
            reranker=reranker,
            rag_settings=settings(
                enabled=True,
                endpoint="http://fake-rerank",
                model="reranker",
            ),
        )
        backend.search("entropy")
        backend.search("entropy")

        result = backend.search("entropy")

        self.assertTrue(result.degraded)
        self.assertTrue(result.hits)
        self.assertTrue(
            any("rerank" in warning for warning in result.warnings)
        )
        self.assertLessEqual(len(result.hits), 20)

    def test_a_rerank_reorders_the_candidates(self) -> None:
        write_page(
            self.layout,
            "concepts/a",
            "Alpha",
            "entropy alpha",
        )
        write_page(
            self.layout,
            "concepts/b",
            "Beta",
            "entropy beta",
        )
        reranker = FakeReranker(
            lambda query, document: (
                10.0 if "concepts/b" in document else 0.0
            )
        )
        backend = self.backend(
            reranker=reranker,
            rag_settings=settings(
                enabled=True,
                endpoint="http://fake-rerank",
                model="reranker",
                top_n=1,
            ),
        )
        backend.search("entropy")
        backend.search("entropy")

        result = backend.search("entropy")

        self.assertFalse(result.degraded)
        self.assertEqual(result.hits[0].slug, "concepts/b")
        self.assertEqual(len(result.hits), 1)


class LifecycleTests(BackendCase):
    def test_status_and_rebuild_are_available(self) -> None:
        write_page(
            self.layout, "concepts/entropy", "Entropy", "entropy"
        )
        backend = self.backend()
        self.assertEqual(backend.status().state, "cold")
        backend.search("entropy")
        self.assertEqual(backend.status().state, "ready")

        report = backend.rebuild()

        self.assertEqual(report.failures, ())
        self.assertEqual(backend.status().state, "ready")

    def test_the_backend_is_cached_per_project(self) -> None:
        configured = settings()
        first = get_backend(self.layout, configured)
        second = get_backend(self.layout, configured)

        self.assertIs(first, second)
        reset_backends()
        third = get_backend(self.layout, configured)
        self.assertIsNot(first, third)

    def test_a_disabled_rag_has_no_backend(self) -> None:
        self.assertIsNone(
            get_backend(self.layout, RagSettings(enabled=False))
        )

    def test_an_unusable_config_has_no_backend(self) -> None:
        unusable = RagSettings(
            enabled=True,
            embedding=EmbeddingConfig(endpoint="", model="m"),
        )

        self.assertIsNone(get_backend(self.layout, unusable))


if __name__ == "__main__":
    unittest.main()
