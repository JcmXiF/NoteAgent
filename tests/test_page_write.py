"""The low-level wiki page write seam: one page, history, effect."""

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
from noteagent.ingest.write_file_blocks import (
    WriteContext,
    write_wiki_page,
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


class PageWriteTests(unittest.TestCase):
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
            actor="agent",
            plan_revision="state-3",
        )

    def write(self, content: str = PAGE, *, context: bool = True):
        return write_wiki_page(
            self.layout,
            PAGE_PATH,
            content,
            mode="create",
            write_context=self.context if context else None,
            journal=self.journal,
        )

    def test_writes_a_page_with_history_and_effect_without_a_plan(self) -> None:
        result = self.write()

        self.assertIsNotNone(result.file)
        self.assertEqual(result.file.mode, "create")
        self.assertEqual(
            (self.layout.root / PAGE_PATH).read_text(encoding="utf-8"), PAGE
        )
        self.assertEqual(result.warnings, ())

        effects = self.journal.query(kind=WIKI_PAGE_WRITE)
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].status, COMMITTED)
        self.assertEqual(effects[0].target, PAGE_PATH)
        self.assertEqual(effects[0].run_id, "run-1")
        self.assertEqual(effects[0].before, "")
        self.assertEqual(len(effects[0].after), 64)
        self.assertEqual(result.effect_id, effects[0].ident)

        history = page_snapshots(self.layout, PAGE_PATH)[0]
        self.assertEqual(history.effect_id, effects[0].ident)
        self.assertEqual(history.before, "")
        self.assertEqual(history.after, effects[0].after)

    def test_overwrite_keeps_the_replaced_version(self) -> None:
        self.write()
        newer = PAGE.replace("正文。", "改过的正文。")

        result = self.write(newer)

        self.assertIsNotNone(result.file)
        history = page_snapshots(self.layout, PAGE_PATH)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[1].after, history[0].before)
        self.assertNotEqual(history[0].before, "")
        self.assertNotEqual(history[0].before, history[0].after)

    def test_without_a_context_no_effect_but_history_is_kept(self) -> None:
        result = self.write(context=False)

        self.assertIsNotNone(result.file)
        self.assertEqual(result.effect_id, "")
        self.assertFalse(self.layout.effects.exists())
        history = page_snapshots(self.layout, PAGE_PATH)[0]
        self.assertEqual(history.effect_id, "")

    def test_a_failed_write_leaves_a_failed_effect(self) -> None:
        with patch(
            "noteagent.ingest.write_file_blocks.write_text",
            side_effect=OSError("disk full"),
        ):
            result = self.write()

        self.assertIsNone(result.file)
        self.assertEqual(result.warnings[0].code, "failed")
        effects = self.journal.query(
            kind=WIKI_PAGE_WRITE, status=FAILED
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].ident, result.effect_id)
        self.assertEqual(effects[0].target, PAGE_PATH)
        self.assertIn("disk full", effects[0].error)

    def test_a_history_failure_keeps_the_after_ref(self) -> None:
        with patch(
            "noteagent.ingest.write_file_blocks.note_agent_write",
            side_effect=OSError("history full"),
        ):
            result = self.write()

        self.assertIsNone(result.file)
        self.assertEqual(result.warnings[0].code, "history_failed")
        effects = self.journal.query(
            kind=WIKI_PAGE_WRITE, status=FAILED
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].ident, result.effect_id)
        self.assertEqual(len(effects[0].after), 64)
        self.assertIn("history full", effects[0].error)

    def test_a_failure_names_the_callers_path(self) -> None:
        with patch(
            "noteagent.ingest.write_file_blocks.write_text",
            side_effect=OSError("disk full"),
        ):
            result = write_wiki_page(
                self.layout,
                "wiki/concepts/entropy.md",
                PAGE,
                mode="create",
                write_context=self.context,
                journal=self.journal,
                display_path="wiki\\concepts\\entropy.md",
            )

        self.assertIn("wiki\\concepts\\entropy.md", result.warnings[0].message)


if __name__ == "__main__":
    unittest.main()
