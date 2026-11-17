"""A reader that goes away pauses its run instead of cancelling it."""

from __future__ import annotations

import threading
import unittest

from agent_fakes import FakeSessionStore, FixedClock, GatedModel, SequentialIds
from noteagent.agent.events import TokenEvent
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.runtime import UserInput
from noteagent.agent.tools import ToolRegistry, ToolResult, ToolSpec
from noteagent.api.sessions import session_payload


class PauseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeSessionStore()
        self.store.create("s1")

    def runtime(self, model, tools: ToolRegistry | None = None) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=tools or ToolRegistry(),
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )

    def test_a_pause_during_a_stream_discards_the_partial(self) -> None:
        pause = threading.Event()
        model = GatedModel("部分")

        def sink(event) -> None:
            if isinstance(event, TokenEvent) and event.text == "部分":
                pause.set()
                model.release.set()

        result = self.runtime(model).execute(
            UserInput("s1", "问题", pause=pause), sink
        )

        self.assertEqual(result.status, "interrupted")
        view = session_payload(self.store, "s1")
        self.assertEqual([item["role"] for item in view["messages"]], ["user"])
        self.assertEqual(
            view["state"]["interrupted"]["reason"], "client_disconnect"
        )

    def test_a_pause_at_a_tool_boundary_keeps_the_tool_result(self) -> None:
        pause = threading.Event()

        def stop(_arguments, _context):
            pause.set()
            return ToolResult(
                content="stopped",
                meta={"title": "stop", "slug": "stop"},
            )

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="stop",
                    description="Stop at the next boundary.",
                    parameters={"type": "object"},
                    handler=stop,
                )
            ]
        )
        model = FauxModelClient(
            FauxModelClient.tool_call("c1", "stop", "{}").script,
            FauxModelClient.text("不该调用").script,
        )

        result = self.runtime(model, tools).execute(
            UserInput("s1", "问题", pause=pause), lambda _event: None
        )

        self.assertEqual(result.status, "interrupted")
        self.assertEqual(len(model.requests), 1)
        entries = self.store.load("s1").entries
        tool_result = next(
            entry for entry in entries if entry.get("role") == "toolResult"
        )
        self.assertEqual(tool_result["content"], "stopped")
        self.assertEqual(
            session_payload(self.store, "s1")["state"]["interrupted"]["reason"],
            "client_disconnect",
        )

    def test_a_pause_before_the_first_model_call_does_not_call_the_model(
        self,
    ) -> None:
        pause = threading.Event()
        pause.set()
        model = FauxModelClient.text("不该调用")

        result = self.runtime(model).execute(
            UserInput("s1", "问题", pause=pause), lambda _event: None
        )

        self.assertEqual(result.status, "interrupted")
        self.assertEqual(model.requests, [])
        view = session_payload(self.store, "s1")
        self.assertEqual([item["role"] for item in view["messages"]], ["user"])
        self.assertEqual(
            view["state"]["interrupted"]["reason"], "client_disconnect"
        )


if __name__ == "__main__":
    unittest.main()
