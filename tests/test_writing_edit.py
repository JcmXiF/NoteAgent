"""A person's edits: versions, the run lease, the archive and its event."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.agent.runtime import UserInput
from noteagent.agent.tools import ToolContext
from noteagent.api.session_access import WritingLeaseRuntime
from noteagent.api.writing_events import iter_writing_events
from noteagent.project.init import init_project
from noteagent.writing.state import (
    WritingState,
    WritingStateBusy,
    WRITER_TTL_S,
    WritingStateConflict,
    WritingStateStore,
    draft_pages,
    draft_sections,
)
from writing_fixtures import writing_tools

PAGE = {
    "wikiFile": "wiki/a.md",
    "title": "A",
    "version": 1,
    "status": "draft",
    "sections": [
        {
            "id": "definition",
            "heading": "定义",
            "body": "旧正文",
            "citations": [
                {
                    "frameId": "f1",
                    "frameVersion": 1,
                    "blockIds": ["b1"],
                }
            ],
            "version": 1,
            "stale": False,
            "humanEdited": False,
        },
        {
            "id": "details",
            "heading": "细节",
            "body": "细节正文",
            "citations": [],
            "version": 1,
            "stale": False,
            "humanEdited": False,
        },
    ],
}


def _state(
    page=None,
    *,
    writer=None,
) -> WritingState:
    return WritingState(
        writer=writer,
        searches=(
            {
                "id": "s1",
                "query": "q",
                "status": "ok",
                "hits": [
                    {
                        "id": "h1",
                        "title": "旧标题",
                        "url": "u",
                        "snippet": "s",
                        "rank": 1,
                    }
                ],
            },
        ),
        selected_pages=(
            {
                "id": "p1",
                "hitId": "h1",
                "url": "u",
                "title": "P",
                "status": "ready",
                "blockCount": 2,
                "version": 1,
                "material": "raw/x.html",
            },
        ),
        frames=(
            {
                "id": "f1",
                "pageId": "p1",
                "pageVersion": 1,
                "blockIds": ["b1"],
                "version": 1,
                "source": "agent",
            },
        ),
        draft={"pages": [page or PAGE]},
    )


def _sections(state: WritingState):
    return draft_sections(draft_pages(state.draft)[0])


class _InnerRuntime:
    def __init__(self, store: WritingStateStore) -> None:
        self.store = store
        self.seen = []
        self.requests = []
        self.calls = 0

    def execute(self, request, sink):
        self.calls += 1
        self.requests.append(request)
        state = self.store.ensure_writer("agent", request.run_id)
        self.seen.append(state.writer)
        return "done"

    def cancel(self, session_id: str) -> None:
        pass

    def wait_state(self, session_id: str):
        return None

    def is_busy(self, session_id: str) -> bool:
        return False

    def compact(self, session_id: str, instructions: str = "") -> bool:
        return False


class WritingEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")
        self.clock = FixedClock()
        self.store = WritingStateStore(self.layout, clock=self.clock)

    def changes(self, *, body: str = "新正文") -> dict[str, object]:
        page = {
            **PAGE,
            "sections": [
                {**PAGE["sections"][0], "body": body},
                PAGE["sections"][1],
            ],
        }
        search = {
            **self.search_row(),
            "hits": [
                {
                    "id": "h1",
                    "title": "改过的标题",
                    "url": "u",
                    "snippet": "s",
                    "rank": 1,
                }
            ],
        }
        return {"searches": [search], "draft": {"pages": [page]}}

    def search_row(self) -> dict[str, object]:
        return {
            "id": "s1",
            "query": "q",
            "status": "ok",
            "hits": [
                {
                    "id": "h1",
                    "title": "旧标题",
                    "url": "u",
                    "snippet": "s",
                    "rank": 1,
                }
            ],
        }

    def test_a_human_edit_marks_the_touched_section(self) -> None:
        self.store.save(_state())

        saved = self.store.save_human(1, self.changes())

        self.assertEqual(saved.state_version, 2)
        self.assertEqual(
            saved.searches[0]["hits"][0]["title"], "改过的标题"
        )
        page = draft_pages(saved.draft)[0]
        sections = _sections(saved)
        self.assertEqual(sections[0]["body"], "新正文")
        self.assertEqual(sections[0]["version"], 2)
        self.assertTrue(sections[0]["humanEdited"])
        self.assertEqual(sections[1]["version"], 1)
        self.assertFalse(sections[1]["humanEdited"])
        self.assertEqual(page["version"], 2)
        self.assertEqual(page["status"], "draft")

    def test_a_save_without_changes_keeps_the_versions(self) -> None:
        self.store.save(_state())

        saved = self.store.save_human(
            1, {"draft": {"pages": [PAGE]}}
        )

        self.assertEqual(saved.state_version, 2)
        page = draft_pages(saved.draft)[0]
        self.assertEqual(page["version"], 1)
        self.assertEqual(
            [section["version"] for section in _sections(saved)],
            [1, 1],
        )
        self.assertFalse(
            any(section["humanEdited"] for section in _sections(saved))
        )

    def test_editing_a_written_page_returns_it_to_draft(self) -> None:
        self.store.save(
            _state(
                {
                    **PAGE,
                    "status": "written",
                    "writtenAt": "2026-11-10T09:00:00Z",
                    "effectIds": ["eff-1"],
                }
            )
        )

        saved = self.store.save_human(1, self.changes())

        page = draft_pages(saved.draft)[0]
        self.assertEqual(page["status"], "draft")
        self.assertEqual(page["version"], 2)
        self.assertEqual(page["effectIds"], ["eff-1"])

    def test_a_save_while_a_run_holds_the_state_is_busy(self) -> None:
        self.store.save(_state())
        held = self.store.ensure_writer("agent", "run-1")

        with self.assertRaises(WritingStateBusy) as caught:
            self.store.save_human(held.state_version, self.changes())
        self.assertIn("agent run-1", str(caught.exception))
        self.assertIn(
            f"retry after {WRITER_TTL_S // 60} minutes",
            str(caught.exception),
        )

        self.assertEqual(
            self.store.load().searches[0]["hits"][0]["title"],
            "旧标题",
        )
        released = self.store.release_writer("run-1")
        saved = self.store.save_human(
            released.state_version, self.changes()
        )
        self.assertEqual(
            saved.searches[0]["hits"][0]["title"], "改过的标题"
        )

    def test_archive_is_busy_while_a_run_holds_the_state(self) -> None:
        self.store.save(_state())
        held = self.store.ensure_writer("agent", "run-1")

        with self.assertRaises(WritingStateBusy):
            self.store.archive()

        self.assertEqual(
            self.store.load().state_version, held.state_version
        )

    def test_a_stale_writer_does_not_block_a_save(self) -> None:
        self.store.save(
            _state(
                writer={
                    "kind": "agent",
                    "id": "old",
                    "since": "2020-01-01T00:00:00Z",
                }
            )
        )

        saved = self.store.save_human(1, self.changes())

        self.assertEqual(saved.state_version, 2)
        self.assertIsNone(saved.writer)
        self.assertEqual(
            saved.searches[0]["hits"][0]["title"], "改过的标题"
        )

    def test_a_long_run_keeps_its_lease_by_writing(self) -> None:
        start = self.clock.now()
        self.store.ensure_writer("agent", "s1")
        self.clock._now = start + timedelta(minutes=10)
        self.store.ensure_writer("agent", "s1")
        self.clock._now = start + timedelta(minutes=20)

        with self.assertRaises(WritingStateBusy):
            self.store.save_human(
                self.store.load().state_version, {}
            )

    def test_a_save_cannot_mark_a_page_published(self) -> None:
        self.store.save(_state())
        page = {
            **{key: value for key, value in PAGE.items() if key != "title"},
            "status": "written",
            "writtenAt": "2026-11-10T09:00:00Z",
            "effectIds": ["fake"],
            "sections": [
                {**PAGE["sections"][0], "body": "改"}
            ],
        }

        saved = self.store.save_human(
            1, {"draft": {"pages": [page]}}
        )

        saved_page = draft_pages(saved.draft)[0]
        self.assertEqual(saved_page["status"], "draft")
        self.assertEqual(saved_page["effectIds"], [])
        self.assertEqual(saved_page["writtenAt"], "")
        self.assertEqual(saved_page["title"], "A")

    def test_editing_a_frame_marks_it_human(self) -> None:
        self.store.save(_state())
        frame = {
            **dict(self.store.load().frames[0]),
            "blockIds": ["b1", "b2"],
        }

        saved = self.store.save_human(1, {"frames": [frame]})

        self.assertEqual(saved.frames[0]["version"], 2)
        self.assertEqual(saved.frames[0]["source"], "human")
        self.assertEqual(
            saved.frames[0]["updatedAt"], "2026-11-10T09:00:00Z"
        )

    def test_a_save_from_an_older_version_is_a_conflict(self) -> None:
        self.store.save(_state())

        with self.assertRaises(WritingStateConflict):
            self.store.save_human(0, self.changes())

        self.assertEqual(
            self.store.load().searches[0]["hits"][0]["title"],
            "旧标题",
        )

    def test_a_duplicate_section_id_is_refused(self) -> None:
        self.store.save(_state())
        page = {
            **PAGE,
            "sections": [
                PAGE["sections"][0],
                {**PAGE["sections"][1], "id": "definition"},
            ],
        }

        with self.assertRaises(ValueError):
            self.store.save_human(
                1, {"draft": {"pages": [page]}}
            )

        self.assertEqual(self.store.load().state_version, 1)

    def test_archive_puts_the_state_away_and_activate_brings_it_back(
        self,
    ) -> None:
        self.store.save(_state())

        items = self.store.archive()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "000001")
        self.assertEqual(items[0]["searches"], 1)
        current = self.store.load()
        self.assertEqual(current.searches, ())
        self.assertEqual(draft_pages(current.draft), [])

        restored = self.store.activate("000001")

        self.assertEqual(
            restored.searches[0]["hits"][0]["title"], "旧标题"
        )
        self.assertEqual(
            len(draft_pages(restored.draft)[0]["sections"]), 2
        )
        self.assertGreater(
            restored.state_version, current.state_version
        )

    def test_the_event_stream_reports_the_next_state_version(self) -> None:
        events = iter_writing_events(self.layout, poll=0.01)
        try:
            first = next(events)
            self.assertIn(b"event: state", first)
            self.assertIn(b'"stateVersion": 0', first)

            self.store.save(_state())
            second = next(events)

            self.assertIn(b'"stateVersion": 1', second)
        finally:
            events.close()

    def test_a_writing_tool_takes_the_lease(self) -> None:
        tools = writing_tools(self.layout)

        tools.dispatch(
            "web_search",
            json.dumps({"query": "x"}),
            ToolContext("s1"),
        )

        self.assertEqual(self.store.load().writer["id"], "s1")
        with self.assertRaises(WritingStateBusy):
            self.store.save_human(
                self.store.load().state_version, {}
            )

    def test_a_run_holds_the_lease_for_its_whole_execute(self) -> None:
        inner = _InnerRuntime(self.store)
        runtime = WritingLeaseRuntime(inner, self.store)

        result = runtime.execute(UserInput("s1", "hi"), None)

        self.assertEqual(result, "done")
        self.assertEqual(inner.calls, 1)
        self.assertEqual(inner.seen[0]["kind"], "agent")
        self.assertTrue(inner.requests[0].run_id)
        self.assertNotEqual(inner.requests[0].run_id, "s1")
        self.assertEqual(
            inner.seen[0]["id"], inner.requests[0].run_id
        )
        self.assertIsNone(self.store.load().writer)

    def test_a_second_run_cannot_hold_the_lease_beside_a_node(self) -> None:
        inner = _InnerRuntime(self.store)
        runtime = WritingLeaseRuntime(inner, self.store)
        self.store.ensure_writer("node", "node-1")

        with self.assertRaises(WritingStateBusy):
            runtime.execute(UserInput("s1", "hi"), None)

        self.assertEqual(inner.calls, 1)
        self.assertEqual(self.store.load().writer["id"], "node-1")


if __name__ == "__main__":
    unittest.main()
