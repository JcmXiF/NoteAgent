"""Preview and undo a run's wiki page writes from the effect journal."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.effects.journal import (
    COMMITTED,
    UNDO,
    WIKI_PAGE_WRITE,
    Effect,
    EffectJournal,
)
from noteagent.effects.restore import (
    CHANGED_BY_PERSON,
    CHANGED_BY_RESTORE,
    CHANGED_BY_RUN,
    MISSING,
    RENAMED,
    UNCHANGED,
    UNKNOWN_EXTERNAL,
    preview_restore,
    restore_run,
)
from noteagent.ingest.page_history import (
    blob_digest,
    keep_before,
    note_agent_write,
    page_snapshots,
    record_write,
)
from noteagent.io.text_file import write_text
from noteagent.project.init import init_project
from noteagent.wiki.editing import rename_page

PAGE_PATH = "wiki/concepts/entropy.md"
PAGE_OLD = """---
type: concept
title: 信息熵
sources: []
---

# 信息熵

旧正文。
"""
PAGE_NEW = PAGE_OLD.replace("旧正文。", "新正文。")
PAGE_OTHER = PAGE_OLD.replace("旧正文。", "别人的正文。")
PAGE_RENAMED = "wiki/concepts/renamed.md"


class RestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.journal = EffectJournal(
            self.layout,
            clock=FixedClock(self.now),
            ids=SequentialIds(),
        )
        self.page_path = PAGE_PATH

    def seed(
        self,
        path: str = PAGE_PATH,
        *,
        old: str = PAGE_OLD,
        new: str = PAGE_NEW,
        effect_id: str = "seed-1",
        run_id: str = "run-1",
    ) -> Effect:
        target = self.layout.root.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, old)
        before = keep_before(self.layout, old)
        write_text(target, new)
        entry = note_agent_write(
            self.layout,
            path,
            before=before,
            after_text=new,
            mode="edit",
            effect_id=effect_id,
            run_id=run_id,
        )
        assert entry is not None
        return self.journal.append(
            Effect(
                kind=WIKI_PAGE_WRITE,
                ident=effect_id,
                target=path,
                run_id=run_id,
                session_id="s1",
                actor="pipeline",
                before=before,
                after=entry.after,
                reversible="snapshot",
                undo="page_history",
                provider="page_history",
                status=COMMITTED,
            )
        )

    def preview_page(self, path: str = PAGE_PATH) -> dict[str, object]:
        preview = preview_restore(self.layout, "run-1", journal=self.journal)
        return next(
            page
            for page in preview["pages"]
            if page["path"] == path
        )

    def page(self, path: str = PAGE_PATH) -> Path:
        return self.layout.root.joinpath(*path.split("/"))

    def test_unchanged_when_the_page_still_says_what_the_run_wrote(self) -> None:
        self.seed()

        page = self.preview_page()

        self.assertEqual(page["classification"], UNCHANGED)
        self.assertEqual(page["defaultAction"], "restore")
        self.assertEqual(page["runId"], "run-1")

    def test_a_person_edit_after_the_run_is_changed_by_person(self) -> None:
        self.seed()
        write_text(self.page(), PAGE_OTHER)
        record_write(
            self.layout,
            PAGE_PATH,
            before=blob_digest(PAGE_NEW),
            after_text=PAGE_OTHER,
            mode="edit",
            by="person",
        )

        self.assertEqual(
            self.preview_page()["classification"], CHANGED_BY_PERSON
        )

    def test_another_agent_run_after_the_run_is_changed_by_run(self) -> None:
        self.seed()
        write_text(self.page(), PAGE_OTHER)
        note_agent_write(
            self.layout,
            PAGE_PATH,
            before=blob_digest(PAGE_NEW),
            after_text=PAGE_OTHER,
            mode="edit",
            effect_id="seed-2",
            run_id="run-2",
        )

        self.assertEqual(
            self.preview_page()["classification"], CHANGED_BY_RUN
        )

    def test_a_later_restore_is_changed_by_restore(self) -> None:
        self.seed()
        write_text(self.page(), PAGE_OLD)
        record_write(
            self.layout,
            PAGE_PATH,
            before=blob_digest(PAGE_NEW),
            after_text=PAGE_OLD,
            mode="restore",
            by="person",
        )

        self.assertEqual(
            self.preview_page()["classification"], CHANGED_BY_RESTORE
        )

    def test_an_external_edit_is_unknown_external(self) -> None:
        self.seed()
        write_text(self.page(), PAGE_OTHER)

        self.assertEqual(
            self.preview_page()["classification"], UNKNOWN_EXTERNAL
        )

    def test_a_deleted_page_is_missing(self) -> None:
        self.seed()
        self.page().unlink()

        self.assertEqual(self.preview_page()["classification"], MISSING)

    def test_a_renamed_page_is_renamed_and_keeps_the_new_path(self) -> None:
        self.seed()
        rename_page(self.layout, PAGE_PATH, PAGE_RENAMED)

        page = self.preview_page()

        self.assertEqual(page["classification"], RENAMED)
        self.assertEqual(page["currentPath"], PAGE_RENAMED)
        self.assertTrue(self.page(PAGE_RENAMED).is_file())

    def test_restore_puts_the_before_version_back_and_writes_an_undo(
        self,
    ) -> None:
        self.seed()

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(len(report["restored"]), 1)
        self.assertEqual(self.page().read_text(encoding="utf-8"), PAGE_OLD)
        self.assertEqual(page_snapshots(self.layout, PAGE_PATH)[0].mode, "restore")
        undo = self.journal.latest(kind=UNDO)
        assert undo is not None
        self.assertEqual(undo.status, COMMITTED)
        self.assertEqual(undo.run_id, "run-1")
        self.assertEqual(undo.after, blob_digest(PAGE_OLD))

    def test_a_second_restore_is_already_restored_and_writes_no_undo(
        self,
    ) -> None:
        self.seed()
        restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(report["restored"], [])
        self.assertEqual(report["skipped"][0]["reason"], "already restored")
        self.assertEqual(len(self.journal.query(kind=UNDO)), 1)

    def test_a_conflict_is_skipped_without_force_and_restored_with_it(
        self,
    ) -> None:
        self.seed()
        write_text(self.page(), PAGE_OTHER)

        blocked = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(blocked["restored"], [])
        self.assertEqual(
            blocked["conflicted"][0]["classification"], UNKNOWN_EXTERNAL
        )
        self.assertEqual(self.page().read_text(encoding="utf-8"), PAGE_OTHER)

        forced = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore", "force": True}],
            journal=self.journal,
        )

        self.assertEqual(len(forced["restored"]), 1)
        self.assertEqual(self.page().read_text(encoding="utf-8"), PAGE_OLD)
        self.assertEqual(len(self.journal.query(kind=UNDO)), 1)

    def test_pages_not_selected_are_left_alone(self) -> None:
        self.seed()
        self.seed(
            "wiki/concepts/other.md",
            old=PAGE_OLD,
            new=PAGE_OTHER,
            effect_id="seed-2",
        )

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(len(report["restored"]), 1)
        self.assertEqual(
            self.page("wiki/concepts/other.md").read_text(encoding="utf-8"),
            PAGE_OTHER,
        )

    def test_a_missing_page_is_not_rebuilt_without_a_version(self) -> None:
        self.seed()
        self.page().unlink()

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(report["restored"], [])
        self.assertEqual(report["conflicted"][0]["classification"], MISSING)
        self.assertEqual(len(self.journal.query(kind=UNDO)), 0)

    def test_a_missing_page_with_force_fails_cleanly(self) -> None:
        self.seed()
        self.page().unlink()

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore", "force": True}],
            journal=self.journal,
        )

        self.assertEqual(report["restored"], [])
        self.assertIn("missing", report["failed"][0]["error"])
        self.assertEqual(len(self.journal.query(kind=UNDO)), 0)

    def test_a_renamed_page_is_conflicted_then_force_fails(self) -> None:
        self.seed()
        rename_page(self.layout, PAGE_PATH, PAGE_RENAMED)

        blocked = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore"}],
            journal=self.journal,
        )

        self.assertEqual(
            blocked["conflicted"][0]["classification"], RENAMED
        )
        self.assertEqual(
            blocked["conflicted"][0]["currentPath"], PAGE_RENAMED
        )

        forced = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "restore", "force": True}],
            journal=self.journal,
        )

        self.assertEqual(forced["restored"], [])
        self.assertIn("moved", forced["failed"][0]["error"])
        self.assertEqual(len(self.journal.query(kind=UNDO)), 0)

    def test_a_skip_choice_changes_nothing(self) -> None:
        self.seed()

        report = restore_run(
            self.layout,
            "run-1",
            [{"path": PAGE_PATH, "action": "skip"}],
            journal=self.journal,
        )

        self.assertEqual(report["skipped"][0]["reason"], "chosen")
        self.assertEqual(self.page().read_text(encoding="utf-8"), PAGE_NEW)
        self.assertEqual(self.journal.query(kind=UNDO), ())


if __name__ == "__main__":
    unittest.main()
