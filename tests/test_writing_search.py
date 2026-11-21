"""web_search writes hits into the writing state; get_state reads them back."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.search_config import SearchError
from noteagent.acquire.types import CandidateCard
from noteagent.agent.tools import ToolContext
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_tools
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore, state_summary


def _cards(query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title=f"信息熵 - {query}",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="test",
        ),
        CandidateCard(
            title="KL 散度",
            url="https://example.com/kl",
            site="example.com",
            snippet="相对熵衡量两个分布的差异。",
            why="",
            via="test",
        ),
    ]


class WritingSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def tools(self, search=_cards):
        with patch(
            "noteagent.api.session_access.rag_backend", return_value=None
        ):
            return session_tools(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    web_search=search,
                ),
                self.layout,
            )

    def search(self, tools, query: str):
        return tools.dispatch(
            "web_search",
            json.dumps({"query": query}),
            ToolContext("s1"),
        )

    def test_a_search_puts_its_hits_in_the_state(self) -> None:
        tools = self.tools()

        result = self.search(tools, "信息熵")

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["count"], 2)
        search_id = payload["searchId"]

        state = WritingStateStore(self.layout).load()
        self.assertEqual(len(state.searches), 1)
        row = state.searches[0]
        self.assertEqual(row["id"], search_id)
        self.assertEqual(row["query"], "信息熵")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["hits"][0]["title"], "信息熵 - 信息熵")
        self.assertEqual(row["hits"][0]["snippet"], "熵是信息的平均量。")
        self.assertEqual(row["hits"][0]["rank"], 1)
        self.assertEqual(row["hits"][1]["rank"], 2)

    def test_get_state_reads_one_searchs_hits(self) -> None:
        tools = self.tools()
        search_id = json.loads(self.search(tools, "信息熵").content)["searchId"]

        detail = tools.dispatch(
            "get_state",
            json.dumps({"searchId": search_id}),
            ToolContext("s1"),
        )

        self.assertFalse(detail.meta.get("error"), detail.content)
        hits = json.loads(detail.content)
        self.assertEqual(
            [hit["title"] for hit in hits], ["信息熵 - 信息熵", "KL 散度"]
        )
        summary = json.loads(
            tools.dispatch("get_state", "{}", ToolContext("s1")).content
        )
        self.assertEqual(
            summary["areas"]["searches"],
            {
                "count": 1,
                "latest": {
                    "id": search_id,
                    "query": "信息熵",
                    "status": "ok",
                },
            },
        )

    def test_more_searches_are_kept(self) -> None:
        tools = self.tools()
        first = json.loads(self.search(tools, "信息熵").content)["searchId"]
        second = json.loads(self.search(tools, "KL 散度").content)["searchId"]

        state = WritingStateStore(self.layout).load()

        self.assertNotEqual(first, second)
        self.assertEqual([row["id"] for row in state.searches], [first, second])

    def test_a_failed_search_is_recorded_and_raised(self) -> None:
        def failing(_query: str):
            raise SearchError("the provider is down")

        tools = self.tools(failing)

        result = self.search(tools, "信息熵")

        self.assertTrue(result.meta.get("error"))
        self.assertIn("the provider is down", result.content)
        state = WritingStateStore(self.layout).load()
        self.assertEqual(state.searches[0]["status"], "failed")
        self.assertEqual(state.searches[0]["hits"], [])
        attention = state_summary(state)["attention"]
        self.assertEqual(attention[0]["kind"], "fetch_failed")
        self.assertIn("the provider is down", attention[0]["message"])

    def test_a_long_hit_list_is_truncated_with_a_diagnostic(self) -> None:
        def many(_query: str):
            return [
                CandidateCard(
                    title="t" * 500,
                    url="https://example.com/" + ("x" * 500),
                    site="example.com",
                    snippet="s" * 400,
                    why="",
                    via="test",
                )
                for _ in range(20)
            ]

        tools = self.tools(many)
        search_id = json.loads(self.search(tools, "long").content)[
            "searchId"
        ]

        detail = tools.dispatch(
            "get_state",
            json.dumps({"searchId": search_id}),
            ToolContext("s1"),
        )

        self.assertLessEqual(len(detail.content), 20_000)
        self.assertIn(
            "truncated", [item.code for item in detail.diagnostics]
        )
        self.assertEqual(
            len(WritingStateStore(self.layout).load().searches[0]["hits"]),
            20,
        )

    def test_an_unknown_search_id_is_an_error(self) -> None:
        result = self.tools().dispatch(
            "get_state",
            json.dumps({"searchId": "missing"}),
            ToolContext("s1"),
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such search", result.content)

    def test_a_blank_query_never_reaches_the_state(self) -> None:
        result = self.tools().dispatch(
            "web_search", json.dumps({"query": "   "}), ToolContext("s1")
        )

        self.assertTrue(result.meta.get("error"))
        self.assertEqual(WritingStateStore(self.layout).load().searches, ())


if __name__ == "__main__":
    unittest.main()
