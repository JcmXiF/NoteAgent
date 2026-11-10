"""Old chats become JSONL sessions once, and a failure stays recoverable."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent_fakes import (
    FixedClock,
    SequentialIds,
    old_chat_payload,
)
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


def _old_chat(
    conversation_id: str = "c-1",
    *,
    title: str = "Entropy",
    with_cards: bool = True,
) -> dict[str, object]:
    return old_chat_payload(
        conversation_id, title=title, with_cards=with_cards
    )


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )

    def write_old(self, payload: dict[str, object], name: str) -> Path:
        self.layout.chats_dir.mkdir(parents=True, exist_ok=True)
        path = self.layout.chats_dir / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def test_an_old_chat_becomes_a_session_with_its_cards(self) -> None:
        self.write_old(_old_chat(), "c-1.json")

        report = self.store.ensure_migrated()

        self.assertEqual(report.migrated, ("c-1",))
        self.assertEqual(report.failed, ())
        self.assertTrue(self.layout.session_path("c-1").is_file())
        self.assertTrue(
            (self.layout.chats_dir / ".migrated").is_file()
        )
        loaded = self.store.load("c-1")
        self.assertEqual(loaded.title, "Entropy")
        self.assertEqual(
            [entry["role"] for entry in loaded.entries],
            ["user", "assistant"],
        )
        self.assertEqual(
            loaded.entries[-1]["meta"]["cards"],
            [
                {
                    "tool": "wiki_read",
                    "title": "Entropy",
                    "slug": "concepts/entropy",
                }
            ],
        )
        self.assertEqual(
            loaded.entries[-1]["ts"], "2026-11-09T10:00:02Z"
        )
        # The marker makes a fresh store's pass a no-op.
        fresh = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        self.assertEqual(fresh.ensure_migrated().migrated, ())

    def test_an_unnamed_legacy_title_reads_as_the_first_question(self) -> None:
        self.write_old(
            _old_chat(title="新对话", with_cards=False), "c-1.json"
        )

        self.store.ensure_migrated()

        self.assertEqual(self.store.load("c-1").title, "什么是熵？")

    def test_a_bad_file_stays_and_does_not_stop_the_good_one(self) -> None:
        good = self.write_old(_old_chat("c-ok"), "c-ok.json")
        bad = self.layout.chats_dir / "c-bad.json"
        bad.write_text("{not json", encoding="utf-8")

        report = self.store.ensure_migrated()

        self.assertEqual(report.migrated, ("c-ok",))
        self.assertEqual([item[0] for item in report.failed], ["c-bad"])
        self.assertTrue(good.is_file())
        self.assertTrue(bad.is_file())
        self.assertTrue(
            (self.layout.chats_dir / ".migrated").is_file()
        )
        self.assertEqual(
            [item.id for item in self.store.list()], ["c-ok"]
        )

        # A fresh store retries the bad file and does not import the good one
        # again, because the marker remembers what was handled.
        fresh = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        again = fresh.ensure_migrated()
        self.assertEqual(again.migrated, ())
        self.assertEqual(again.skipped, ("c-ok",))
        self.assertEqual([item[0] for item in again.failed], ["c-bad"])

    def test_a_deleted_migrated_session_stays_deleted(self) -> None:
        self.write_old(_old_chat("c-1"), "c-1.json")
        (self.layout.chats_dir / "c-bad.json").write_text(
            "{not json", encoding="utf-8"
        )
        self.store.ensure_migrated()
        self.store.delete("c-1")

        fresh = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        fresh.ensure_migrated()

        self.assertFalse(self.layout.session_path("c-1").exists())
        self.assertEqual(fresh.list(), ())

    def test_two_stores_migrate_a_chat_once(self) -> None:
        self.write_old(_old_chat("c-1"), "c-1.json")
        stores = [
            JsonlSessionStore(
                self.layout, clock=FixedClock(), ids=SequentialIds()
            )
            for _ in range(2)
        ]
        reports = []

        def run(store):
            reports.append(store.ensure_migrated())

        threads = [
            threading.Thread(target=run, args=(store,)) for store in stores
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(
            sum(len(report.migrated) for report in reports), 1
        )
        self.assertEqual(sum(len(report.failed) for report in reports), 0)
        self.assertTrue(self.layout.session_path("c-1").is_file())

    def test_an_existing_session_is_not_overwritten(self) -> None:
        self.store.create("c-1", "New name")
        self.write_old(_old_chat("c-1"), "c-1.json")

        report = self.store.ensure_migrated()

        self.assertEqual(report.migrated, ())
        self.assertEqual(report.skipped, ("c-1",))
        self.assertEqual(self.store.load("c-1").title, "New name")

    def test_the_list_works_after_the_index_is_removed(self) -> None:
        self.write_old(_old_chat("c-1"), "c-1.json")
        self.store.ensure_migrated()
        (self.layout.sessions_dir / "index.json").unlink()

        self.assertEqual(
            [item.id for item in self.store.list()], ["c-1"]
        )
        self.assertTrue(
            (self.layout.sessions_dir / "index.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()
