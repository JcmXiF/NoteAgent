"""The composition root: the app is built from the parts a test hands it."""

from __future__ import annotations

import unittest

from agent_fakes import (
    FakeJobClient,
    FakeModelClient,
    FakeRuntime,
    FakeSessionStore,
    FakeWaitStore,
    FixedClock,
    SequentialIds,
)
from noteagent.agent.clock import SystemClock, UuidIds
from noteagent.agent.events import ObservationEvent, TokenEvent, is_terminal
from noteagent.agent.jobs import JobClient
from noteagent.agent.model import ModelClient
from noteagent.agent.runtime import AgentRuntime, UserInput
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import ToolRegistry, ToolResult, ToolSpec

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _read_page(arguments, context):
    return ToolResult(content="page", meta={"slug": arguments["slug"]})


def _deps() -> AppDeps:
    model = FakeModelClient("answer")
    tools = ToolRegistry(
        [
            ToolSpec(
                name="wiki_read",
                description="Open a wiki page.",
                parameters={"type": "object", "properties": {"slug": {"type": "string"}}},
                handler=_read_page,
            )
        ]
    )
    return AppDeps(
        clock=FixedClock(),
        ids=SequentialIds(),
        tools=tools,
        runtime=FakeRuntime(model, tools),
        model=model,
        sessions=FakeSessionStore(),
        waits=FakeWaitStore(),
        jobs=FakeJobClient(),
        settings={"model": "faux"},
    )


@unittest.skipIf(TestClient is None, "fastapi not installed")
class AppFactoryTests(unittest.TestCase):
    def test_the_app_keeps_the_dependencies_it_was_given(self) -> None:
        deps = _deps()
        app = create_app(deps)
        self.assertIs(app.state.deps, deps)

    def test_the_kernel_health_route_answers_through_the_factory(self) -> None:
        from noteagent import __version__

        client = TestClient(create_app(_deps()))
        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(), {"status": "ok", "version": __version__}
        )

    def test_the_existing_health_route_still_answers(self) -> None:
        client = TestClient(create_app(_deps()))
        self.assertEqual(client.get("/api/health").json(), {"status": "ok"})

    def test_production_wiring_uses_the_real_clock_and_ids(self) -> None:
        deps = create_app().state.deps
        self.assertIsInstance(deps.clock, SystemClock)
        self.assertIsInstance(deps.ids, UuidIds)
        # The wiki tools are built per run from the active project, not once at
        # startup, because the reader may switch projects while the app runs.
        self.assertIsNone(deps.tools)

    def test_the_injected_model_and_tool_run_through_the_factory(self) -> None:
        # The factory is the composition root: the runtime a test reaches
        # through ``app.state.deps`` really calls the model and tool it was
        # given, rather than a module-level client.
        events = []
        result = _deps().runtime.execute(UserInput("s1", "hi"), events.append)

        self.assertEqual(result.status, "completed")
        self.assertIn(TokenEvent("answer"), events)
        cards = [event.payload() for event in events if isinstance(event, ObservationEvent)]
        self.assertEqual(cards, [{"slug": "concepts/entropy"}])
        self.assertTrue(is_terminal(events[-1]))


class InjectedSeamTests(unittest.TestCase):
    def test_the_injected_parts_satisfy_the_kernel_contracts(self) -> None:
        deps = _deps()
        self.assertIsInstance(deps.runtime, AgentRuntime)
        self.assertIsInstance(deps.model, ModelClient)
        self.assertIsInstance(deps.sessions, SessionStore)
        self.assertIsInstance(deps.waits, WaitStore)
        self.assertIsInstance(deps.jobs, JobClient)


if __name__ == "__main__":
    unittest.main()
