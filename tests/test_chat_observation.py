"""What the assistant looked at is reported as data, not only as prose."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.chat import iter_chat_query_sse
from noteagent.chat.query import run_query
from noteagent.chat.store import create_conversation
from noteagent.chat.tools.types import EVENT_META, ToolObservation
from noteagent.llm.client import LlmTurn, ToolCall
from noteagent.project.init import init_project


def observation(**overrides) -> ToolObservation:
    fields = {
        "tool": "search",
        "title": "熵",
        "target": "[[concepts/entropy]]",
        "content": "整页正文，很长，不该发给界面",
        "meta": {"scope": "wiki", "path": "wiki/concepts/entropy.md", "slug": "concepts/entropy"},
    }
    fields.update(overrides)
    return ToolObservation(**fields)


class AsEventTests(unittest.TestCase):
    def test_it_carries_what_was_touched_and_where_it_is(self) -> None:
        event = observation().as_event()
        self.assertEqual(event["tool"], "search")
        self.assertEqual(event["title"], "熵")
        self.assertEqual(event["target"], "[[concepts/entropy]]")
        self.assertEqual(event["path"], "wiki/concepts/entropy.md")
        self.assertEqual(event["scope"], "wiki")

    def test_the_text_written_for_the_model_does_not_travel(self) -> None:
        # `content` is a whole page or a whole search result; a card does not show it.
        self.assertNotIn("content", observation().as_event())

    def test_it_sends_the_fields_the_interface_uses_and_no_others(self) -> None:
        event = observation(meta={"scope": "wiki", "secret": "internal"}).as_event()
        self.assertIn("scope", event)
        self.assertNotIn("secret", event)

    def test_a_tool_that_says_nothing_extra_still_reports_itself(self) -> None:
        event = observation(meta={}).as_event()
        self.assertEqual(set(event), {"tool", "title", "target"})

    def test_the_whitelist_is_what_the_interface_is_told_about(self) -> None:
        # Every name here has to be one an interface could act on.
        for key in EVENT_META:
            self.assertNotIn(key, {"content", "blocks", "html", "markdown"})

    def test_the_missing_and_refused_marks_travel(self) -> None:
        self.assertTrue(observation(meta={"missing": True}).as_event()["missing"])
        self.assertTrue(
            observation(meta={"missing": True, "unsafe": True}).as_event()["unsafe"]
        )
        self.assertTrue(observation(meta={"needs_human": True}).as_event()["needs_human"])

    def test_a_search_hit_on_the_web_keeps_its_address(self) -> None:
        event = observation(
            tool="search",
            target="https://example.com/a",
            meta={"scope": "web", "url": "https://example.com/a", "not_source": True},
        ).as_event()
        self.assertEqual(event["url"], "https://example.com/a")
        self.assertTrue(event["not_source"])

    def test_what_a_write_did_travels(self) -> None:
        # The interface needs to tell "proposed, nothing written" from "wrote it", and
        # to send the reader to where the confirmation happens.
        event = observation(
            tool="wiki_write",
            title="propose_topic_map",
            meta={"action": "propose_topic_map", "wrote_wiki": False, "needs_human": True},
        ).as_event()
        self.assertEqual(event["action"], "propose_topic_map")
        self.assertFalse(event["wrote_wiki"])
        self.assertTrue(event["needs_human"])


class _ScriptedClient:
    def __init__(self, script) -> None:
        self.script = list(script)

    def complete(self, messages, **kwargs):
        return self.script.pop(0)


class QueryReportsObservationsTests(unittest.TestCase):
    def _run(self, script, observations, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            with patch("noteagent.chat.query.load_llm_config", return_value=object()):
                with patch("noteagent.chat.query.LlmClient", lambda *_: _ScriptedClient(script)):
                    with patch(
                        "noteagent.chat.query.observe_tool",
                        lambda *a, **k: tuple(observations),
                    ):
                        run_query(layout, created.id, "什么是熵", **kwargs)

    def test_every_result_of_a_tool_call_is_reported(self) -> None:
        seen: list[ToolObservation] = []
        self._run(
            [
                LlmTurn(content="查一下", tool_calls=(ToolCall("c1", "search", "{}"),)),
                LlmTurn(content="答"),
            ],
            [observation(title="甲"), observation(title="乙")],
            on_observation=seen.append,
        )
        # A search gives several results, and each is a separate thing it looked at.
        self.assertEqual([item.title for item in seen], ["甲", "乙"])

    def test_a_round_with_no_tools_reports_nothing(self) -> None:
        seen: list[ToolObservation] = []
        self._run([LlmTurn(content="答")], [observation()], on_observation=seen.append)
        self.assertEqual(seen, [])

    def test_each_round_reports_its_own_results(self) -> None:
        seen: list[str] = []
        self._run(
            [
                LlmTurn(content="一", tool_calls=(ToolCall("c1", "search", "{}"),)),
                LlmTurn(content="二", tool_calls=(ToolCall("c2", "search", "{}"),)),
                LlmTurn(content="答"),
            ],
            [observation(title="甲")],
            on_observation=lambda item: seen.append(item.title),
        )
        self.assertEqual(seen, ["甲", "甲"])


class SseReportsObservationsTests(unittest.TestCase):
    def test_the_event_reaches_the_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            with patch("noteagent.api.chat.run_query") as run:
                def fake(layout_, conversation_id, content, **kwargs):
                    reported = kwargs.get("on_observation")
                    self.assertIsNotNone(reported)
                    reported(observation())
                    # The worker turns this into the `done` payload, so it has to be
                    # what `run_query` really answers with.
                    return created

                run.side_effect = fake
                blob = b"".join(
                    iter_chat_query_sse(layout, created.id, "什么是熵")
                ).decode("utf-8")

        events = [line for line in blob.splitlines() if line.startswith("event: ")]
        self.assertIn("event: observation", events)
        payload = next(
            line for line in blob.splitlines() if line.startswith("data: ") and "search" in line
        )
        data = json.loads(payload[len("data: ") :])
        self.assertEqual(data["tool"], "search")
        self.assertEqual(data["target"], "[[concepts/entropy]]")
        # The answer's own stream is unaffected: the observed text stays out of it.
        self.assertNotIn("整页正文", blob)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
