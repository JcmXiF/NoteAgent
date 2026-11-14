"""One coding-agent-style turn bound: no low tool-round cap, no forced answer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx
from agent_fakes import FixedClock, SequentialIds

from noteagent.agent.events import ErrorEvent, TokenEvent, WaitEvent
from noteagent.agent.kernel import LEAK_RETRY, KernelRuntime
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

    def test_a_lone_propose_is_allowed(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        model = FauxModelClient(
            FauxModelClient.tool_call(
                "p1",
                "propose",
                json.dumps(
                    {"job": "plan", "interface": "source"}
                ),
            ).script
        )
        runtime = self.runtime(model)
        events = []

        result = runtime.execute(UserInput("s1", "整理"), events.append)

        self.assertEqual(result.status, "waiting")
        self.assertEqual(len(model.requests), 1)
        wait = runtime.wait_state("s1")
        self.assertIsNotNone(wait)
        assert wait is not None
        self.assertEqual(wait.status, "proposed")
        self.assertTrue(
            any(isinstance(event, WaitEvent) for event in events)
        )
        self.assertFalse(
            any(
                entry.get("role") == "toolResult"
                for entry in self.store.load("s1").entries
            )
        )

    def test_max_turns_is_a_hard_bound(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        call = FauxModelClient.tool_call(
            "t1", "echo", '{"text": "again"}'
        ).script
        model = FauxModelClient(call, call, call)
        events = []

        result = self.runtime(model, max_turns=2).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(model.requests), 2)
        errors = [event for event in events if isinstance(event, ErrorEvent)]
        self.assertEqual(errors[-1].code, "max_turns_exceeded")
        self.assertEqual(
            [entry["role"] for entry in self.messages()],
            ["user", "assistant", "toolResult", "assistant", "toolResult"],
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
