"""write_wiki publishes the state draft through the page-write seam."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import ToolContext
from noteagent.effects.journal import (
    COMMITTED,
    WIKI_PAGE_WRITE,
    EffectJournal,
)
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.ingest.page_history import page_snapshots
from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.ingest.source_citations import (
    list_source_citations,
    parse_clip_sections,
)
from noteagent.project.doctor import DoctorFinding, DoctorReport
from noteagent.project.init import init_project
from noteagent.writing.maintenance import DOCTOR_FINDINGS_LIMIT
from noteagent.writing.state import WritingStateStore, draft_pages
from writing_fixtures import (
    prepare_draft,
    write_draft,
    writing_tools,
)

WIKI_FILE = "wiki/concepts/信息熵.md"


class WritingPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def prepared(self, tools, *, body: str = "熵是信息的平均量。"):
        return prepare_draft(
            tools,
            self.layout,
            wiki_file=WIKI_FILE,
            body=body,
        )

    def write(self, tools, draft_version: int):
        return write_draft(
            tools,
            wiki_file=WIKI_FILE,
            draft_version=draft_version,
        )

    def wiki_path(self) -> Path:
        return self.layout.root.joinpath(*WIKI_FILE.split("/"))

    def test_publish_writes_the_page_with_sources_and_markers(self) -> None:
        tools = writing_tools(self.layout)
        page_id, frame_id, draft_version = self.prepared(tools)

        result = self.write(tools, draft_version)

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["effectIds"]), 1)
        effect_id = payload["effectIds"][0]
        self.assertTrue(payload["writtenAt"])

        wiki = self.wiki_path()
        self.assertTrue(wiki.is_file())
        text = wiki.read_text(encoding="utf-8")
        parsed = parse_page(text)
        self.assertIn("信息熵", parsed.fields.get("title", ""))
        page_state = WritingStateStore(self.layout).load().selected_pages[0]
        material = str(page_state["material"])
        sources = parse_inline_list(parsed.fields.get("sources", ""))
        self.assertEqual(
            sources, [f"{material}#b2", f"{material}#b3"]
        )
        self.assertIn(f"<!-- na-clip:{frame_id} b2,b3 -->", text)
        self.assertIn("## 定义", text)
        self.assertIn("熵是信息的平均量。", text)
        self.assertTrue((self.layout.root / material).is_file())

        marks = parse_clip_sections(parsed.body)
        self.assertEqual(marks[0].collection_id, frame_id)
        self.assertEqual(marks[0].block_ids, ("b2", "b3"))

        state = WritingStateStore(self.layout).load()
        page = draft_pages(state.draft)[0]
        self.assertEqual(page["status"], "written")
        self.assertEqual(page["effectIds"], [effect_id])

        effects = EffectJournal(self.layout).query(
            kind=WIKI_PAGE_WRITE
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].status, COMMITTED)
        self.assertEqual(effects[0].target, WIKI_FILE)
        self.assertEqual(effects[0].ident, effect_id)
        history = page_snapshots(self.layout, WIKI_FILE)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].effect_id, effect_id)

    def test_publishing_the_same_version_twice_is_idempotent(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        first = json.loads(self.write(tools, draft_version).content)
        before = self.wiki_path().read_text(encoding="utf-8")

        second = self.write(tools, draft_version)

        payload = json.loads(second.content)
        self.assertEqual(payload["effectIds"], first["effectIds"])
        self.assertEqual(payload["writtenAt"], first["writtenAt"])
        self.assertEqual(
            self.wiki_path().read_text(encoding="utf-8"), before
        )
        self.assertEqual(
            len(
                EffectJournal(self.layout).query(
                    kind=WIKI_PAGE_WRITE
                )
            ),
            1,
        )
        self.assertEqual(
            len(page_snapshots(self.layout, WIKI_FILE)), 1
        )

    def test_the_marker_resolves_to_the_raw_source(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, frame_id, draft_version = self.prepared(tools)
        self.write(tools, draft_version)
        state = WritingStateStore(self.layout).load()
        material = str(state.selected_pages[0]["material"])

        index = list_source_citations(self.layout)

        marks = index.marks.get(material, [])
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].path, WIKI_FILE)
        self.assertEqual(marks[0].heading, "定义")
        self.assertEqual(marks[0].collection_id, frame_id)
        self.assertEqual(marks[0].block_ids, ("b2", "b3"))

    def test_a_pinned_block_missing_from_the_page_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        sections = [dict(section) for section in page["sections"]]
        sections[0] = {
            **sections[0],
            "citations": [
                {
                    "frameId": frame_id,
                    "frameVersion": 1,
                    "blockIds": ["b99"],
                }
            ],
        }
        page["sections"] = sections
        store.save(replace(state, draft={"pages": [page]}))

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such block", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_a_missing_frame_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        store.save(replace(store.load(), frames=()))

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such frame", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_an_existing_frontmatter_title_is_kept(self) -> None:
        target = self.wiki_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\n"
            "type: concept\n"
            "title: 自定义标题\n"
            "created: 2020-01-01\n"
            "updated: 2020-01-01\n"
            "sources: []\n"
            "---\n\n"
            "# 旧标题\n",
            encoding="utf-8",
        )
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)

        self.write(tools, draft_version)

        text = target.read_text(encoding="utf-8")
        parsed = parse_page(text)
        self.assertIn("自定义标题", parsed.fields.get("title", ""))
        self.assertIn("type: concept", text)
        self.assertIn("sources: [raw/", text)

    def test_an_empty_section_body_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(
            tools, body=""
        )

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("has no body", result.content)
        self.assertFalse(self.wiki_path().exists())
        page = draft_pages(
            WritingStateStore(self.layout).load().draft
        )[0]
        self.assertEqual(page["status"], "draft")

    def test_a_missing_source_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        page_id, _frame_id, draft_version = self.prepared(tools)
        source = (
            self.layout.writing_pages / f"{page_id}.material.html"
        )
        source.unlink()

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("original", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_publishing_without_a_draft_names_the_drafts(self) -> None:
        tools = writing_tools(self.layout)

        result = write_draft(tools, draft_version=1)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("draft pages: none", result.content)

    def test_a_stale_draft_version_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, _draft_version = self.prepared(tools)

        result = self.write(tools, 0)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("version", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_a_legacy_draft_key_publishes_to_the_canonical_page(
        self,
    ) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        page["wikiFile"] = "concepts/信息熵"
        store.save(replace(state, draft={"pages": [page]}))

        result = write_draft(
            tools,
            wiki_file="concepts/信息熵",
            draft_version=draft_version,
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["wikiFile"], WIKI_FILE)
        self.assertTrue(self.wiki_path().is_file())

    def test_the_write_handler_checks_cancel_before_the_page_write(
        self,
    ) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        cancel = Event()
        cancel.set()
        spec = tools.get("write_wiki")

        with self.assertRaises(RunCancelled):
            spec.handler(
                {
                    "wikiFile": WIKI_FILE,
                    "draftVersion": draft_version,
                },
                ToolContext("s1", cancel=cancel),
            )

        self.assertFalse(self.wiki_path().exists())

    def test_a_retry_after_a_failed_mark_only_marks(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        original = WritingStateStore.mark_draft_written
        calls = {"count": 0}

        def flaky(store, wiki_file, version, effect_ids, written_at):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("the mark failed")
            return original(
                store, wiki_file, version, effect_ids, written_at
            )

        with patch.object(
            WritingStateStore, "mark_draft_written", flaky
        ):
            first = self.write(tools, draft_version)
        self.assertTrue(first.meta.get("error"))

        second = self.write(tools, draft_version)

        self.assertFalse(second.meta.get("error"), second.content)
        payload = json.loads(second.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["effectIds"]), 1)
        effects = EffectJournal(self.layout).query(
            kind=WIKI_PAGE_WRITE
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].ident, payload["effectIds"][0])
        self.assertEqual(
            len(page_snapshots(self.layout, WIKI_FILE)), 1
        )
        page = WritingStateStore(self.layout).load().draft["pages"][0]
        self.assertEqual(page["status"], "written")

    def test_a_cancelled_write_leaves_no_page(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        cancel = Event()
        cancel.set()

        with self.assertRaises(RunCancelled):
            tools.dispatch(
                "write_wiki",
                json.dumps(
                    {
                        "wikiFile": WIKI_FILE,
                        "draftVersion": draft_version,
                    }
                ),
                ToolContext("s1", cancel=cancel),
            )

        self.assertFalse(self.wiki_path().exists())
        page = WritingStateStore(self.layout).load().draft
        status = page["pages"][0]["status"] if page["pages"] else ""
        self.assertEqual(status, "draft")

    def test_a_slug_publishes_to_the_canonical_page(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = prepare_draft(
            tools,
            self.layout,
            wiki_file="concepts/信息熵",
            body="熵是信息的平均量。",
        )

        result = write_draft(
            tools,
            wiki_file="concepts/信息熵",
            draft_version=draft_version,
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["wikiFile"], WIKI_FILE)
        self.assertTrue(self.wiki_path().is_file())


class WritingMaintenanceTests(unittest.TestCase):
    """A publish leaves index, log and doctor in step with the page."""

    BODY = (
        "熵是信息的平均量，衡量一个随机变量分布的平均不确定性；"
        "交叉熵衡量两个分布的差异，KL 散度是它减去熵。"
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def prepared(self, wiki_file: str = WIKI_FILE, body: str = ""):
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = prepare_draft(
            tools,
            self.layout,
            wiki_file=wiki_file,
            body=body or self.BODY,
        )
        return tools, draft_version

    def publish(self, tools, draft_version: int, wiki_file: str = WIKI_FILE):
        return write_draft(
            tools,
            wiki_file=wiki_file,
            draft_version=draft_version,
        )

    def test_a_new_page_appears_in_the_index(self) -> None:
        tools, draft_version = self.prepared()

        self.publish(tools, draft_version)

        index = self.layout.wiki_index.read_text(encoding="utf-8")
        self.assertIn("[[concepts/信息熵]]", index)

    def test_a_publish_appends_one_write_log_entry(self) -> None:
        tools, draft_version = self.prepared()

        self.publish(tools, draft_version)

        log = self.layout.wiki_log.read_text(encoding="utf-8")
        self.assertIn("] write | 信息熵", log)
        self.assertIn(
            "- `wiki/concepts/信息熵.md` (draft 1)", log
        )
        self.assertEqual(log.count("] write |"), 1)

    def test_publishing_the_same_version_twice_appends_one_log_entry(
        self,
    ) -> None:
        tools, draft_version = self.prepared()
        self.publish(tools, draft_version)
        first = self.layout.wiki_log.read_text(encoding="utf-8")

        self.publish(tools, draft_version)

        self.assertEqual(
            self.layout.wiki_log.read_text(encoding="utf-8"), first
        )

    def test_the_structure_doctor_runs_and_its_findings_are_reported(
        self,
    ) -> None:
        tools, draft_version = self.prepared(
            body=self.BODY + " 参见 [[不存在的页]]。"
        )

        result = self.publish(tools, draft_version)

        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        codes = [item.get("code") for item in payload["warnings"]]
        self.assertIn("link.broken", codes)

    def test_existing_index_and_log_content_is_kept(self) -> None:
        self.layout.wiki_index.parent.mkdir(parents=True, exist_ok=True)
        self.layout.wiki_index.write_text(
            "# Custom Index\n\n## Notes\n\n- [[旧页]] — 旧页\n",
            encoding="utf-8",
        )
        self.layout.wiki_log.write_text(
            "## [2020-01-01] ingest | 旧来源\n\n- 旧记录\n",
            encoding="utf-8",
        )
        tools, draft_version = self.prepared()

        self.publish(tools, draft_version)

        index = self.layout.wiki_index.read_text(encoding="utf-8")
        self.assertIn("[[旧页]]", index)
        self.assertIn("[[concepts/信息熵]]", index)
        log = self.layout.wiki_log.read_text(encoding="utf-8")
        self.assertIn("旧记录", log)
        self.assertIn("] write |", log)

    def test_a_source_page_is_listed_under_sources(self) -> None:
        wiki_file = "wiki/sources/信息熵.md"
        tools, draft_version = self.prepared(wiki_file=wiki_file)

        self.publish(tools, draft_version, wiki_file=wiki_file)

        index = self.layout.wiki_index.read_text(encoding="utf-8")
        sources_start = index.index("## Sources")
        notes_start = index.index("## Notes")
        self.assertIn(
            "[[sources/信息熵]]",
            index[sources_start:notes_start],
        )

    def test_a_retry_repairs_the_index_after_a_failed_maintenance(
        self,
    ) -> None:
        tools, draft_version = self.prepared()
        with patch(
            "noteagent.writing.maintenance.update_wiki_index",
            side_effect=OSError("disk full"),
        ):
            first = self.publish(tools, draft_version)

        first_payload = json.loads(first.content)
        self.assertIn(
            "index_failed",
            [item.get("code") for item in first_payload["warnings"]],
        )
        self.assertNotIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )

        second = self.publish(tools, draft_version)

        second_payload = json.loads(second.content)
        self.assertEqual(
            second_payload["effectIds"], first_payload["effectIds"]
        )
        self.assertEqual(
            second_payload["writtenAt"], first_payload["writtenAt"]
        )
        self.assertNotIn(
            "index_failed",
            [item.get("code") for item in second_payload["warnings"]],
        )
        self.assertIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )
        log = self.layout.wiki_log.read_text(encoding="utf-8")
        self.assertEqual(log.count("(draft 1)"), 1)

    def test_a_retry_after_a_lost_mark_finishes_the_side_files(self) -> None:
        tools, draft_version = self.prepared()
        original = WritingStateStore.mark_draft_written
        calls = {"count": 0}

        def flaky(store, wiki_file, version, effect_ids, written_at):
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("the mark failed")
            return original(
                store, wiki_file, version, effect_ids, written_at
            )

        with patch.object(
            WritingStateStore, "mark_draft_written", flaky
        ):
            first = self.publish(tools, draft_version)
        self.assertTrue(first.meta.get("error"))
        self.assertNotIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "] write |",
            self.layout.wiki_log.read_text(encoding="utf-8"),
        )

        second = self.publish(tools, draft_version)

        self.assertFalse(second.meta.get("error"), second.content)
        self.assertIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )
        log = self.layout.wiki_log.read_text(encoding="utf-8")
        self.assertEqual(log.count("(draft 1)"), 1)

    def test_a_failed_index_update_is_a_warning_not_a_rollback(
        self,
    ) -> None:
        tools, draft_version = self.prepared()

        with patch(
            "noteagent.writing.maintenance.update_wiki_index",
            side_effect=OSError("disk full"),
        ):
            result = self.publish(tools, draft_version)

        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        codes = [item.get("code") for item in payload["warnings"]]
        self.assertIn("index_failed", codes)
        self.assertTrue(
            self.layout.root.joinpath(*WIKI_FILE.split("/")).is_file()
        )
        self.assertEqual(
            len(
                EffectJournal(self.layout).query(
                    kind=WIKI_PAGE_WRITE
                )
            ),
            1,
        )
        page = draft_pages(
            WritingStateStore(self.layout).load().draft
        )[0]
        self.assertEqual(page["status"], "written")
        # The log step does not depend on the index step.
        self.assertIn(
            "(draft 1)",
            self.layout.wiki_log.read_text(encoding="utf-8"),
        )

        rebuild_wiki_index(self.layout)

        self.assertIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )

    def test_a_failed_log_append_is_a_warning_not_a_rollback(
        self,
    ) -> None:
        tools, draft_version = self.prepared()

        with patch(
            "noteagent.writing.maintenance._append_log",
            side_effect=OSError("log locked"),
        ):
            result = self.publish(tools, draft_version)

        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        codes = [item.get("code") for item in payload["warnings"]]
        self.assertIn("log_failed", codes)
        self.assertTrue(
            self.layout.root.joinpath(*WIKI_FILE.split("/")).is_file()
        )
        self.assertIn(
            "[[concepts/信息熵]]",
            self.layout.wiki_index.read_text(encoding="utf-8"),
        )

    def test_a_failed_doctor_is_a_warning_not_a_rollback(self) -> None:
        tools, draft_version = self.prepared()

        with patch(
            "noteagent.writing.maintenance.doctor_after_write",
            side_effect=RuntimeError("doctor broke"),
        ):
            result = self.publish(tools, draft_version)

        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        codes = [item.get("code") for item in payload["warnings"]]
        self.assertIn("doctor_failed", codes)
        self.assertTrue(
            self.layout.root.joinpath(*WIKI_FILE.split("/")).is_file()
        )
        self.assertIn(
            "] write |",
            self.layout.wiki_log.read_text(encoding="utf-8"),
        )

    def test_doctor_findings_are_capped_so_the_answer_stays_parseable(
        self,
    ) -> None:
        findings = tuple(
            DoctorFinding(
                "warning", f"test.{index}", f"finding {index}"
            )
            for index in range(DOCTOR_FINDINGS_LIMIT + 5)
        )
        report = DoctorReport(
            findings=findings, content_pages=1, index_links=1
        )
        tools, draft_version = self.prepared()

        with patch(
            "noteagent.writing.maintenance.doctor_after_write",
            return_value=report,
        ):
            result = self.publish(tools, draft_version)

        payload = json.loads(result.content)
        listed = [
            item
            for item in payload["warnings"]
            if str(item.get("code", "")).startswith("test.")
        ]
        self.assertEqual(len(listed), DOCTOR_FINDINGS_LIMIT)
        codes = [item.get("code") for item in payload["warnings"]]
        self.assertIn("doctor_findings_omitted", codes)


if __name__ == "__main__":
    unittest.main()
