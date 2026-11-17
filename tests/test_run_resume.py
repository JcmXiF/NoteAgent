"""Resuming an interrupted run, and abandoning one without inventing a result."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.run_marker import (
    ABANDONED,
    FINISHED,
    INTERRUPTED,
    RUNNING,
    RunMarker,
    marker_entry,
)
from noteagent.agent.runtime import RunResume
from noteagent.agent.tools import ToolRegistry
from noteagent.api.sessions import session_payload
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.sessions.recovery import (
    abandon_run,
    interrupted_run,
    recover_store,
)

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


class RunResumeKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def runtime(self, model) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=ToolRegistry(),
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )

    def user(self) -> None:
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "问题"}
        )

    def start(self) -> None:
        self.store.append("s1", marker_entry(RunMarker("run-1", RUNNING)))

    def assistant(self, *, tool_calls: bool = False, content: str = "答案") -> None:
        entry: dict[str, object] = {
            "type": "message",
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            entry["toolCalls"] = [
                {"id": "c1", "name": "wiki_read", "arguments": "{}"}
            ]
        self.store.append("s1", entry)

    def interrupt_after_tool_call(self) -> None:
        self.user()
        self.start()
        self.assistant(tool_calls=True)
        recover_store(self.store, clock=FixedClock())

    def entries(self):
        return self.store.load("s1").entries

    def markers(self):
        return [entry for entry in self.entries() if entry["type"] == "run_marker"]

    def test_resume_synthesizes_outcome_unknown_and_continues(self) -> None:
        self.interrupt_after_tool_call()
        model = FauxModelClient.text("继续回答")
        runtime = self.runtime(model)

        result = runtime.execute(RunResume("s1", "run-1"), lambda _event: None)

        self.assertEqual(result.status, "completed")
        unknown = [
            entry
            for entry in self.entries()
            if entry.get("type") == "message"
            and entry.get("role") == "toolResult"
            and entry.get("meta", {}).get("interrupted")
        ]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["toolCallId"], "c1")
        self.assertIn("outcome unknown", unknown[0]["content"])
        resumed = [
            marker for marker in self.markers() if marker.get("resumes") == "run-1"
        ]
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["state"], RUNNING)
        self.assertEqual(self.markers()[-1]["state"], FINISHED)
        self.assertIn("Resume after an interruption", resumed[0]["note"])
        self.assertIsNone(session_payload(self.store, "s1")["state"].get("interrupted"))
        request = model.requests[0]
        joined = "\n".join(message.content for message in request.messages)
        self.assertIn("Resume after an interruption", joined)
        self.assertIn("outcome unknown", joined)

    def test_resume_after_a_question_has_no_unknown_result(self) -> None:
        self.user()
        self.start()
        recover_store(self.store, clock=FixedClock())
        model = FauxModelClient.text("答案")

        result = self.runtime(model).execute(
            RunResume("s1", "run-1"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(
            any(
                entry.get("type") == "message"
                and entry.get("role") == "toolResult"
                and entry.get("meta", {}).get("interrupted")
                for entry in self.entries()
            )
        )
        self.assertIn(
            "Continue from the last committed turn",
            "\n".join(message.content for message in model.requests[0].messages),
        )

    def test_resume_after_a_tool_result_has_no_unknown_result(self) -> None:
        self.user()
        self.start()
        self.assistant(tool_calls=True)
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "toolResult",
                "toolCallId": "c1",
                "name": "wiki_read",
                "content": "页面内容",
            },
        )
        recover_store(self.store, clock=FixedClock())
        model = FauxModelClient.text("答案")

        result = self.runtime(model).execute(
            RunResume("s1", "run-1"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertFalse(
            any(
                entry.get("type") == "message"
                and entry.get("role") == "toolResult"
                and entry.get("meta", {}).get("interrupted")
                for entry in self.entries()
            )
        )

    def test_a_finished_run_refuses_resume(self) -> None:
        self.user()
        self.start()
        self.assistant()
        recover_store(self.store, clock=FixedClock())
        model = FauxModelClient.text("不该被调用")

        with self.assertRaises(ValueError):
            self.runtime(model).execute(
                RunResume("s1", "run-1"), lambda _event: None
            )
        self.assertEqual(model.requests, [])

    def test_a_waiting_run_refuses_resume(self) -> None:
        self.user()
        self.start()
        self.store.append(
            "s1",
            {
                "type": "wait_proposed",
                "id": "w1",
                "waitId": "w1",
                "job": "plan",
                "interface": "source",
                "completion": "start_plan",
                "proposedAt": "2026-11-10T09:00:00Z",
            },
        )
        recover_store(self.store, clock=FixedClock())
        model = FauxModelClient.text("不该被调用")

        with self.assertRaises(ValueError):
            self.runtime(model).execute(
                RunResume("s1", "run-1"), lambda _event: None
            )
        self.assertEqual(model.requests, [])

    def test_a_duplicate_resume_is_refused(self) -> None:
        self.interrupt_after_tool_call()
        model = FauxModelClient.text("继续回答")
        runtime = self.runtime(model)
        runtime.execute(RunResume("s1", "run-1"), lambda _event: None)
        before = len(model.requests)

        with self.assertRaises(ValueError):
            runtime.execute(RunResume("s1", "run-1"), lambda _event: None)
        self.assertEqual(len(model.requests), before)

    def test_abandon_is_idempotent_and_writes_no_unknown(self) -> None:
        self.interrupt_after_tool_call()

        marker, changed = abandon_run(
            self.store, "s1", "run-1", clock=FixedClock()
        )

        self.assertTrue(changed)
        assert marker is not None
        self.assertEqual(marker.state, ABANDONED)
        self.assertEqual(self.markers()[-1]["state"], ABANDONED)
        self.assertFalse(
            any(
                entry.get("type") == "message"
                and entry.get("role") == "toolResult"
                and entry.get("meta", {}).get("interrupted")
                for entry in self.entries()
            )
        )
        self.assertIsNone(interrupted_run(self.store, "s1", "run-1"))
        _, changed_again = abandon_run(
            self.store, "s1", "run-1", clock=FixedClock()
        )
        self.assertFalse(changed_again)
        self.assertEqual(
            len(
                [
                    entry
                    for entry in self.entries()
                    if entry["type"] == "run_marker"
                    and entry["state"] == ABANDONED
                ]
            ),
            1,
        )


class RunResumeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        if TestClient is None or create_app is None or AppDeps is None:
            raise unittest.SkipTest("fastapi is not installed")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")
        self.user()
        self.start()
        self.assistant(tool_calls=True)
        recover_store(self.store, clock=FixedClock())

    def client(self, text: str = "继续回答") -> TestClient:
        return TestClient(
            create_app(
                AppDeps(
                    sessions=self.store,
                    model=FauxModelClient.text(text),
                    settings={"model": "faux"},
                )
            )
        )

    def user(self) -> None:
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "问题"}
        )

    def start(self) -> None:
        self.store.append("s1", marker_entry(RunMarker("run-1", RUNNING)))

    def assistant(self, *, tool_calls: bool = False) -> None:
        entry: dict[str, object] = {
            "type": "message",
            "role": "assistant",
            "content": "答案",
        }
        if tool_calls:
            entry["toolCalls"] = [
                {"id": "c1", "name": "wiki_read", "arguments": "{}"}
            ]
        self.store.append("s1", entry)

    def test_resume_streams_the_continuation(self) -> None:
        response = self.client().post(
            "/api/v1/sessions/s1/resume", json={"runId": "run-1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("继续回答", response.text)
        self.assertTrue(
            any(
                entry.get("role") == "toolResult"
                and entry.get("meta", {}).get("interrupted")
                for entry in self.store.load("s1").entries
            )
        )

    def test_abandon_answers_and_is_idempotent(self) -> None:
        client = self.client()

        first = client.post(
            "/api/v1/sessions/s1/abandon", json={"runId": "run-1"}
        )
        second = client.post(
            "/api/v1/sessions/s1/abandon", json={"runId": "run-1"}
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["abandoned"])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["abandoned"])
        self.assertEqual(
            self.store.load("s1").entries[-1]["state"], ABANDONED
        )
        self.assertIsNone(
            session_payload(self.store, "s1")["state"].get("interrupted")
        )

    def test_a_settled_run_is_refused(self) -> None:
        self.store.append("s1", marker_entry(RunMarker("run-1", FINISHED)))
        client = self.client()

        resume = client.post(
            "/api/v1/sessions/s1/resume", json={"runId": "run-1"}
        )
        abandon = client.post(
            "/api/v1/sessions/s1/abandon", json={"runId": "run-1"}
        )

        self.assertEqual(resume.status_code, 409)
        self.assertEqual(resume.json()["code"], "session.notInterrupted")
        self.assertEqual(abandon.status_code, 409)
        self.assertEqual(abandon.json()["code"], "session.notInterrupted")


if __name__ == "__main__":
    unittest.main()
