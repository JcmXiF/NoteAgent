"""One streamed call to a model, and the one message it adds up to."""

from __future__ import annotations

import unittest

from agent_fakes import FakeModelClient
from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelClient,
    ModelDoneEvent,
    ModelErrorEvent,
    ModelRequest,
    ModelStartEvent,
    ModelStreamError,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCallDelta,
    ModelToolCallEnd,
    ModelUsageEvent,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolCallBlock,
    Usage,
    collect,
)


def _request() -> ModelRequest:
    return ModelRequest(model="m", messages=(Message("user", "hi"),))


def _done(**overrides) -> ModelDoneEvent:
    values = {"blocks": (), "stop_reason": ""}
    values.update(overrides)
    return ModelDoneEvent(AssistantMessage(**values))


class CollectTests(unittest.TestCase):
    def test_text_deltas_become_the_answer(self) -> None:
        message = collect(
            [
                ModelStartEvent("m"),
                ModelTextDelta("En"),
                ModelTextDelta("tropy"),
                _done(stop_reason="stop"),
            ]
        )
        self.assertEqual(message.content, "Entropy")
        self.assertEqual(message.stop_reason, "stop")

    def test_a_completed_message_is_not_rebuilt_from_the_deltas(self) -> None:
        # A provider may send the whole message on ``done``; that is the answer,
        # even if the deltas were dropped or coalesced along the way.
        message = collect(
            [
                ModelTextDelta("partial"),
                _done(blocks=(TextBlock("the whole answer"),)),
            ]
        )
        self.assertEqual(message.content, "the whole answer")

    def test_thinking_is_kept_apart_from_the_answer(self) -> None:
        message = collect(
            [
                ModelThinkingDelta("weigh "),
                ModelThinkingDelta("options"),
                ModelTextDelta("answer"),
                _done(),
            ]
        )
        self.assertEqual(message.thinking, "weigh options")
        self.assertEqual(message.content, "answer")

    def test_blocks_keep_the_order_the_model_wrote_them_in(self) -> None:
        call = ToolCall("c1", "wiki_read", '{"targets":["a"]}')
        message = collect(
            [
                ModelThinkingDelta("plan"),
                ModelTextDelta("answer"),
                ModelToolCallEnd(call),
                _done(),
            ]
        )
        self.assertEqual(
            [type(block) for block in message.blocks],
            [ThinkingBlock, TextBlock, ToolCallBlock],
        )
        self.assertEqual(message.tool_calls, (call,))

    def test_a_thinking_signature_survives_the_collected_message(self) -> None:
        message = collect(
            [
                _done(
                    blocks=(
                        ThinkingBlock("plan", signature="sig-1"),
                        TextBlock("answer"),
                    )
                )
            ]
        )
        self.assertEqual(message.blocks[0].signature, "sig-1")

    def test_tool_calls_arrive_whole_when_their_arguments_stop_streaming(self) -> None:
        call = ToolCall("c1", "wiki_read", '{"targets":["a"]}')
        message = collect(
            [
                ModelToolCallDelta(index=0, call_id="c1", name="wiki_read", arguments='{"targets":'),
                ModelToolCallDelta(index=0, call_id="", name="", arguments='["a"]}'),
                ModelToolCallEnd(call),
                _done(stop_reason="tool_calls"),
            ]
        )
        self.assertEqual(message.tool_calls, (call,))

    def test_usage_is_carried_out_of_the_stream(self) -> None:
        usage = Usage(input_tokens=12, output_tokens=5, cache_read_tokens=8)
        message = collect(
            [
                ModelUsageEvent(usage),
                _done(blocks=(TextBlock("answer"),)),
            ]
        )
        self.assertEqual(message.usage, usage)

    def test_an_error_event_ends_the_stream(self) -> None:
        with self.assertRaises(ModelStreamError) as caught:
            collect([ModelErrorEvent("model_error", "the model refused", retryable=True)])
        self.assertEqual(caught.exception.code, "model_error")
        self.assertTrue(caught.exception.retryable)

    def test_a_stream_that_never_finishes_is_an_error(self) -> None:
        with self.assertRaises(ModelStreamError):
            collect([ModelTextDelta("half an answer")])


class ModelSeamTests(unittest.TestCase):
    def test_a_client_only_has_to_offer_a_stream(self) -> None:
        self.assertIsInstance(FakeModelClient(), ModelClient)

    def test_the_fake_streams_a_message(self) -> None:
        self.assertEqual(collect(FakeModelClient().stream(_request())).content, "answer")


if __name__ == "__main__":
    unittest.main()
