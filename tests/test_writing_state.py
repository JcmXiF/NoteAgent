"""The writing state: one project, one truth, versions, and its summary."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.project.init import init_project
from noteagent.writing.state import (
    WritingState,
    WritingStateConflict,
    WritingStateError,
    WritingStateStore,
    state_summary,
)


class WritingStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.store = WritingStateStore(self.layout, clock=FixedClock())

    def test_a_missing_state_is_an_empty_state(self) -> None:
        state = self.store.load()

        self.assertEqual(state, WritingState())
        self.assertEqual(state.state_version, 0)
        self.assertIsNone(state.writer)
        self.assertEqual(state.searches, ())
        self.assertEqual(state.selected_pages, ())
        self.assertEqual(state.frames, ())
        self.assertEqual(state.draft, {"pages": []})
        self.assertEqual(state.attention, ())

    def test_save_writes_the_state_and_bumps_its_version(self) -> None:
        saved = self.store.save(WritingState(searches=({"id": "s1"},)))

        self.assertEqual(saved.state_version, 1)
        self.assertTrue(self.layout.writing_state.is_file())
        loaded = self.store.load()
        self.assertEqual(loaded, saved)
        self.assertEqual(loaded.searches, ({"id": "s1"},))

        again = self.store.save(loaded)

        self.assertEqual(again.state_version, 2)
        self.assertEqual(self.store.load().state_version, 2)

    def test_a_new_store_reads_the_persisted_state(self) -> None:
        self.store.save(
            WritingState(
                writer={"kind": "agent", "id": "run-1"},
                selected_pages=({"id": "p1"},),
            )
        )

        reopened = WritingStateStore(self.layout, clock=FixedClock()).load()

        self.assertEqual(reopened.state_version, 1)
        self.assertEqual(reopened.writer, {"kind": "agent", "id": "run-1"})
        self.assertEqual(reopened.selected_pages, ({"id": "p1"},))

    def test_a_stale_save_is_refused_not_overwritten(self) -> None:
        self.store.save(WritingState(searches=({"id": "s1"},)))
        stale = WritingState(state_version=0, searches=({"id": "s2"},))

        with self.assertRaises(WritingStateConflict):
            self.store.save(stale)

        self.assertEqual(self.store.load().searches, ({"id": "s1"},))

    def test_a_non_integer_state_version_is_refused(self) -> None:
        self.layout.writing_state.parent.mkdir(parents=True, exist_ok=True)
        self.layout.writing_state.write_text(
            '{"version": 1, "stateVersion": "5"}', encoding="utf-8"
        )

        with self.assertRaises(WritingStateError):
            self.store.load()

    def test_a_damaged_state_is_an_error_not_an_empty_state(self) -> None:
        self.layout.writing_state.parent.mkdir(parents=True, exist_ok=True)
        self.layout.writing_state.write_text("{not json", encoding="utf-8")

        with self.assertRaises(WritingStateError):
            self.store.load()

    def test_an_unknown_schema_version_is_refused(self) -> None:
        self.layout.writing_state.parent.mkdir(parents=True, exist_ok=True)
        self.layout.writing_state.write_text(
            '{"version": 99, "stateVersion": 0}', encoding="utf-8"
        )

        with self.assertRaises(WritingStateError):
            self.store.load()

    def test_save_is_refused_while_the_project_lock_is_held(self) -> None:
        lock = ProjectLock(self.layout.project_lock, clock=FixedClock())
        lock.acquire("test")
        try:
            with self.assertRaises(ProjectBusy):
                self.store.save(WritingState())
        finally:
            lock.release("test")

        self.assertEqual(self.store.save(WritingState()).state_version, 1)

    def test_the_summary_counts_the_four_sections_and_attention(self) -> None:
        state = WritingState(
            state_version=3,
            writer={"kind": "agent", "id": "run-1"},
            searches=({"id": "s1"},),
            selected_pages=({"id": "p1"},),
            frames=({"id": "f1"},),
            draft={
                "pages": [
                    {"sections": [{"id": "a"}, {"id": "b"}]},
                    {"sections": [{"id": "c"}]},
                ]
            },
            attention=({"kind": "fetch_failed"},),
        )

        summary = state_summary(state)

        self.assertEqual(summary["stateVersion"], 3)
        self.assertEqual(summary["writer"], {"kind": "agent", "id": "run-1"})
        self.assertEqual(
            summary["areas"]["searches"],
            {
                "count": 1,
                "latest": {"id": "s1", "query": "", "status": ""},
            },
        )
        self.assertEqual(summary["areas"]["selectedPages"], {"count": 1})
        self.assertEqual(summary["areas"]["frames"], {"count": 1})
        self.assertEqual(
            summary["areas"]["draft"], {"pages": 2, "sections": 3}
        )
        self.assertEqual(summary["attention"], [{"kind": "fetch_failed"}])

    def test_a_failed_search_is_derived_into_attention(self) -> None:
        state = WritingState(
            searches=(
                {
                    "id": "s1",
                    "status": "failed",
                    "error": "the provider is down",
                },
            ),
        )

        summary = state_summary(state)

        self.assertEqual(
            summary["attention"],
            [
                {
                    "kind": "fetch_failed",
                    "target": "s1",
                    "message": "the provider is down",
                }
            ],
        )

    def test_two_appends_keep_both_search_rows(self) -> None:
        barrier = threading.Barrier(2)

        def add(index: int) -> None:
            barrier.wait()
            self.store.append_search({"id": f"s{index}"})

        threads = [
            threading.Thread(target=add, args=(index,))
            for index in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        rows = {
            str(row["id"]) for row in self.store.load().searches
        }
        self.assertEqual(rows, {"s0", "s1"})

    def test_the_wire_shape_round_trips(self) -> None:
        state = WritingState(
            state_version=4,
            selected_pages=({"id": "p1"},),
            draft={"pages": [{"wikiFile": "wiki/a.md", "sections": []}]},
        )

        raw = state.to_dict()

        self.assertEqual(raw["version"], 1)
        self.assertEqual(raw["stateVersion"], 4)
        self.assertEqual(raw["selectedPages"], [{"id": "p1"}])
        self.assertEqual(WritingState.from_dict(raw), state)


if __name__ == "__main__":
    unittest.main()
