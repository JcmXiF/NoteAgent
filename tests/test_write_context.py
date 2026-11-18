"""A wiki write with a run context leaves an effect and a linked page history row."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.effects.journal import (
    COMMITTED,
    FAILED,
    WIKI_PAGE_WRITE,
    EffectJournal,
)
from noteagent.ingest.page_history import page_snapshots
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    WriteContext,
    write_file_blocks,
)
from noteagent.project.init import init_project

PAGE_PATH = "wiki/concepts/entropy.md"
PAGE = """---
type: concept
title: 信息熵
sources: []
---

# 信息熵

正文。
"""
NEWER = PAGE.replace("正文。", "改过的正文。")


class WriteContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.journal = EffectJournal(
            self.layout,
            clock=FixedClock(
                datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
            ),
            ids=SequentialIds(),
        )
        self.context = WriteContext(
            run_id="run-1",
            session_id="s1",
            actor="pipeline",
            plan_revision="plan-2",
        )
        self.block = FileBlock(path=PAGE_PATH, content=PAGE)

    def write(self, block: FileBlock, *, context: bool = True):
        with patch(
            "noteagent.ingest.write_file_blocks.EffectJournal",
            return_value=self.journal,
        ) as journal_cls:
            result = write_file_blocks(
                self.layout,
                [block],
                write_context=self.context if context else None,
            )
        return result, journal_cls

    def test_a_context_writes_one_effect_and_links_the_page_history(self) -> None:
        result, journal_cls = self.write(self.block)

        journal_cls.assert_called_once_with(self.layout)
        self.assertEqual(len(result.written), 1)
        effects = self.journal.query(kind=WIKI_PAGE_WRITE)
        self.assertEqual(len(effects), 1)
        effect = effects[0]
        self.assertEqual(effect.status, COMMITTED)
        self.assertEqual(effect.target, PAGE_PATH)
        self.assertEqual(effect.run_id, "run-1")
        self.assertEqual(effect.session_id, "s1")
        self.assertEqual(effect.actor, "pipeline")
        self.assertEqual(effect.before, "")
        self.assertEqual(len(effect.after), 64)
        self.assertEqual(effect.reversible, "snapshot")
        self.assertEqual(effect.undo, "page_history")
        self.assertEqual(effect.provider, "page_history")

        history = page_snapshots(self.layout, PAGE_PATH)[0]
        self.assertEqual(history.effect_id, effect.ident)
        self.assertEqual(history.run_id, "run-1")
        self.assertEqual(history.plan_revision, "plan-2")

    def test_without_a_context_nothing_is_journalled(self) -> None:
        result, journal_cls = self.write(self.block, context=False)

        journal_cls.assert_not_called()
        self.assertEqual(len(result.written), 1)
        self.assertFalse(self.layout.effects.exists())
        history = page_snapshots(self.layout, PAGE_PATH)[0]
        self.assertEqual(history.effect_id, "")
        self.assertEqual(history.run_id, "")

    def test_a_second_write_of_the_same_page_does_not_duplicate(
        self,
    ) -> None:
        self.write(self.block)
        second = FileBlock(path=PAGE_PATH, content=NEWER)

        result, _journal_cls = self.write(second)

        self.assertEqual(result.written, ())
        self.assertEqual(
            self.page_file().read_text(encoding="utf-8"), PAGE
        )
        self.assertEqual(
            len(self.journal.query(kind=WIKI_PAGE_WRITE)), 1
        )
        self.assertEqual(len(page_snapshots(self.layout, PAGE_PATH)), 1)

    def test_a_noop_write_does_not_add_an_effect(self) -> None:
        self.write(self.block)

        result, _journal_cls = self.write(self.block)

        self.assertEqual(result.written, ())
        self.assertEqual(
            len(self.journal.query(kind=WIKI_PAGE_WRITE)), 1
        )

    def test_a_failed_write_leaves_a_failed_effect(self) -> None:
        with patch(
            "noteagent.ingest.write_file_blocks.write_text",
            side_effect=OSError("disk full"),
        ):
            result, _journal_cls = self.write(self.block)

        self.assertEqual(result.written, ())
        self.assertEqual(result.warnings[0].code, "failed")
        effects = self.journal.query(
            kind=WIKI_PAGE_WRITE, status=FAILED
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].target, PAGE_PATH)
        self.assertEqual(effects[0].status, FAILED)
        self.assertIn("disk full", effects[0].error)

    def test_a_journal_failure_is_a_hard_effect_warning(self) -> None:
        with patch.object(
            self.journal, "append", side_effect=OSError("journal full")
        ):
            result, _journal_cls = self.write(self.block)

        self.assertEqual(len(result.written), 1)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            ["effect_failed"],
        )
        self.assertIn("effect_failed", HARD_WRITE_CODES)
        self.assertEqual(self.journal.query(kind=WIKI_PAGE_WRITE), ())

    def test_a_history_failure_keeps_the_after_ref(self) -> None:
        with patch(
            "noteagent.ingest.write_file_blocks.note_agent_write",
            side_effect=OSError("history full"),
        ):
            result, _journal_cls = self.write(self.block)

        self.assertEqual(result.written, ())
        self.assertEqual(result.warnings[0].code, "history_failed")
        self.assertIn("history_failed", HARD_WRITE_CODES)
        effects = self.journal.query(
            kind=WIKI_PAGE_WRITE, status=FAILED
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(len(effects[0].after), 64)
        self.assertIn("history full", effects[0].error)

    def page_file(self) -> Path:
        return self.layout.root.joinpath(*PAGE_PATH.split("/"))


if __name__ == "__main__":
    unittest.main()
