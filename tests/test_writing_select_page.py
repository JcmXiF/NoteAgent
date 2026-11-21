"""select_page fetches a hit's page into the writing state."""

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
from noteagent.pipeline.captures import CaptureInbox
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore, state_summary

PAGE_HTML = (
    "<html><body><article>"
    '<h2 data-na-block="b1">定义</h2>'
    '<p data-na-block="b2">熵是信息的平均量。</p>'
    "</article></body></html>"
)


def _cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="信息熵 - 维基百科",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="test",
        )
    ]


def _capture(
    *,
    status: int | None = 200,
    fidelity: str = "ok",
    reasons: tuple[str, ...] = (),
    html: str = PAGE_HTML,
) -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="信息熵",
        markdown="熵是信息的平均量。",
        html=html,
        capture_method="test",
        fidelity=fidelity,
        reasons=reasons,
        http_status=status,
        char_count=10,
    )


class WritingSelectPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def tools(self, fetch=lambda _url: _capture()):
        with patch(
            "noteagent.api.session_access.rag_backend", return_value=None
        ):
            return session_tools(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    web_search=_cards,
                    web_fetch=fetch,
                ),
                self.layout,
            )

    def hit(self, tools) -> str:
        tools.dispatch(
            "web_search",
            json.dumps({"query": "信息熵"}),
            ToolContext("s1"),
        )
        state = WritingStateStore(self.layout).load()
        return str(state.searches[0]["hits"][0]["id"])

    def select(self, tools, hit_id: str):
        return tools.dispatch(
            "select_page",
            json.dumps({"hitId": hit_id}),
            ToolContext("s1"),
        )

    def test_a_hit_becomes_a_ready_page_with_blocks(self) -> None:
        tools = self.tools()
        hit_id = self.hit(tools)

        result = self.select(tools, hit_id)

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["blockCount"], 2)
        page_id = payload["pageId"]

        state = WritingStateStore(self.layout).load()
        self.assertEqual(len(state.selected_pages), 1)
        page = state.selected_pages[0]
        self.assertEqual(page["id"], page_id)
        self.assertEqual(page["hitId"], hit_id)
        self.assertEqual(page["url"], "https://example.com/entropy")
        self.assertEqual(page["title"], "信息熵")
        self.assertEqual(page["status"], "ready")
        self.assertEqual(page["blockCount"], 2)
        self.assertTrue(
            (self.layout.writing_pages / f"{page_id}.json").is_file()
        )

        detail = tools.dispatch(
            "get_state",
            json.dumps({"pageId": page_id}),
            ToolContext("s1"),
        )
        blocks = json.loads(detail.content)
        self.assertEqual([block["id"] for block in blocks], ["b1", "b2"])
        summary = json.loads(
            tools.dispatch("get_state", "{}", ToolContext("s1")).content
        )
        self.assertEqual(
            summary["areas"]["selectedPages"], {"count": 1, "failed": 0}
        )

    def test_the_same_url_is_not_selected_twice(self) -> None:
        tools = self.tools()
        hit_id = self.hit(tools)
        first = json.loads(self.select(tools, hit_id).content)

        second = self.select(tools, hit_id)

        payload = json.loads(second.content)
        self.assertEqual(payload["status"], "already_selected")
        self.assertEqual(payload["pageId"], first["pageId"])
        self.assertEqual(len(WritingStateStore(self.layout).load().selected_pages), 1)

    def test_a_failed_fetch_is_recorded_and_raised(self) -> None:
        def failing(_url: str):
            raise OSError("the host is down")

        tools = self.tools(failing)
        hit_id = self.hit(tools)

        result = self.select(tools, hit_id)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("the host is down", result.content)
        state = WritingStateStore(self.layout).load()
        page = state.selected_pages[0]
        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["blockCount"], 0)
        self.assertEqual(page["errorKind"], "fetch_failed")
        self.assertIn("the host is down", page["error"])
        attention = state_summary(state)["attention"]
        self.assertEqual(attention[0]["kind"], "fetch_failed")
        self.assertEqual(attention[0]["target"], page["id"])

    def test_a_failed_page_can_be_selected_again(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> Capture:
            calls.append(url)
            if len(calls) == 1:
                raise OSError("the host is down")
            return _capture()

        tools = self.tools(fetch)
        hit_id = self.hit(tools)
        self.assertTrue(self.select(tools, hit_id).meta.get("error"))
        failed = WritingStateStore(self.layout).load().selected_pages[0]
        page_id = failed["id"]

        second = self.select(tools, hit_id)

        self.assertFalse(second.meta.get("error"), second.content)
        payload = json.loads(second.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["pageId"], page_id)
        self.assertEqual(payload["blockCount"], 2)
        state = WritingStateStore(self.layout).load()
        self.assertEqual(len(state.selected_pages), 1)
        self.assertEqual(state.selected_pages[0]["status"], "ready")
        self.assertEqual(state.selected_pages[0]["blockCount"], 2)
        self.assertEqual(state_summary(state)["attention"], [])

    def test_a_login_wall_is_attention(self) -> None:
        tools = self.tools(
            lambda _url: _capture(
                status=403,
                fidelity="failed",
                reasons=("http_forbidden",),
            )
        )
        hit_id = self.hit(tools)

        result = self.select(tools, hit_id)

        self.assertTrue(result.meta.get("error"))
        state = WritingStateStore(self.layout).load()
        page = state.selected_pages[0]
        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["blockCount"], 0)
        self.assertEqual(page["errorKind"], "login_wall")
        attention = state_summary(state)["attention"]
        self.assertEqual(attention[0]["kind"], "login_wall")
        inbox = CaptureInbox(self.layout.capture_inbox).payload()
        self.assertEqual(inbox["openCount"], 1)
        self.assertEqual(
            inbox["items"][0]["url"], "https://example.com/entropy"
        )

    def test_an_empty_capture_is_failed(self) -> None:
        tools = self.tools(
            lambda _url: _capture(
                status=200, fidelity="failed", reasons=("empty",)
            )
        )
        hit_id = self.hit(tools)

        result = self.select(tools, hit_id)

        self.assertTrue(result.meta.get("error"))
        page = WritingStateStore(self.layout).load().selected_pages[0]
        self.assertEqual(page["status"], "failed")
        self.assertEqual(page["blockCount"], 0)
        self.assertEqual(page["errorKind"], "fetch_failed")
        self.assertIn("empty", page["error"])

    def test_an_unknown_hit_is_an_error(self) -> None:
        result = self.select(self.tools(), "missing")

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such hit", result.content)
        self.assertEqual(
            WritingStateStore(self.layout).load().selected_pages, ()
        )


if __name__ == "__main__":
    unittest.main()
