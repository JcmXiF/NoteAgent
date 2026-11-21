"""select_frame turns block ids on one selected page into a frame."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.tools import ToolContext
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_tools
from noteagent.ingest.clips import MAX_BLOCKS_PER_COLLECTION
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore

PAGE_HTML = (
    "<html><body><article>"
    + "".join(
        f'<p data-na-block="b{index}">段落 {index}</p>'
        for index in range(1, 5)
    )
    + "</article></body></html>"
)


def _cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="信息熵",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="test",
        )
    ]


def _capture(_url: str) -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="信息熵",
        markdown="段落 1 段落 2 段落 3 段落 4",
        html=PAGE_HTML,
        capture_method="test",
        fidelity="ok",
        reasons=(),
        http_status=200,
        char_count=100,
    )


class WritingSelectFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def tools(self):
        with patch(
            "noteagent.api.session_access.rag_backend", return_value=None
        ):
            return session_tools(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    web_search=_cards,
                    web_fetch=_capture,
                ),
                self.layout,
            )

    def page(self, tools) -> str:
        tools.dispatch(
            "web_search",
            json.dumps({"query": "信息熵"}),
            ToolContext("s1"),
        )
        state = WritingStateStore(self.layout).load()
        hit_id = str(state.searches[0]["hits"][0]["id"])
        result = tools.dispatch(
            "select_page",
            json.dumps({"hitId": hit_id}),
            ToolContext("s1"),
        )
        return str(json.loads(result.content)["pageId"])

    def frame(self, tools, page_id: str, block_ids: list[str]):
        return tools.dispatch(
            "select_frame",
            json.dumps({"pageId": page_id, "blockIds": block_ids}),
            ToolContext("s1"),
        )

    def test_blocks_become_a_frame(self) -> None:
        tools = self.tools()
        page_id = self.page(tools)

        result = self.frame(tools, page_id, ["b2", "b3"])

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["blockCount"], 2)
        frame_id = payload["frameId"]

        state = WritingStateStore(self.layout).load()
        self.assertEqual(len(state.frames), 1)
        row = state.frames[0]
        self.assertEqual(row["id"], frame_id)
        self.assertEqual(row["pageId"], page_id)
        self.assertEqual(row["blockIds"], ["b2", "b3"])
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["source"], "agent")
        self.assertIn("pageVersion", row)

        detail = tools.dispatch(
            "get_state",
            json.dumps({"framesFor": page_id}),
            ToolContext("s1"),
        )
        frames = json.loads(detail.content)
        self.assertEqual([item["id"] for item in frames], [frame_id])
        self.assertEqual(frames[0]["blockIds"], ["b2", "b3"])
        all_frames = json.loads(
            tools.dispatch(
                "get_state",
                json.dumps({"section": "frames"}),
                ToolContext("s1"),
            ).content
        )
        self.assertEqual([item["id"] for item in all_frames], [frame_id])
        self.assertEqual(all_frames[0]["pageId"], page_id)
        summary = json.loads(
            tools.dispatch("get_state", "{}", ToolContext("s1")).content
        )
        self.assertEqual(summary["areas"]["frames"], {"count": 1})

    def test_an_empty_frame_is_an_error(self) -> None:
        tools = self.tools()
        page_id = self.page(tools)

        result = self.frame(tools, page_id, [])

        self.assertTrue(result.meta.get("error"))
        self.assertEqual(WritingStateStore(self.layout).load().frames, ())

    def test_an_unknown_page_is_an_error(self) -> None:
        tools = self.tools()

        result = self.frame(tools, "missing", ["b1"])

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such page", result.content)
        self.assertEqual(WritingStateStore(self.layout).load().frames, ())

    def test_an_unknown_block_is_an_error(self) -> None:
        tools = self.tools()
        page_id = self.page(tools)

        result = self.frame(tools, page_id, ["b9"])

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such block", result.content)
        self.assertEqual(WritingStateStore(self.layout).load().frames, ())

    def test_too_many_blocks_is_an_error(self) -> None:
        tools = self.tools()
        page_id = self.page(tools)

        result = self.frame(
            tools,
            page_id,
            [f"b{index}" for index in range(MAX_BLOCKS_PER_COLLECTION + 1)],
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("at most", result.content)
        self.assertEqual(WritingStateStore(self.layout).load().frames, ())


if __name__ == "__main__":
    unittest.main()
