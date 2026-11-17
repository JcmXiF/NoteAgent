"""The derived retrieval index: tables, sync, lifecycle and status.

Every test builds a throwaway project and a deterministic fake embedder; the
only external dependency is the LanceDB engine itself.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import lancedb

from noteagent.notes.store import create_note, delete_note
from noteagent.project.layout import ProjectLayout
from noteagent.rag.chunker import ChunkLimits, chunk_note
from noteagent.rag.embedding import EmbeddingError
from noteagent.rag.faux import FakeEmbedder
from noteagent.rag.index import (
    STATUS_COLD,
    STATUS_DISABLED,
    STATUS_READY,
    STATUS_STALE,
    RetrievalIndex,
)

DIM = 8
#: Small sections are kept apart, so a two-heading page makes two chunks.
LIMITS = ChunkLimits(min_chars=0)


def write_page(
    layout: ProjectLayout, slug: str, title: str, body: str
) -> Path:
    path = layout.wiki / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{title}"\n---\n\n{body}\n',
        encoding="utf-8",
    )
    return path


def table_rows(layout: ProjectLayout, name: str) -> list[dict]:
    path = layout.meta / "rag" / "lancedb"
    if not path.is_dir():
        return []
    db = lancedb.connect(path)
    if name not in set(db.list_tables().tables):
        return []
    return db.open_table(name).to_arrow().to_pylist()


def unit_length(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


class IndexCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()
        self.layout = ProjectLayout(root=self.root)
        self.embedder = FakeEmbedder(dim=DIM)
        self.index = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            limits=LIMITS,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()


class FirstSyncTests(IndexCase):
    def test_the_first_sync_builds_chunks_and_pages(self) -> None:
        write_page(self.layout, "a", "A", "## One\n\nalpha\n\n## Two\n\nbeta")
        write_page(self.layout, "b", "B", "only one section")

        report = self.index.sync()

        self.assertEqual(report.added, 2)
        self.assertEqual(report.embedded_chunks, 3)
        self.assertEqual(report.failures, ())
        chunks = sorted(
            table_rows(self.layout, "chunks"),
            key=lambda row: row["chunk_id"],
        )
        self.assertEqual(
            [row["chunk_id"] for row in chunks],
            ["wiki:a#0", "wiki:a#1", "wiki:b#0"],
        )
        self.assertEqual(
            [row["kind"] for row in chunks], ["wiki"] * 3
        )
        self.assertEqual(
            [row["page_id"] for row in chunks], ["a", "a", "b"]
        )
        self.assertEqual(
            [row["heading_path"] for row in chunks],
            ["One", "Two", ""],
        )
        self.assertEqual(
            [row["chunk_text"] for row in chunks],
            ["alpha", "beta", "only one section"],
        )
        for row in chunks:
            self.assertEqual(len(row["vector"]), DIM)
            self.assertAlmostEqual(unit_length(row["vector"]), 1.0)

        pages = sorted(
            table_rows(self.layout, "pages"),
            key=lambda row: row["unit_id"],
        )
        self.assertEqual(
            [row["unit_id"] for row in pages], ["wiki:a", "wiki:b"]
        )
        self.assertEqual(
            [row["page_id"] for row in pages], ["a", "b"]
        )
        self.assertEqual(
            [row["chunk_count"] for row in pages], [2, 1]
        )
        self.assertEqual(
            [row["status"] for row in pages], ["ready", "ready"]
        )
        self.assertEqual(
            {row["model"] for row in pages}, {"fake-model"}
        )
        self.assertEqual({row["dim"] for row in pages}, {DIM})
        for row in pages:
            self.assertEqual(len(row["revision"]), 64)
            self.assertEqual(len(row["config_hash"]), 64)
            self.assertTrue(row["updated_at"])

        self.assertEqual(self.index.status().state, STATUS_READY)

    def test_a_second_sync_embeds_nothing(self) -> None:
        write_page(self.layout, "a", "A", "## One\n\nalpha")
        self.index.sync()
        calls = len(self.embedder.calls)

        report = self.index.sync()

        self.assertEqual(len(self.embedder.calls), calls)
        self.assertEqual(report.added, 0)
        self.assertEqual(report.updated, 0)
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(report.deleted, 0)
        self.assertEqual(report.embedded_chunks, 0)
        self.assertEqual(report.failures, ())

    def test_a_synced_index_builds_the_lexical_index(self) -> None:
        write_page(
            self.layout,
            "a",
            "Entropy",
            "entropy measures uncertainty",
        )
        self.index.sync()

        lexical = self.index.lexical_index()
        hits = lexical.search("entropy")

        self.assertTrue(hits)
        self.assertEqual(hits[0].page_id, "a")
        self.assertTrue(hits[0].exact)

    def test_the_index_exposes_what_the_retrievers_need(self) -> None:
        self.assertIs(self.index.embedder, self.embedder)
        self.assertEqual(self.index.scope, "wiki")

    def test_status_is_cold_before_the_first_sync(self) -> None:
        write_page(self.layout, "a", "A", "alpha")

        status = self.index.status()

        self.assertEqual(status.state, STATUS_COLD)
        self.assertEqual(status.units, 1)
        self.assertEqual(table_rows(self.layout, "chunks"), [])

    def test_an_empty_page_is_ready_with_no_chunks(self) -> None:
        write_page(self.layout, "empty", "Empty", "")

        report = self.index.sync()

        self.assertEqual(report.added, 1)
        self.assertEqual(report.embedded_chunks, 0)
        pages = table_rows(self.layout, "pages")
        self.assertEqual(pages[0]["chunk_count"], 0)
        self.assertEqual(pages[0]["status"], "ready")
        self.assertEqual(table_rows(self.layout, "chunks"), [])


class SwitchEmbedder:
    """A fake that can be told to fail on texts carrying a marker."""

    def __init__(self, dim: int = DIM) -> None:
        self.dim = dim
        self.inner = FakeEmbedder(dim=dim)
        self.calls = self.inner.calls
        self.fail_marker = ""

    def embed(self, texts, *, query: bool = False):
        if self.fail_marker and any(
            self.fail_marker in text for text in texts
        ):
            raise EmbeddingError("the fake endpoint is down")
        return self.inner.embed(texts, query=query)


class UpdateTests(IndexCase):
    def test_a_changed_page_replaces_its_old_chunks(self) -> None:
        write_page(self.layout, "a", "A", "## One\n\nalpha\n\n## Two\n\nbeta")
        write_page(self.layout, "b", "B", "beta page")
        self.index.sync()
        before = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }

        write_page(self.layout, "a", "A", "## One\n\nalpha changed")
        report = self.index.sync()

        self.assertEqual(report.updated, 1)
        self.assertEqual(report.embedded_chunks, 1)
        chunks = sorted(
            table_rows(self.layout, "chunks"),
            key=lambda row: row["chunk_id"],
        )
        self.assertEqual(
            [row["chunk_id"] for row in chunks],
            ["wiki:a#0", "wiki:b#0"],
        )
        self.assertEqual(chunks[0]["chunk_text"], "alpha changed")
        pages = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }
        self.assertEqual(pages["wiki:a"]["chunk_count"], 1)
        self.assertNotEqual(
            pages["wiki:a"]["revision"], before["wiki:a"]["revision"]
        )
        self.assertEqual(
            pages["wiki:b"]["revision"], before["wiki:b"]["revision"]
        )

    def test_a_deleted_page_is_removed(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        page_b = write_page(self.layout, "b", "B", "beta")
        self.index.sync()

        page_b.unlink()
        report = self.index.sync()

        self.assertEqual(report.deleted, 1)
        self.assertEqual(
            [row["unit_id"] for row in table_rows(self.layout, "pages")],
            ["wiki:a"],
        )
        self.assertEqual(
            [row["chunk_id"] for row in table_rows(self.layout, "chunks")],
            ["wiki:a#0"],
        )
        self.assertEqual(self.index.status().state, STATUS_READY)

    def test_a_renamed_page_is_a_new_unit(self) -> None:
        path = write_page(self.layout, "a", "A", "alpha")
        self.index.sync()

        path.rename(self.layout.wiki / "b.md")
        report = self.index.sync()

        self.assertEqual(report.added, 1)
        self.assertEqual(report.deleted, 1)
        self.assertEqual(
            [row["unit_id"] for row in table_rows(self.layout, "pages")],
            ["wiki:b"],
        )
        self.assertEqual(
            [row["chunk_id"] for row in table_rows(self.layout, "chunks")],
            ["wiki:b#0"],
        )


class NoteTests(IndexCase):
    def write_note(self):
        return create_note(
            self.layout,
            page="wiki/a.md",
            body="review plan",
            anchor="A",
            quote="a quote",
            note_id="20261101-090000-abcd",
        )

    def test_notes_wait_for_the_all_scope(self) -> None:
        write_page(self.layout, "a", "Alpha", "alpha")
        note = self.write_note()

        self.index.sync()
        self.assertEqual(
            [
                row["kind"]
                for row in table_rows(self.layout, "chunks")
            ],
            ["wiki"],
        )

        all_scope = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            scope="all",
            limits=LIMITS,
        )
        report = all_scope.sync()

        self.assertEqual(report.added, 1)
        chunks = table_rows(self.layout, "chunks")
        note_rows = [
            row for row in chunks if row["kind"] == "note"
        ]
        self.assertEqual(len(note_rows), 1)
        self.assertEqual(
            note_rows[0]["chunk_id"], f"note:{note.id}#0"
        )
        self.assertEqual(note_rows[0]["page_id"], "a")
        self.assertEqual(note_rows[0]["chunk_text"], "review plan")
        expected = FakeEmbedder(dim=DIM).embed(
            [
                chunk_note(
                    note, page_title="Alpha"
                ).embed_text
            ]
        )[0]
        for actual, wanted in zip(
            note_rows[0]["vector"], expected, strict=True
        ):
            self.assertAlmostEqual(actual, wanted)
        pages = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }
        self.assertEqual(pages[f"note:{note.id}"]["kind"], "note")
        self.assertEqual(pages[f"note:{note.id}"]["page_id"], "a")

    def test_a_deleted_note_is_removed(self) -> None:
        write_page(self.layout, "a", "Alpha", "alpha")
        note = self.write_note()
        all_scope = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            scope="all",
            limits=LIMITS,
        )
        all_scope.sync()

        delete_note(self.layout, note.id)
        report = all_scope.sync()

        self.assertEqual(report.deleted, 1)
        self.assertEqual(
            [
                row["kind"]
                for row in table_rows(self.layout, "pages")
            ],
            ["wiki"],
        )
        self.assertEqual(all_scope.status().state, STATUS_READY)

    def test_a_page_title_change_reembeds_its_notes(self) -> None:
        write_page(self.layout, "a", "Alpha", "alpha")
        self.write_note()
        all_scope = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            scope="all",
            limits=LIMITS,
        )
        all_scope.sync()

        write_page(self.layout, "a", "Alpha Renamed", "alpha")
        report = all_scope.sync()

        self.assertEqual(report.updated, 2)
        pages = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }
        self.assertEqual(
            {row["status"] for row in pages.values()}, {"ready"}
        )


class FailureTests(IndexCase):
    def test_a_page_over_the_chunk_limit_fails(self) -> None:
        body = "\n\n".join(
            f"## S{index}\n\nx{index}" for index in range(513)
        )
        write_page(self.layout, "big", "Big", body)

        report = self.index.sync()

        self.assertEqual(len(report.failures), 1)
        self.assertIn("512", report.failures[0].reason)
        self.assertEqual(table_rows(self.layout, "chunks"), [])
        pages = table_rows(self.layout, "pages")
        self.assertEqual(pages[0]["status"], "failed")
        self.assertEqual(pages[0]["chunk_count"], 0)

    def test_a_failed_page_keeps_its_old_rows(self) -> None:
        embedder = SwitchEmbedder()
        index = RetrievalIndex(
            self.layout,
            embedder,
            model="fake-model",
            limits=LIMITS,
        )
        write_page(self.layout, "a", "A", "alpha")
        write_page(self.layout, "b", "B", "beta")
        index.sync()
        before = {
            row["chunk_id"]: row
            for row in table_rows(self.layout, "chunks")
        }

        write_page(self.layout, "a", "A", "FAIL alpha")
        embedder.fail_marker = "FAIL"
        report = index.sync()

        self.assertEqual(len(report.failures), 1)
        self.assertEqual(report.failures[0].unit_id, "wiki:a")
        after = {
            row["chunk_id"]: row
            for row in table_rows(self.layout, "chunks")
        }
        self.assertEqual(after, before)
        pages = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }
        self.assertEqual(pages["wiki:a"]["status"], "failed")
        self.assertEqual(pages["wiki:b"]["status"], "ready")
        status = index.status()
        self.assertEqual(status.state, STATUS_STALE)
        self.assertEqual(status.failed, 1)

        embedder.fail_marker = ""
        report = index.sync()

        self.assertEqual(report.failures, ())
        pages = {
            row["unit_id"]: row
            for row in table_rows(self.layout, "pages")
        }
        self.assertEqual(pages["wiki:a"]["status"], "ready")
        chunks = table_rows(self.layout, "chunks")
        self.assertIn(
            "FAIL alpha",
            [row["chunk_text"] for row in chunks],
        )
        self.assertEqual(index.status().state, STATUS_READY)


class RebuildTests(IndexCase):
    def test_a_model_or_dimension_change_rebuilds_the_table(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        self.index.sync()

        other = RetrievalIndex(
            self.layout,
            FakeEmbedder(dim=4),
            model="other-model",
            limits=LIMITS,
        )
        report = other.sync()

        self.assertTrue(report.rebuilt)
        self.assertEqual(report.embedded_chunks, 1)
        pages = table_rows(self.layout, "pages")
        self.assertEqual({row["model"] for row in pages}, {"other-model"})
        self.assertEqual({row["dim"] for row in pages}, {4})
        chunks = table_rows(self.layout, "chunks")
        self.assertEqual(len(chunks[0]["vector"]), 4)
        self.assertEqual(other.status().state, STATUS_READY)

    def test_a_query_prefix_change_rebuilds(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        self.index.sync()

        other = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            limits=LIMITS,
            query_prefix="Q: {query}",
        )
        report = other.sync()

        self.assertTrue(report.rebuilt)
        self.assertEqual(other.status().state, STATUS_READY)

    def test_a_corrupt_index_is_wiped_and_rebuilt(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        self.index.path.mkdir(parents=True)
        (self.index.path / "chunks.lance").write_text(
            "not a lance dataset", encoding="utf-8"
        )

        report = self.index.sync()

        self.assertTrue(report.rebuilt)
        self.assertEqual(report.added, 1)
        self.assertEqual(self.index.status().state, STATUS_READY)

    def test_an_empty_table_with_an_old_dimension_is_rebuilt(self) -> None:
        self.index.sync()
        write_page(self.layout, "a", "A", "alpha")

        other = RetrievalIndex(
            self.layout,
            FakeEmbedder(dim=4),
            model="fake-model",
            limits=LIMITS,
        )
        report = other.sync()

        self.assertTrue(report.rebuilt)
        chunks = table_rows(self.layout, "chunks")
        self.assertEqual(len(chunks[0]["vector"]), 4)

    def test_a_chunker_change_also_rebuilds(self) -> None:
        write_page(self.layout, "a", "A", "## One\n\nalpha\n\n## Two\n\nbeta")
        self.index.sync()

        tighter = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            limits=ChunkLimits(min_chars=1000),
        )
        report = tighter.sync()

        self.assertTrue(report.rebuilt)
        self.assertEqual(report.embedded_chunks, 1)
        chunks = table_rows(self.layout, "chunks")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_id"], "wiki:a#0")


class StatusTests(IndexCase):
    def test_a_changed_page_turns_the_index_stale(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        self.index.sync()

        write_page(self.layout, "a", "A", "changed")

        self.assertEqual(self.index.status().state, STATUS_STALE)
        self.index.sync()
        self.assertEqual(self.index.status().state, STATUS_READY)

    def test_a_new_or_removed_page_turns_the_index_stale(self) -> None:
        page_a = write_page(self.layout, "a", "A", "alpha")
        self.index.sync()

        write_page(self.layout, "b", "B", "beta")
        self.assertEqual(self.index.status().state, STATUS_STALE)
        self.index.sync()
        self.assertEqual(self.index.status().state, STATUS_READY)

        page_b = self.layout.wiki / "b.md"
        page_b.unlink()
        self.assertEqual(self.index.status().state, STATUS_STALE)
        self.index.sync()
        self.assertEqual(self.index.status().state, STATUS_READY)
        self.assertEqual(
            [row["unit_id"] for row in table_rows(self.layout, "pages")],
            ["wiki:a"],
        )
        self.assertTrue(page_a.is_file())

    def test_a_missing_chunk_row_makes_the_index_stale(self) -> None:
        write_page(self.layout, "a", "A", "## One\n\nalpha\n\n## Two\n\nbeta")
        self.index.sync()
        db = lancedb.connect(self.index.path)
        db.open_table("chunks").delete("chunk_id = 'wiki:a#0'")

        status = self.index.status()

        self.assertEqual(status.state, STATUS_STALE)
        self.assertEqual(status.reason, "the chunk store is incomplete")

    def test_a_disabled_index_is_disabled_and_syncs_nothing(self) -> None:
        write_page(self.layout, "a", "A", "alpha")
        disabled = RetrievalIndex(
            self.layout,
            self.embedder,
            model="fake-model",
            limits=LIMITS,
            enabled=False,
        )

        self.assertEqual(disabled.status().state, STATUS_DISABLED)
        report = disabled.sync()

        self.assertTrue(report.disabled)
        self.assertEqual(report.added, 0)
        self.assertEqual(self.embedder.calls, [])
        self.assertEqual(table_rows(self.layout, "chunks"), [])


if __name__ == "__main__":
    unittest.main()
