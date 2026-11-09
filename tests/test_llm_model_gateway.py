"""The OpenAI adapter speaks the one model contract the loop knows."""

from __future__ import annotations

import json
import unittest

import httpx

from noteagent.agent.model import (
    Message,
    ModelClient,
    ModelDoneEvent,
    ModelErrorEvent,
    ModelRequest,
    ToolCall,
    collect,
)
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient, _stream_events, openai_body


def _config() -> LlmConfig:
    return LlmConfig(
        base_url="http://model.test/v1",
        api_key="key",
        model="default-model",
        temperature=0.1,
        max_tokens=4096,
    )


def _request() -> ModelRequest:
    return ModelRequest(
        model="asked-model",
        messages=(
            Message("system", "you are a note agent"),
            Message("user", "什么是熵？"),
            Message(
                "assistant",
                "",
                tool_calls=(ToolCall("t1", "wiki_read", '{"targets":["a"]}'),),
            ),
            Message("tool", "熵是……", tool_call_id="t1", name="wiki_read"),
        ),
        tools=(
            {
                "type": "function",
                "function": {
                    "name": "wiki_read",
                    "description": "Open a wiki page.",
                    "parameters": {"type": "object"},
                },
            },
        ),
    )


def _response(*payloads: dict, done: bool = True) -> httpx.Response:
    body = "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
    if done:
        body += "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode("utf-8"))


class OpenAIStreamTests(unittest.TestCase):
    def test_a_finished_stream_ends_with_done(self) -> None:
        events = list(
            _stream_events(
                _response(
                    {
                        "choices": [
                            {"delta": {"content": "answer"}, "finish_reason": "stop"}
                        ]
                    }
                ),
                _request(),
            )
        )

        self.assertIsInstance(events[-1], ModelDoneEvent)
        self.assertEqual(collect(events).content, "answer")

    def test_a_cut_stream_is_an_error_not_a_finished_answer(self) -> None:
        events = list(
            _stream_events(
                _response(
                    {"choices": [{"delta": {"content": "half"}}]},
                    done=False,
                ),
                _request(),
            )
        )

        self.assertIsInstance(events[-1], ModelErrorEvent)
        self.assertEqual(events[-1].code, "model_error")
        self.assertTrue(events[-1].retryable)


class OpenAIBodyTests(unittest.TestCase):
    def test_the_request_becomes_the_wire_shape(self) -> None:
        body = openai_body(_request(), _config())

        self.assertEqual(body["model"], "asked-model")
        self.assertTrue(body["stream"])
        self.assertEqual(
            body["messages"][0],
            {"role": "system", "content": "you are a note agent"},
        )
        self.assertEqual(body["messages"][2]["tool_calls"][0]["id"], "t1")
        self.assertEqual(
            body["messages"][2]["tool_calls"][0]["function"]["name"], "wiki_read"
        )
        self.assertEqual(body["messages"][3]["tool_call_id"], "t1")
        self.assertEqual(body["messages"][3]["name"], "wiki_read")
        self.assertEqual(body["tools"][0]["function"]["name"], "wiki_read")
        self.assertEqual(body["tool_choice"], "auto")

    def test_the_config_fills_what_the_request_leaves_open(self) -> None:
        request = ModelRequest(model="", messages=(Message("user", "hi"),))
        body = openai_body(request, _config())

        self.assertEqual(body["model"], "default-model")
        self.assertEqual(body["temperature"], 0.1)
        self.assertEqual(body["max_tokens"], 4096)

    def test_a_request_can_override_the_knobs(self) -> None:
        request = ModelRequest(
            model="",
            messages=(Message("user", "hi"),),
            temperature=0.7,
            max_tokens=128,
        )
        body = openai_body(request, _config())
        self.assertEqual(body["temperature"], 0.7)
        self.assertEqual(body["max_tokens"], 128)

    def test_the_adapter_is_a_model_client(self) -> None:
        self.assertIsInstance(OpenAIModelClient(_config()), ModelClient)


if __name__ == "__main__":
    unittest.main()
