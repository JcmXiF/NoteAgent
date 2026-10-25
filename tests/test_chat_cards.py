"""What an answer looked at is kept with it, so reopening still says so."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.chat.limits import default_limits
from noteagent.chat.query import run_query
from noteagent.chat.store import (
    MAX_CARDS_PER_TURN,
    ChatConversation,
    ChatTurn,
    append_turn,
    conversation_from_dict,
    conversation_to_dict,
    create_conversation,
    empty_conversation,
    load_conversation,
    save_conversation,
)
from noteagent.chat.tools.types import ToolObservation
from noteagent.llm.client import LlmTurn, ToolCall
from noteagent.project.init import init_project


def observation(**overrides) -> ToolObservation:
    fields = {
        "tool": "search",
        "title": "熵",
        "target": "[[concepts/entropy]]",
        "content": "整页正文，很长，不该存进会话",
        "meta": {"scope": "wiki", "path": "wiki/concepts/entropy.md"},
    }
    fields.update(overrides)
    return ToolObservation(**fields)


class _ScriptedClient:
    def __init__(self, script) -> None:
        self.script = list(script)

    def complete(self, messages, **kwargs):
        return self.script.pop(0)


class CardsOnTheTurnTests(unittest.TestCase):
    def test_a_turn_keeps_its_cards_through_a_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            conversation = create_conversation(layout, "t")
            conversation = append_turn(
                conversation,
                role="assistant",
                content="答",
                limits=default_limits(),
                cards=[observation().as_event()],
            )
            save_conversation(layout, conversation)
            saved = load_conversation(layout, conversation.id)
            self.assertEqual(len(saved.messages), 1)
            self.assertEqual(saved.messages[0].cards[0]["path"], "wiki/concepts/entropy.md")

    def test_the_text_written_for_the_model_is_not_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            conversation = create_conversation(layout, "t")
            conversation = append_turn(
                conversation,
                role="assistant",
                content="答",
                limits=default_limits(),
                cards=[observation().as_event()],
            )
            save_conversation(layout, conversation)
            payload = load_conversation(layout, conversation.id).messages[0].cards[0]
            self.assertNotIn("content", payload)

    def test_a_question_has_no_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            conversation = create_conversation(layout, "t")
            conversation = append_turn(
                conversation,
                role="user",
                content="什么是熵",
                limits=default_limits(),
                cards=[observation().as_event()],
            )
            self.assertEqual(conversation.messages[0].cards, ())

    def test_cards_are_bounded_in_number_and_length(self) -> None:
        cards = [{"tool": "search", "title": f"标题 {index}"} for index in range(200)]
        cards.append({"tool": "search", "title": "长" * 1000})
        conversation = append_turn(
            empty_conversation("c-1"),
            role="assistant",
            content="答",
            limits=default_limits(),
            cards=cards,
        )
        kept = conversation.messages[0].cards
        self.assertEqual(len(kept), MAX_CARDS_PER_TURN)
        self.assertLessEqual(len(kept[-1]["title"]), 400)

    def test_only_plain_values_are_kept(self) -> None:
        conversation = append_turn(
            empty_conversation("c-1"),
            role="assistant",
            content="答",
            limits=default_limits(),
            cards=[{"tool": "search", "nested": {"x": 1}, "ok": True, "count": 2}],
        )
        card = conversation.messages[0].cards[0]
        self.assertEqual(card, {"tool": "search", "ok": True, "count": 2})

    def test_a_turn_without_cards_writes_nothing_extra(self) -> None:
        turn = ChatTurn(id="m-1", role="assistant", content="答", created_at="2026-01-01T00:00:00Z")
        payload = conversation_to_dict(
            ChatConversation(id="c-1", title="", updated_at="", messages=(turn,))
        )
        self.assertNotIn("cards", payload["messages"][0])

    def test_a_conversation_from_before_cards_still_reads(self) -> None:
        conversation = conversation_from_dict(
            {
                "id": "c-1",
                "title": "旧会话",
                "updatedAt": "2026-01-01T00:00:00Z",
                "messages": [
                    {
                        "id": "m-1",
                        "role": "assistant",
                        "content": "答",
                        "createdAt": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        )
        self.assertEqual(conversation.messages[0].cards, ())


class QueryKeepsCardsTests(unittest.TestCase):
    def _answer(self, script, observations):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        layout = init_project(Path(tmp.name) / "proj")
        created = create_conversation(layout, "t")
        with patch("noteagent.chat.query.load_llm_config", return_value=object()):
            with patch("noteagent.chat.query.LlmClient", lambda *_: _ScriptedClient(script)):
                with patch(
                    "noteagent.chat.query.observe_tool",
                    lambda *a, **k: tuple(observations),
                ):
                    run_query(layout, created.id, "什么是熵")
        return load_conversation(layout, created.id)

    def test_the_answer_carries_what_it_looked_at(self) -> None:
        saved = self._answer(
            [
                LlmTurn(content="查一下", tool_calls=(ToolCall("c1", "search", "{}"),)),
                LlmTurn(content="答"),
            ],
            [observation(title="甲"), observation(title="乙")],
        )
        answer = saved.messages[-1]
        self.assertEqual(answer.role, "assistant")
        self.assertEqual([card["title"] for card in answer.cards], ["甲", "乙"])
        self.assertNotIn("content", answer.cards[0])

    def test_an_answer_with_no_tools_keeps_no_cards(self) -> None:
        saved = self._answer([LlmTurn(content="答")], [observation()])
        self.assertEqual(saved.messages[-1].cards, ())


if __name__ == "__main__":
    unittest.main()
