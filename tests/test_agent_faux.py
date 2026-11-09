"""A scripted model, so a run can be tested without a network or a key."""

from __future__ import annotations

import unittest

from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    Message,
    ModelClient,
    ModelRequest,
    ModelStreamError,
    ToolCall,
    collect,
)


def _request() -> ModelRequest:
    return ModelRequest(model="faux", messages=(Message("user", "hi"),))


class FauxModelTests(unittest.TestCase):
    def test_a_text_script_streams_the_answer_in_pieces(self) -> None:
        client = FauxModelClient.text("熵衡量不确定性", chunks=["熵衡量", "不确定性"])

        self.assertIsInstance(client, ModelClient)
        events = list(client.stream(_request()))

        self.assertEqual(
            [event.kind for event in events],
            ["start", "text_delta", "text_delta", "done"],
        )
        self.assertEqual(collect(events).content, "熵衡量不确定性")

    def test_the_request_is_recorded_for_the_test_to_read(self) -> None:
        client = FauxModelClient.text("answer")
        list(client.stream(_request()))

        self.assertEqual(client.requests[0].messages[0].content, "hi")

    def test_a_second_call_takes_the_second_script(self) -> None:
        client = FauxModelClient(
            FauxModelClient.text("first").script,
            FauxModelClient.text("second").script,
        )
        first = collect(client.stream(_request()))
        second = collect(client.stream(_request()))
        self.assertEqual((first.content, second.content), ("first", "second"))

    def test_a_tool_call_script_ends_with_the_call(self) -> None:
        client = FauxModelClient.tool_call(
            "t1", "wiki_read", '{"targets":["concepts/entropy"]}', text="我先查一下。"
        )

        message = collect(client.stream(_request()))

        self.assertEqual(message.content, "我先查一下。")
        self.assertEqual(
            message.tool_calls,
            (ToolCall("t1", "wiki_read", '{"targets":["concepts/entropy"]}'),),
        )

    def test_an_unscripted_call_is_an_error(self) -> None:
        with self.assertRaises(ModelStreamError):
            collect(FauxModelClient().stream(_request()))


if __name__ == "__main__":
    unittest.main()
