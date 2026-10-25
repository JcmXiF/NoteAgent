"""The answer is reported as it is written, and a tool round takes its words back."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.chat.query import run_query
from noteagent.chat.store import create_conversation, load_conversation
from noteagent.llm.client import LlmError, LlmTurn, ToolCall
from noteagent.project.init import init_project


class _ScriptedClient:
    """Hands out scripted turns, streaming each one the way the real client does."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def complete(self, messages, **kwargs):
        self.calls.append(kwargs)
        turn = self.script.pop(0)
        on_token = kwargs.get("on_token")
        # Only a streaming call reports the prose as it arrives, and it hands back what
        # was streamed rather than the original text; without one the answer would be
        # handed over whole, which is what this checks against.
        if on_token is None:
            return turn
        pieces = turn.content.split("|")
        for piece in pieces:
            on_token(piece)
        return LlmTurn(
            content="".join(pieces),
            tool_calls=turn.tool_calls,
            finish_reason=turn.finish_reason,
        )


def _tool_call() -> ToolCall:
    return ToolCall(
        id="call-1",
        name="wiki_read",
        arguments='{"targets": ["concepts/entropy"]}',
    )


class ChatQueryStreamTests(unittest.TestCase):
    def _run(self, script, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            client = _ScriptedClient(script)
            with (
                patch("noteagent.chat.query.load_llm_config", return_value=object()),
                patch("noteagent.chat.query.LlmClient", lambda *_: client),
                patch("noteagent.chat.query.observe_tool", lambda *a, **k: []),
            ):
                saved = run_query(layout, created.id, "什么是熵", **kwargs)
            return saved, client, load_conversation(layout, created.id)

    def test_the_answer_arrives_as_it_is_written(self) -> None:
        pieces: list[str] = []
        saved, client, _ = self._run(
            [LlmTurn(content="信息|熵|衡量不确定性")],
            on_token=pieces.append,
        )

        # Each piece reached the reader as it was written, and none of it twice.
        self.assertEqual(pieces, ["信息", "熵", "衡量不确定性"])
        self.assertEqual(saved.messages[-1].content, "信息熵衡量不确定性")
        # Streaming is what the core asked the client for, not a later re-send.
        self.assertIsNotNone(client.calls[0].get("on_token"))

    def test_a_tool_round_is_taken_back_and_the_answer_is_not(self) -> None:
        pieces: list[str] = []
        resets: list[int] = []
        saved, client, _ = self._run(
            [
                LlmTurn(content="我先查一下|再去写", tool_calls=(_tool_call(),)),
                LlmTurn(content="熵衡量|不确定性"),
            ],
            on_token=pieces.append,
            on_reset=lambda: resets.append(len(pieces)),
        )

        # The reader saw the first round's words, then had them taken back before the
        # answer started, so what they are left with is the answer alone.
        self.assertEqual(resets, [2])
        self.assertEqual(pieces, ["我先查一下", "再去写", "熵衡量", "不确定性"])
        self.assertEqual(pieces[resets[0] :], ["熵衡量", "不确定性"])
        self.assertEqual(saved.messages[-1].content, "熵衡量不确定性")

    def test_two_tool_rounds_are_each_taken_back(self) -> None:
        pieces: list[str] = []
        resets: list[int] = []
        self._run(
            [
                LlmTurn(content="先查", tool_calls=(_tool_call(),)),
                LlmTurn(content="再查", tool_calls=(_tool_call(),)),
                LlmTurn(content="答"),
            ],
            on_token=pieces.append,
            on_reset=lambda: resets.append(len(pieces)),
        )
        self.assertEqual(resets, [1, 2])

    def test_a_preamble_alongside_tool_calls_is_not_kept_as_the_answer(self) -> None:
        saved, _, _ = self._run(
            [
                LlmTurn(content="读一下", tool_calls=(_tool_call(),)),
                LlmTurn(content="答案"),
            ],
            on_token=lambda _text: None,
            on_reset=lambda: None,
        )
        # The preamble stays part of what the model was told, but it is not what was
        # written down as the answer.
        self.assertEqual(saved.messages[-1].content, "答案")

    def test_an_empty_answer_still_fails(self) -> None:
        with self.assertRaises(ValueError):
            self._run([LlmTurn(content="   ")], on_token=lambda _text: None)

    def test_a_round_that_fails_is_not_reported_as_an_answer(self) -> None:
        class _Broken:
            def __init__(self, config=None) -> None:
                pass

            def complete(self, messages, **kwargs):
                # The stream broke after it had already sent something.
                on_token = kwargs.get("on_token")
                if on_token is not None:
                    on_token("半句")
                raise LlmError("LLM stream failed: connection reset")

        pieces: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            created = create_conversation(layout, "t")
            with (
                patch("noteagent.chat.query.load_llm_config", return_value=object()),
                patch("noteagent.chat.query.LlmClient", _Broken),
            ):
                with self.assertRaises(LlmError):
                    run_query(layout, created.id, "什么是熵", on_token=pieces.append)
            loaded = load_conversation(layout, created.id)

        # The reader saw the half sentence, and nothing was written down: the turn is
        # reported as failed rather than as an answer that happens to be short.
        self.assertEqual(pieces, ["半句"])
        self.assertEqual([item.role for item in loaded.messages], ["user"])

    def test_without_a_reader_the_round_is_still_fetched_whole(self) -> None:
        saved, client, _ = self._run([LlmTurn(content="整段回答")])
        # No streaming was asked for, so the answer arrived in one piece and is saved.
        self.assertIsNone(client.calls[0].get("on_token"))
        self.assertEqual(saved.messages[-1].content, "整段回答")


if __name__ == "__main__":
    unittest.main()
