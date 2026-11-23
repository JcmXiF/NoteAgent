"""Stop the process at each run boundary, then start a new kernel on the log.

The scenario is the same at every boundary: a run committed some entries, the
process stopped before it could continue, and a fresh kernel reads the same
store. The transcript, not an in-memory run, has to tell the new kernel where
the run stopped.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.events import TokenEvent
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.run_marker import (
    FINISHED,
    INTERRUPTED,
    RUNNING,
    RunMarker,
    marker_entry,
)
from noteagent.agent.runtime import RunResume
from noteagent.agent.tools import ToolRegistry
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.sessions.recovery import interrupted_run, recover_store


class RunFaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self._sessions = 0

    def session(self) -> str:
        self._sessions += 1
        session_id = f"s{self._sessions}"
        self.store.create(session_id)
        return session_id

    def append(self, session: str, entry: dict[str, object]) -> None:
        self.store.append(session, entry)

    def user(self, session: str, content: str = "问题") -> None:
        self.append(
            session,
            {"type": "message", "role": "user", "content": content},
        )

    def start(self, session: str, run_id: str = "run-1") -> None:
        self.append(session, marker_entry(RunMarker(run_id, RUNNING)))

    def assistant(
        self,
        session: str,
        *,
        tool_calls: bool = False,
        content: str = "答案",
    ) -> None:
        entry: dict[str, object] = {
            "type": "message",
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            entry["toolCalls"] = [
                {"id": "c1", "name": "wiki_read", "arguments": "{}"}
            ]
        self.append(session, entry)

    def tool_result(self, session: str) -> None:
        self.append(
            session,
            {
                "type": "message",
                "role": "toolResult",
                "toolCallId": "c1",
                "name": "wiki_read",
                "content": "页面内容",
            },
        )

    def finish(self, session: str, run_id: str = "run-1") -> None:
        self.append(session, marker_entry(RunMarker(run_id, FINISHED)))

    def kernel(self, text: str = "答案") -> tuple[KernelRuntime, FauxModelClient]:
        model = FauxModelClient.text(text)
        return (
            KernelRuntime(
                model=model,
                sessions=self.store,
                tools=ToolRegistry(),
                clock=FixedClock(),
                ids=SequentialIds(),
                model_name="faux",
            ),
            model,
        )

    def entries(
        self, session: str
    ) -> tuple[Mapping[str, object], ...]:
        return self.store.load(session).entries

    def markers(self, session: str) -> list[Mapping[str, object]]:
        return [
            entry
            for entry in self.entries(session)
            if entry["type"] == "run_marker"
        ]

    def seed_after_question(self, session: str) -> str:
        """The user turn was committed, but no start marker followed it."""
        self.user(session, "第一个问题")
        self.start(session)
        self.assistant(session)
        self.finish(session)
        self.user(session, "第二个问题")
        return ""

    def seed_after_start(self, session: str) -> str:
        self.user(session)
        self.start(session)
        return "run-1"

    def seed_after_tool_call(self, session: str) -> str:
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)
        return "run-1"

    def seed_after_tool_result(self, session: str) -> str:
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)
        self.tool_result(session)
        return "run-1"

    def test_each_interruption_boundary_resumes_once(self) -> None:
        cases = (
            ("question", self.seed_after_question, False, 0, None),
            ("start marker", self.seed_after_start, False, 0, None),
            ("tool call", self.seed_after_tool_call, True, 1, None),
            (
                "tool result",
                self.seed_after_tool_result,
                False,
                1,
                "页面内容",
            ),
        )
        for label, seed, has_unknown, tool_calls, tool_text in cases:
            with self.subTest(boundary=label):
                session = self.session()
                seed(session)
                found = recover_store(self.store, clock=FixedClock())

                self.assertEqual(len(found), 1)
                run_id = found[0].run_id
                unknown_before = self._unknown(session)
                runtime, model = self.kernel()

                result = runtime.execute(
                    RunResume(session, run_id), lambda _event: None
                )

                self.assertEqual(result.status, "completed")
                self.assertEqual(len(model.requests), 1)
                self.assertEqual(
                    self._unknown(session),
                    unknown_before + (1 if has_unknown else 0),
                )
                self.assertEqual(
                    self._tool_call_count(session), tool_calls
                )
                self.assertEqual(
                    len(
                        [
                            marker
                            for marker in self.markers(session)
                            if marker.get("resumes") == run_id
                        ]
                    ),
                    1,
                )
                self.assertEqual(self.markers(session)[-1]["state"], FINISHED)
                joined = "\n".join(
                    message.content for message in model.requests[0].messages
                )
                if has_unknown:
                    self.assertIn("outcome unknown", joined)
                if tool_text is not None:
                    self.assertIn(tool_text, joined)

                with self.assertRaises(ValueError):
                    runtime.execute(
                        RunResume(session, run_id), lambda _event: None
                    )
                self.assertEqual(len(model.requests), 1)

    def test_resume_streams_the_new_answer_once(self) -> None:
        session = self.session()
        self.seed_after_tool_call(session)
        recover_store(self.store, clock=FixedClock())
        runtime, model = self.kernel("继续回答")
        tokens: list[str] = []

        def sink(event) -> None:
            if isinstance(event, TokenEvent):
                tokens.append(event.text)

        result = runtime.execute(RunResume(session, "run-1"), sink)

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual("".join(tokens), "继续回答")
        self.assertNotIn("答案", "".join(tokens))

    def test_a_final_answer_is_settled_and_not_resumed(self) -> None:
        session = self.session()
        self.user(session)
        self.start(session)
        self.assistant(session)

        found = recover_store(self.store, clock=FixedClock())
        runtime, model = self.kernel("不该被调用")

        self.assertEqual(found, ())
        self.assertEqual(self.markers(session)[-1]["state"], FINISHED)
        with self.assertRaises(ValueError):
            runtime.execute(
                RunResume(session, "run-1"), lambda _event: None
            )
        self.assertEqual(model.requests, [])

    def test_a_written_terminal_marker_is_left_alone(self) -> None:
        session = self.session()
        self.user(session)
        self.start(session)
        self.assistant(session)
        self.finish(session)

        found = recover_store(self.store, clock=FixedClock())
        runtime, model = self.kernel("不该被调用")

        self.assertEqual(found, ())
        self.assertEqual(
            [marker["state"] for marker in self.markers(session)],
            [RUNNING, FINISHED],
        )
        with self.assertRaises(ValueError):
            runtime.execute(
                RunResume(session, "run-1"), lambda _event: None
            )
        self.assertEqual(model.requests, [])

    def test_the_old_marker_is_never_rewritten(self) -> None:
        session = self.session()
        self.user(session)
        self.start(session, "old-run")
        self.assistant(session, tool_calls=True)
        before = [
            dict(marker)
            for marker in self.markers(session)
            if marker["runId"] == "old-run"
        ]
        self.assertEqual(len(before), 1)

        recover_store(self.store, clock=FixedClock())
        after_scan = [
            dict(marker)
            for marker in self.markers(session)
            if marker["runId"] == "old-run"
        ]
        runtime, _model = self.kernel()

        self.assertEqual(after_scan[: len(before)], before)
        self.assertEqual(
            [marker["state"] for marker in after_scan],
            [RUNNING, INTERRUPTED],
        )

        runtime.execute(
            RunResume(session, "old-run"), lambda _event: None
        )

        after_resume = [
            dict(marker)
            for marker in self.markers(session)
            if marker["runId"] == "old-run"
        ]
        self.assertEqual(after_resume, after_scan)
        self.assertTrue(
            any(
                marker.get("resumes") == "old-run"
                for marker in self.markers(session)
            )
        )

    def _unknown(self, session: str) -> int:
        return sum(
            1
            for entry in self.entries(session)
            if entry.get("type") == "message"
            and entry.get("role") == "toolResult"
            and entry.get("meta", {}).get("interrupted")
        )

    def _tool_call_count(self, session: str) -> int:
        return sum(
            1
            for entry in self.entries(session)
            if entry.get("type") == "message"
            and entry.get("role") == "assistant"
            and entry.get("toolCalls")
        )


if __name__ == "__main__":
    unittest.main()
