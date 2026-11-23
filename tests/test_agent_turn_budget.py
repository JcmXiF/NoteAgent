"""One model-turn bound: the cap forces one final answer without tools."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx
from agent_fakes import FixedClock, SequentialIds

from noteagent.agent.events import ErrorEvent, StatusEvent, TokenEvent
from noteagent.agent.kernel import LEAK_RETRY, MAX_TURNS_FINAL, KernelRuntime
from noteagent.agent.model import ModelErrorEvent
from noteagent.agent.runtime import UserInput
from noteagent.agent.tools import ToolResult, ToolSpec, ToolRegistry
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


def _echo(arguments, _context):
    return ToolResult(
        content=f"echo: {arguments['text']}",
        meta={"title": "echo"},
    )


def _leak() -> list[ModelErrorEvent]:
    return [
        ModelErrorEvent(
            "tool_call_leak", "the model wrote tool-call markup", retryable=True
        )
    ]


class TurnBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def runtime(
        self,
        model,
        *,
        max_turns: int = 50,
    ) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=ToolRegistry(
                [
                    ToolSpec(
                        name="echo",
                        description="Echo the text.",
                        parameters={
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"],
                        },
                        handler=_echo,
                    )
                ]
            ),
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
            max_turns=max_turns,
        )

    def messages(self) -> list[dict[str, object]]:
        return [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]

    def markers(self) -> list[dict[str, object]]:
        return [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "run_marker"
        ]

    def test_a_normal_answer_completes(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        model = FauxModelClient(FauxModelClient.text("答案").script)
        events = []

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.messages()[-1]["content"], "答案")
        self.assertEqual(self.markers()[-1]["state"], "finished")
        self.assertEqual(self.markers()[-1].get("note", ""), "")

    def test_a_turn_cap_forces_a_final_answer(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        call = FauxModelClient.tool_call(
            "t1", "echo", '{"text": "again"}'
        ).script
        model = FauxModelClient(
            call, call, FauxModelClient.text("最终答案").script
        )
        events = []

        result = self.runtime(model, max_turns=2).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(model.requests[-1].tools, ())
        self.assertTrue(
            any(
                message.role == "system"
                and MAX_TURNS_FINAL in message.content
                for message in model.requests[-1].messages
            )
        )
        self.assertEqual(self.messages()[-1]["content"], "最终答案")
        self.assertFalse(
            [event for event in events if isinstance(event, ErrorEvent)]
        )
        self.assertTrue(
            any(
                isinstance(event, StatusEvent)
                and "max_turns_exceeded" in event.message
                for event in events
            )
        )
        self.assertEqual(self.markers()[-1]["state"], "finished")
        self.assertEqual(
            self.markers()[-1]["note"], "max_turns_exceeded"
        )

    def test_a_leak_on_the_forced_call_is_not_retried(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        call = FauxModelClient.tool_call(
            "t1", "echo", '{"text": "again"}'
        ).script
        model = FauxModelClient(call, call, _leak())
        events = []

        result = self.runtime(model, max_turns=2).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(model.requests), 3)
        errors = [event for event in events if isinstance(event, ErrorEvent)]
        self.assertEqual(errors[-1].code, "tool_call_leak")
        self.assertEqual(self.markers()[-1]["state"], "failed")
        self.assertEqual(
            self.markers()[-1]["note"], "max_turns_exceeded"
        )

    def test_a_cap_whose_final_answer_is_empty_fails(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        call = FauxModelClient.tool_call(
            "t1", "echo", '{"text": "again"}'
        ).script
        model = FauxModelClient(
            call, call, FauxModelClient.text("").script
        )
        events = []

        result = self.runtime(model, max_turns=2).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(model.requests), 3)
        errors = [event for event in events if isinstance(event, ErrorEvent)]
        self.assertEqual(errors[-1].code, "model_error")
        self.assertEqual(self.markers()[-1]["state"], "failed")
        self.assertEqual(
            self.markers()[-1]["note"], "max_turns_exceeded"
        )

    def test_a_leak_is_retried_once(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        model = FauxModelClient(
            _leak(), FauxModelClient.text("答案").script
        )
        events = []

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 2)
        self.assertTrue(
            any(
                message.role == "system"
                and LEAK_RETRY in message.content
                for message in model.requests[1].messages
            )
        )
        self.assertTrue(
            any(
                isinstance(event, TokenEvent) and event.reset
                for event in events
            )
        )
        self.assertEqual(self.messages()[-1]["content"], "答案")

    def test_a_second_leak_fails(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        model = FauxModelClient(_leak(), _leak())
        events = []

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "failed")
        errors = [event for event in events if isinstance(event, ErrorEvent)]
        self.assertEqual(errors[-1].code, "tool_call_leak")
        self.assertEqual(len(model.requests), 2)

    def test_the_leak_note_is_not_repeated_after_recovery(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        model = FauxModelClient(
            _leak(),
            FauxModelClient.tool_call(
                "t1", "echo", '{"text": "hi"}'
            ).script,
            FauxModelClient.text("答案").script,
        )

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 3)
        self.assertFalse(
            any(
                message.role == "system"
                and LEAK_RETRY in message.content
                for message in model.requests[2].messages
            )
        )

    def test_the_adapter_recovery_executes_at_a_normal_turn(self) -> None:
        block = (
            "<｜｜DSML｜｜ calls>\n"
            '<｜｜DSML｜｜ invoke name="echo">\n'
            '<｜｜DSML｜｜ parameter name="text" string="true">again'
            "</｜｜DSML｜｜ parameter>\n"
            "</｜｜DSML｜｜ invoke>\n"
            "</｜｜DSML｜｜ calls>"
        )
        seen: list[dict] = []
        calls = {"n": 0}

        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            index = calls["n"]
            calls["n"] += 1
            if index == 0:
                body = sse(
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "t1",
                                            "function": {
                                                "name": "echo",
                                                "arguments": '{"text": "hi"}',
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                )
                body += sse(
                    {
                        "choices": [
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    }
                )
            elif index == 1:
                body = sse(
                    {"choices": [{"delta": {"content": block}}]}
                )
                body += sse(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]}
                )
            else:
                body = sse(
                    {"choices": [{"delta": {"content": "最终答案"}}]}
                )
                body += sse(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]}
                )
            body += "data: [DONE]\n\n"
            return httpx.Response(200, content=body.encode("utf-8"))

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http_client.close)
        adapter = OpenAIModelClient(
            LlmConfig("http://model.test/v1", "key", "model"),
            client=http_client,
        )
        events = []

        result = self.runtime(adapter).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(
            [entry["role"] for entry in self.messages()],
            [
                "user",
                "assistant",
                "toolResult",
                "assistant",
                "toolResult",
                "assistant",
            ],
        )
        self.assertEqual(self.messages()[-1]["content"], "最终答案")
        self.assertEqual(
            [
                entry["content"]
                for entry in self.messages()
                if entry["role"] == "toolResult"
            ],
            ["echo: hi", "echo: again"],
        )


if __name__ == "__main__":
    unittest.main()
