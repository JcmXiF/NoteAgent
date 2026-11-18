"""The project's effect journal: side effects, one append-only line each."""

from __future__ import annotations

import json
import multiprocessing
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.effects.journal import (
    COMMITTED,
    FAILED,
    NOTE_WRITE,
    RAW_ADD,
    UNDO,
    WIKI_PAGE_WRITE,
    Effect,
    EffectJournal,
)
from noteagent.project.layout import ProjectLayout

NOW = "2026-11-18T09:00:00Z"


def append_in_process(layout_root: str, worker: int) -> None:
    """A separate process appends to the same journal (spawn-safe)."""
    from noteagent.effects.journal import Effect, EffectJournal, NOTE_WRITE
    from noteagent.project.layout import ProjectLayout

    journal = EffectJournal(ProjectLayout(Path(layout_root)))
    for number in range(10):
        journal.append(
            Effect(kind=NOTE_WRITE, target=f"{worker}-{number}.md")
        )


class _JournalCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.journal = EffectJournal(
            self.layout,
            clock=FixedClock(
                datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
            ),
            ids=SequentialIds(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def rows(self) -> list[dict[str, object]]:
        path = self.layout.effects
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class AppendTests(_JournalCase):
    def test_an_effect_is_appended_as_one_jsonl_line(self) -> None:
        written = self.journal.append(
            Effect(
                kind=WIKI_PAGE_WRITE,
                target="wiki/concepts/信息熵.md",
                run_id="run-1",
                session_id="s1",
                actor="pipeline",
                before="a" * 64,
                after="b" * 64,
                reversible="snapshot",
                undo="page_history",
                provider="page_history",
            )
        )

        self.assertEqual(written.ident, "eff-1")
        self.assertEqual(written.at, NOW)
        self.assertEqual(written.status, COMMITTED)
        self.assertEqual(self.rows(), [
            {
                "v": 1,
                "type": "effect",
                "id": "eff-1",
                "at": NOW,
                "runId": "run-1",
                "sessionId": "s1",
                "actor": "pipeline",
                "kind": "wiki_page_write",
                "target": "wiki/concepts/信息熵.md",
                "before": "a" * 64,
                "after": "b" * 64,
                "reversible": "snapshot",
                "undo": "page_history",
                "provider": "page_history",
                "status": "committed",
                "error": "",
            }
        ])
        self.assertEqual(self.journal.query(), (written,))
        self.assertEqual(self.journal.latest(), written)


class QueryTests(_JournalCase):
    def setUp(self) -> None:
        super().setUp()
        self.first = self.journal.append(
            Effect(
                kind=WIKI_PAGE_WRITE,
                target="wiki/a.md",
                run_id="run-1",
                session_id="s1",
            )
        )
        self.second = self.journal.append(
            Effect(
                kind=RAW_ADD,
                target="raw/a.md",
                run_id="run-1",
                session_id="s1",
            )
        )
        self.third = self.journal.append(
            Effect(
                kind=WIKI_PAGE_WRITE,
                target="wiki/b.md",
                run_id="run-2",
                session_id="s2",
                status=FAILED,
            )
        )
        self.fourth = self.journal.append(
            Effect(kind=UNDO, target="wiki/a.md", run_id="run-2", session_id="s2")
        )

    def test_queries_filter_by_run_session_kind_and_status(self) -> None:
        self.assertEqual(
            self.journal.query(run_id="run-1"),
            (self.first, self.second),
        )
        self.assertEqual(
            self.journal.query(kind=WIKI_PAGE_WRITE),
            (self.first, self.third),
        )
        self.assertEqual(
            self.journal.query(status=FAILED), (self.third,)
        )
        self.assertEqual(
            self.journal.query(
                session_id="s2", kind=WIKI_PAGE_WRITE
            ),
            (self.third,),
        )
        self.assertEqual(self.journal.query(), (
            self.first,
            self.second,
            self.third,
            self.fourth,
        ))
        self.assertEqual(self.journal.query(run_id="missing"), ())

    def test_query_orders_by_at_then_line(self) -> None:
        later = self.journal.append(
            Effect(
                kind=NOTE_WRITE,
                target="notes/later.md",
                at="2026-11-18T09:00:02Z",
            )
        )
        earlier = self.journal.append(
            Effect(
                kind=NOTE_WRITE,
                target="notes/earlier.md",
                at="2026-11-18T09:00:01Z",
            )
        )

        self.assertEqual(
            self.journal.query(kind=NOTE_WRITE), (earlier, later)
        )
        self.assertEqual(self.journal.latest(kind=NOTE_WRITE), later)

    def test_latest_answers_the_last_matching_line(self) -> None:
        self.assertEqual(
            self.journal.latest(kind=WIKI_PAGE_WRITE), self.third
        )
        self.assertEqual(
            self.journal.latest(run_id="run-1"), self.second
        )
        self.assertIsNone(self.journal.latest(session_id="missing"))


class BadLineTests(_JournalCase):
    def test_bad_lines_are_skipped_with_a_warning_and_do_not_reorder(self) -> None:
        first = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/a.md")
        )
        path = self.layout.effects
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
            handle.write("[1, 2, 3]\n")
            handle.write(
                json.dumps(
                    {
                        "v": 1,
                        "type": "effect",
                        "id": "e-bad-kind",
                        "at": NOW,
                        "kind": "read",
                        "status": "committed",
                    }
                )
                + "\n"
            )
            handle.write(
                json.dumps(
                    {
                        "v": 2,
                        "type": "effect",
                        "id": "e-future",
                        "at": NOW,
                        "kind": WIKI_PAGE_WRITE,
                        "status": "committed",
                    }
                )
                + "\n"
            )
            handle.write(
                json.dumps(
                    {
                        "v": 1,
                        "type": "effect",
                        "id": "e-bad-status",
                        "at": NOW,
                        "kind": WIKI_PAGE_WRITE,
                        "status": "maybe",
                    }
                )
                + "\n"
            )
            handle.write("\n")
        second = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/b.md")
        )

        with self.assertLogs("noteagent.effects.journal", level="WARNING") as logs:
            effects = self.journal.query()

        self.assertEqual(effects, (first, second))
        self.assertEqual(len(logs.records), 5)
        self.assertEqual(self.journal.latest(), second)


class ConcurrencyTests(_JournalCase):
    def test_two_processes_append_without_losing_lines(self) -> None:
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(
                target=append_in_process,
                args=(str(self.layout.root), worker),
            )
            for worker in range(2)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)

        self.assertEqual(
            [process.exitcode for process in processes], [0, 0]
        )
        self.assertEqual(len(self.journal.query(kind=NOTE_WRITE)), 20)
        self.assertEqual(len(self.rows()), 20)

    def test_concurrent_appends_keep_every_line(self) -> None:
        written = []
        errors: list[Exception] = []

        def append_ten(worker: int) -> None:
            try:
                for number in range(10):
                    written.append(
                        self.journal.append(
                            Effect(
                                kind=NOTE_WRITE,
                                target=f"notes/{worker}-{number}.md",
                            )
                        )
                    )
            except Exception as err:  # pragma: no cover - reported below
                errors.append(err)

        threads = [
            threading.Thread(target=append_ten, args=(worker,))
            for worker in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(written), 80)
        self.assertEqual(len({effect.ident for effect in written}), 80)
        self.assertEqual(len(self.journal.query(kind=NOTE_WRITE)), 80)
        self.assertEqual(len(self.rows()), 80)


class EmptyJournalTests(_JournalCase):
    def test_an_absent_journal_reads_as_empty_and_is_made_on_first_append(
        self,
    ) -> None:
        self.assertFalse(self.layout.effects.exists())
        self.assertEqual(self.journal.query(), ())
        self.assertIsNone(self.journal.latest())

        written = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/a.md")
        )

        self.assertTrue(self.layout.effects.is_file())
        self.assertEqual(self.journal.query(), (written,))

    def test_an_empty_file_is_not_an_error(self) -> None:
        self.layout.effects.parent.mkdir(parents=True, exist_ok=True)
        self.layout.effects.write_text("", encoding="utf-8")

        self.assertEqual(self.journal.query(), ())

        written = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/a.md")
        )
        self.assertEqual(self.journal.query(), (written,))

    def test_a_partial_utf8_line_is_skipped_with_a_warning(self) -> None:
        first = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/a.md")
        )
        with self.layout.effects.open("ab") as handle:
            handle.write(b'{"v":1,"type":"effect","id":"e-\xe4\xb8')

        with self.assertLogs("noteagent.effects.journal", level="WARNING") as logs:
            effects = self.journal.query()

        self.assertEqual(effects, (first,))
        self.assertEqual(len(logs.records), 1)

    def test_a_partial_tail_does_not_swallow_the_next_effect(self) -> None:
        first = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/a.md")
        )
        with self.layout.effects.open("a", encoding="utf-8") as handle:
            handle.write('{"v":1,"type":"eff')

        second = self.journal.append(
            Effect(kind=WIKI_PAGE_WRITE, target="wiki/b.md")
        )

        with self.assertLogs("noteagent.effects.journal", level="WARNING"):
            effects = self.journal.query()
        self.assertEqual(effects, (first, second))

    def test_an_unknown_kind_or_status_is_refused_before_writing(self) -> None:
        with self.assertRaises(ValueError):
            self.journal.append(Effect(kind="read"))
        with self.assertRaises(ValueError):
            self.journal.append(
                Effect(kind=WIKI_PAGE_WRITE, status="maybe")
            )

        self.assertEqual(self.journal.query(), ())
        self.assertFalse(self.layout.effects.exists())


if __name__ == "__main__":
    unittest.main()
