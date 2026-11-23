"""The auto-frame node: one scoped run, its result, lease and recovery."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelErrorEvent,
    ModelStartEvent,
    ModelToolCallEnd,
    ToolCall,
)
from noteagent.agent.projection import project
from noteagent.agent.run_marker import (
    RUNNING,
    RunFingerprint,
    RunMarker,
    config_digest,
    marker_entry,
)
from noteagent.agent.tools import toolset_name
from noteagent.api.nodes import auto_frame_instruction, node_tools
from noteagent.project.init import init_project
from noteagent.project.run_fingerprint import project_fingerprint
from noteagent.sessions.recovery import recover_store
from noteagent.writing.state import (
    WritingState,
    WritingStateBusy,
    WritingStateStore,
)

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _auto_frame_model() -> FauxModelClient:
    return FauxModelClient(
        FauxModelClient.tool_call(
            "c1", "get_state", '{"pageId": "p1"}'
        ).script,
        FauxModelClient.tool_call(
            "c2",
            "select_frame",
            '{"pageId": "p1", "blockIds": ["b1", "b2"]}',
        ).script,
        FauxModelClient.text("DONE").script,
    )


@unittest.skipIf(TestClient is None, "fastapi not installed")
class NodeRunApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        self.layout = init_project(repo / "projects" / "demo")
        patched = patch.object(projects_mod, "REPO_ROOT", repo)
        patched.start()
        self.addCleanup(patched.stop)
        projects_mod.use_project("demo")
        self.store = FakeSessionStore()
        self.clock = FixedClock()
        self.ids = SequentialIds()
        self.writing = WritingStateStore(
            self.layout, clock=self.clock
        )
        self.writing.save_page_blocks(
            "p1",
            [{"id": "b1"}, {"id": "b2"}, {"id": "b3"}],
        )
        self.writing.save(
            WritingState(
                selected_pages=(
                    {
                        "id": "p1",
                        "hitId": "h1",
                        "url": "u",
                        "title": "P",
                        "status": "ready",
                        "blockCount": 3,
                        "version": 1,
                        "material": "raw/x.html",
                    },
                ),
            )
        )

    def client(self, model) -> "TestClient":
        deps = AppDeps(
            sessions=self.store,
            model=model,
            clock=self.clock,
            ids=self.ids,
        )
        return TestClient(create_app(deps))

    def test_auto_frame_writes_the_frames_into_the_state(self) -> None:
        model = _auto_frame_model()
        client = self.client(model)

        response = client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["sessionId"], "node-1")
        self.assertEqual(payload["status"], "completed", payload)
        self.assertEqual(
            [frame["blockIds"] for frame in payload["frames"]],
            [["b1", "b2"]],
        )
        state = self.writing.load()
        self.assertEqual(state.frames[0]["pageId"], "p1")
        self.assertIsNone(state.writer)
        tools = [
            tool["function"]["name"]
            for tool in model.requests[0].tools
        ]
        self.assertEqual(sorted(tools), ["get_state", "select_frame"])

    def test_the_node_session_stays_out_of_the_chat_list(self) -> None:
        client = self.client(_auto_frame_model())

        client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        listed = client.get("/api/v1/sessions").json()["sessions"]
        self.assertEqual(listed, [])
        entries = self.store.load("node-1").entries
        self.assertTrue(
            any(entry.get("type") == "run_marker" for entry in entries)
        )
        self.assertTrue(
            any(entry.get("type") == "message" for entry in entries)
        )

    def test_another_tool_is_not_offered_to_the_node(self) -> None:
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "c1", "web_search", '{"query": "x"}'
            ).script,
            FauxModelClient.text("DONE").script,
        )
        client = self.client(model)

        response = client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["frames"], [])
        second = model.requests[1].messages
        self.assertTrue(
            any("unknown tool" in str(message.content) for message in second)
        )

    def test_a_save_is_busy_while_the_node_runs(self) -> None:
        writing = self.writing

        class CheckingModel(FauxModelClient):
            def __init__(self, *scripts):
                super().__init__(*scripts)
                self.busy: bool | None = None
                self.checked = False

            def stream(self, request):
                if not self.checked:
                    self.checked = True
                    current = writing.load()
                    try:
                        writing.save_human(current.state_version, {})
                        self.busy = False
                    except WritingStateBusy:
                        self.busy = True
                yield from super().stream(request)

        model = CheckingModel(
            FauxModelClient.tool_call(
                "c1", "get_state", '{"pageId": "p1"}'
            ).script,
            FauxModelClient.tool_call(
                "c2",
                "select_frame",
                '{"pageId": "p1", "blockIds": ["b1"]}',
            ).script,
            FauxModelClient.text("DONE").script,
        )
        client = self.client(model)

        response = client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(model.busy)
        self.assertIsNone(self.writing.load().writer)
        current = self.writing.load()
        self.writing.save_human(current.state_version, {})

    def test_a_failed_node_leaves_a_visible_attention_row(self) -> None:
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelErrorEvent(
                    "model_error",
                    "the model refused",
                    retryable=True,
                ),
            ]
        )
        client = self.client(model)

        response = client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "failed")
        self.assertIn("the model refused", payload["error"])
        state = self.writing.load()
        self.assertEqual(state.attention[-1]["kind"], "blocked")
        self.assertEqual(state.attention[-1]["target"], "p1")
        self.assertIsNone(state.writer)

    def test_an_interrupted_node_resumes_and_writes_the_frames(
        self,
    ) -> None:
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "c2",
                "select_frame",
                '{"pageId": "p1", "blockIds": ["b1"]}',
            ).script,
            FauxModelClient.text("DONE").script,
        )
        deps = AppDeps(
            sessions=self.store,
            model=model,
            clock=self.clock,
            ids=self.ids,
        )
        self.store.create("node-1", "Auto-frame")
        self.store.append(
            "node-1",
            {"type": "node", "node": "auto-frame", "pageId": "p1"},
        )
        self.store.append(
            "node-1",
            {
                "type": "message",
                "role": "user",
                "content": auto_frame_instruction("p1"),
            },
        )
        fingerprint = project_fingerprint(self.layout, config={})
        self.store.append(
            "node-1",
            marker_entry(
                RunMarker(
                    "run-1",
                    RUNNING,
                    fingerprint=RunFingerprint.from_mapping(
                        fingerprint,
                        model="",
                        toolset=toolset_name(
                            node_tools(deps, self.layout)
                        ),
                        config_hash=config_digest(
                            fingerprint.get("configHash", ""),
                            project(
                                self.store.load("node-1").entries
                            ).sections,
                        ),
                    ),
                )
            ),
        )
        self.store.append(
            "node-1",
            {
                "type": "message",
                "role": "assistant",
                "content": "",
                "toolCalls": [
                    {
                        "id": "c1",
                        "name": "get_state",
                        "arguments": '{"pageId": "p1"}',
                    }
                ],
            },
        )
        recover_store(self.store, clock=self.clock)

        response = TestClient(create_app(deps)).post(
            "/api/projects/demo/nodes/node-1/resume",
            json={"runId": "run-1"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "completed", payload)
        self.assertEqual(
            [frame["blockIds"] for frame in payload["frames"]],
            [["b1"]],
        )
        state = self.writing.load()
        self.assertEqual(state.frames[0]["blockIds"], ["b1"])
        self.assertIsNone(state.writer)

    def test_a_non_node_id_is_refused_for_resume(self) -> None:
        client = self.client(_auto_frame_model())

        response = client.post(
            "/api/projects/demo/nodes/not-a-node/resume",
            json={"runId": "run-1"},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "node.notFound")

    def test_an_unknown_node_id_is_refused_for_resume(self) -> None:
        client = self.client(_auto_frame_model())

        response = client.post(
            "/api/projects/demo/nodes/node-missing/resume",
            json={"runId": "run-1"},
        )

        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "node.notFound")

    def test_a_refused_start_leaves_no_session_behind(self) -> None:
        self.writing.ensure_writer("node", "node-other")
        client = self.client(_auto_frame_model())

        response = client.post(
            "/api/projects/demo/nodes/auto-frame",
            json={"pageId": "p1"},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "state.busy")
        self.assertEqual(list(self.store.list()), [])

    def test_a_running_node_can_be_cancelled(self) -> None:
        class BlockingModel:
            def __init__(self) -> None:
                self.started = threading.Event()
                self.release = threading.Event()

            def stream(self, request):
                self.started.set()
                self.release.wait(5)
                yield ModelStartEvent("faux")
                yield ModelToolCallEnd(
                    ToolCall(
                        "c1",
                        "select_frame",
                        '{"pageId": "p1", "blockIds": ["b1"]}',
                    )
                )
                yield ModelDoneEvent(
                    AssistantMessage(stop_reason="tool_calls")
                )

        model = BlockingModel()
        deps = AppDeps(
            sessions=self.store,
            model=model,
            clock=self.clock,
            ids=self.ids,
        )
        app = create_app(deps)
        canceller = TestClient(app)
        responses: list = []

        def start() -> None:
            responses.append(
                TestClient(app).post(
                    "/api/projects/demo/nodes/auto-frame",
                    json={"pageId": "p1", "nodeId": "node-cancel"},
                )
            )

        thread = threading.Thread(target=start)
        thread.start()
        try:
            self.assertTrue(model.started.wait(5))
            cancelled = canceller.post(
                "/api/projects/demo/nodes/node-cancel/cancel"
            )
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.assertTrue(cancelled.json()["cancelled"])
        finally:
            model.release.set()
            thread.join(5)

        self.assertTrue(responses)
        self.assertEqual(responses[0].status_code, 200, responses[0].text)
        self.assertEqual(responses[0].json()["status"], "cancelled")
        self.assertIsNone(self.writing.load().writer)


if __name__ == "__main__":
    unittest.main()
