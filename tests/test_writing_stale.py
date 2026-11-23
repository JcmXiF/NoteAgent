"""Stale sections, missing sources, and provenance that survives an archive."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.agent.tools import ToolContext
from noteagent.ingest.source_citations import list_source_citations
from noteagent.project.init import init_project
from noteagent.writing.state import (
    WritingState,
    WritingStateStore,
    draft_pages,
    draft_sections,
    state_summary,
)
from writing_fixtures import (
    prepare_draft,
    write_draft,
    writing_tools,
)

WIKI_FILE = "wiki/a.md"


def _state(
    *,
    page_version: int = 1,
    frame_version: int = 1,
    frame_blocks: list[str] | None = None,
    frame_page_version: int = 1,
    section_frame_version: int = 1,
    section_blocks: list[str] | None = None,
    frame_present: bool = True,
    reason: str = "",
) -> WritingState:
    frame = {
        "id": "f1",
        "pageId": "p1",
        "pageVersion": frame_page_version,
        "blockIds": (
            frame_blocks if frame_blocks is not None else ["b1"]
        ),
        "version": frame_version,
        "source": "agent",
    }
    section = {
        "id": "definition",
        "heading": "定义",
        "body": "正文",
        "citations": [
            {
                "frameId": "f1",
                "frameVersion": section_frame_version,
                "blockIds": (
                    section_blocks
                    if section_blocks is not None
                    else ["b1"]
                ),
            }
        ],
        "version": 1,
        "stale": bool(reason),
        "staleReason": reason,
        "humanEdited": False,
    }
    return WritingState(
        selected_pages=(
            {
                "id": "p1",
                "url": "u",
                "title": "P",
                "status": "ready",
                "blockCount": 2,
                "version": page_version,
                "material": "raw/sources/web/example.com/x.html",
            },
        ),
        frames=(frame,) if frame_present else (),
        draft={
            "pages": [
                {
                    "wikiFile": WIKI_FILE,
                    "title": "A",
                    "version": 1,
                    "status": "draft",
                    "sections": [section],
                }
            ]
        },
    )


def _section(state: WritingState):
    return draft_sections(draft_pages(state.draft)[0])[0]


class WritingStaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.clock = FixedClock()
        self.store = WritingStateStore(self.layout, clock=self.clock)
        self.store.save_page_blocks(
            "p1", [{"id": "b1"}, {"id": "b2"}]
        )

    def test_editing_a_frame_marks_the_section_stale(self) -> None:
        self.store.save(_state())
        frame = {
            **self.store.load().frames[0],
            "blockIds": ["b1", "b2"],
        }

        saved = self.store.save_human(1, {"frames": [frame]})

        section = _section(saved)
        self.assertTrue(section["stale"])
        self.assertEqual(section["staleReason"], "frame_changed")
        self.assertEqual(saved.frames[0]["version"], 2)
        self.assertEqual(saved.frames[0]["source"], "human")

    def test_deleting_a_frame_makes_the_target_missing(self) -> None:
        self.store.save(_state())

        saved = self.store.save_human(1, {"frames": []})

        section = _section(saved)
        self.assertTrue(section["stale"])
        self.assertEqual(section["staleReason"], "frame_missing")
        self.assertEqual(saved.frames, ())

    def test_deleting_a_selected_page_makes_the_target_missing(
        self,
    ) -> None:
        self.store.save(_state())

        saved = self.store.save_human(1, {"selectedPages": []})

        section = _section(saved)
        self.assertEqual(section["staleReason"], "page_missing")
        self.assertEqual(saved.selected_pages, ())

    def test_a_page_version_drift_marks_the_section_stale(self) -> None:
        self.store.save(_state(page_version=2))

        saved = self.store.save_human(1, {})

        section = _section(saved)
        self.assertEqual(section["staleReason"], "page_changed")

    def test_a_gone_pinned_block_is_missing(self) -> None:
        self.store.save_page_blocks("p1", [{"id": "b2"}])
        self.store.save(_state())

        saved = self.store.save_human(1, {})

        section = _section(saved)
        self.assertEqual(section["staleReason"], "blocks_missing")

    def test_a_page_title_edit_does_not_stale_the_section(self) -> None:
        self.store.save(_state())
        page = {
            **self.store.load().selected_pages[0],
            "title": "新标题",
        }

        saved = self.store.save_human(
            1, {"selectedPages": [page]}
        )

        section = _section(saved)
        self.assertFalse(section["stale"])
        self.assertEqual(section["staleReason"], "")
        self.assertEqual(saved.selected_pages[0]["title"], "新标题")
        self.assertEqual(saved.selected_pages[0]["version"], 1)

    def test_the_summary_attention_names_the_stale_section(self) -> None:
        self.store.save(_state(reason="frame_changed"))

        summary = state_summary(self.store.load())

        item = summary["attention"][0]
        self.assertEqual(item["kind"], "stale_section")
        self.assertEqual(item["reason"], "frame_changed")
        self.assertEqual(item["target"], "wiki/a.md#definition")

    def test_get_state_shows_the_reason_in_the_draft_and_section(
        self,
    ) -> None:
        self.store.save(_state(reason="frame_missing"))
        tools = writing_tools(self.layout)

        summary = json.loads(
            tools.dispatch(
                "get_state", "{}", ToolContext("s1")
            ).content
        )
        draft = json.loads(
            tools.dispatch(
                "get_state",
                json.dumps({"section": "draft"}),
                ToolContext("s1"),
            ).content
        )
        detail = json.loads(
            tools.dispatch(
                "get_state",
                json.dumps(
                    {
                        "section": "section",
                        "wikiFile": WIKI_FILE,
                        "sectionId": "definition",
                    }
                ),
                ToolContext("s1"),
            ).content
        )

        self.assertEqual(
            summary["attention"][0]["kind"], "missing_target"
        )
        self.assertEqual(
            summary["attention"][0]["reason"], "frame_missing"
        )
        self.assertEqual(
            draft[0]["sections"][0]["staleReason"], "frame_missing"
        )
        self.assertTrue(detail["stale"])
        self.assertEqual(detail["staleReason"], "frame_missing")


class WritingPublishStaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.clock = FixedClock()
        self.store = WritingStateStore(self.layout, clock=self.clock)

    def prepare(self):
        tools = writing_tools(self.layout)
        page_id, frame_id, _ = prepare_draft(
            tools,
            self.layout,
            wiki_file=WIKI_FILE,
            blocks=("b2",),
        )
        return tools, page_id, frame_id

    def write(self, tools):
        return write_draft(tools, wiki_file=WIKI_FILE)

    def test_refreshing_a_selected_page_marks_its_sections_stale(
        self,
    ) -> None:
        tools, _page_id, _frame_id = self.prepare()
        state = self.store.load()
        hit_id = str(state.searches[0]["hits"][0]["id"])
        before = int(state.selected_pages[0].get("version") or 0)

        result = tools.dispatch(
            "select_page",
            json.dumps({"hitId": hit_id, "refresh": True}),
            ToolContext("s1"),
        )

        self.assertFalse(result.meta.get("error"), result.content)
        refreshed = self.store.load()
        self.assertGreater(
            int(refreshed.selected_pages[0].get("version") or 0),
            before,
        )
        self.assertEqual(
            _section(refreshed)["staleReason"], "page_changed"
        )

    def test_a_drifted_frame_still_publishes_with_a_warning(self) -> None:
        tools, _page_id, frame_id = self.prepare()
        state = self.store.load()
        released = self.store.release_writer("s1")
        frame = {
            **state.frames[0],
            "blockIds": ["b2", "b3"],
        }
        self.store.save_human(
            released.state_version, {"frames": [frame]}
        )
        self.assertEqual(
            _section(self.store.load())["staleReason"],
            "frame_changed",
        )

        result = self.write(tools)

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(
            payload["warnings"],
            [{"section": "definition", "reason": "frame_changed"}],
        )
        page = draft_pages(self.store.load().draft)[0]
        self.assertEqual(page["status"], "written")
        self.assertTrue(
            (self.layout.root / WIKI_FILE).is_file()
        )

    def test_a_drifted_page_still_publishes_with_a_warning(self) -> None:
        tools, _page_id, _frame_id = self.prepare()
        state = self.store.load()
        version = int(state.selected_pages[0].get("version") or 0)
        page = {**state.selected_pages[0], "version": version + 1}
        self.store.save(
            self.store.refresh_provenance(
                replace(state, selected_pages=(page,))
            )
        )
        self.assertEqual(
            _section(self.store.load())["staleReason"],
            "page_changed",
        )

        result = self.write(tools)

        self.assertFalse(result.meta.get("error"), result.content)
        self.assertEqual(
            json.loads(result.content)["warnings"],
            [{"section": "definition", "reason": "page_changed"}],
        )

    def test_a_deleted_frame_refuses_the_publish(self) -> None:
        tools, _page_id, _frame_id = self.prepare()
        self.store.release_writer("s1")
        self.store.save_human(
            self.store.load().state_version, {"frames": []}
        )

        result = self.write(tools)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such frame", result.content)
        self.assertFalse(
            (self.layout.root / WIKI_FILE).exists()
        )

    def test_a_deleted_page_refuses_the_publish(self) -> None:
        tools, _page_id, _frame_id = self.prepare()
        self.store.release_writer("s1")
        self.store.save_human(
            self.store.load().state_version, {"selectedPages": []}
        )

        result = self.write(tools)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such page", result.content)
        self.assertFalse(
            (self.layout.root / WIKI_FILE).exists()
        )

    def test_a_gone_pinned_block_refuses_the_publish(self) -> None:
        tools, page_id, _frame_id = self.prepare()
        self.store.save_page_blocks(page_id, [{"id": "b1"}])

        result = self.write(tools)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such block", result.content)
        self.assertFalse(
            (self.layout.root / WIKI_FILE).exists()
        )

    def test_the_marker_still_resolves_after_the_state_is_archived(
        self,
    ) -> None:
        tools, _page_id, _frame_id = self.prepare()
        self.write(tools)
        state = self.store.load()
        material = str(state.selected_pages[0]["material"])
        before = list_source_citations(self.layout).marks.get(
            material, []
        )
        self.assertTrue(before)
        self.store.release_writer("s1")

        self.store.archive()

        after = list_source_citations(self.layout).marks.get(
            material, []
        )
        self.assertEqual(
            [(item.id, item.block_ids) for item in after],
            [(item.id, item.block_ids) for item in before],
        )
        self.assertTrue((self.layout.root / material).is_file())
        current = self.store.load()
        self.assertEqual(current.searches, ())
        self.assertEqual(draft_pages(current.draft), [])


if __name__ == "__main__":
    unittest.main()
