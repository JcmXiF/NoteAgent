"""What a run writes down about itself, and what it deliberately leaves out.

The trace is for support and evals: which steps ran, which tools were touched,
where a run stopped. It is not a second copy of the conversation. Prompts, tool
arguments, tool output and generated text never enter it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.model import ModelErrorEvent
from noteagent.agent.runtime import UserInput
from noteagent.agent.tools import Diagnostic, ToolRegistry, ToolResult, ToolSpec
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.sessions.trace import SessionTrace


_SECRETS = ("SECRET-PROMPT", "SECRET-ARG", "SECRET-OUTPUT", "SECRET-ANSWER")


def _note(arguments, _context):
    return ToolResult(
        content=f"SECRET-OUTPUT {arguments['text']}",
        meta={
            "title": "SECRET-TITLE",
            "query": "SECRET-QUERY",
            "slug": "concepts/secret",
        },
        diagnostics=(
            Diagnostic("warning", "SECRET-OUTPUT", "SECRET-OUTPUT"),
        ),
    )


def _tools() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolSpec(
                name="note",
                description="Write a note.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=_note,
            )
        ]
    )


class TraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        self.store.create("s1")

    def runtime(self, model, *, trace=None) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=_tools(),
            trace=trace,
            clock=FixedClock(),
            ids=SequentialIds(),
        )

    def records(self) -> list[dict[str, object]]:
        path = self.layout.session_events_path("s1")
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_the_trace_names_the_steps_without_the_content(self) -> None:
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "note", '{"text": "SECRET-ARG"}'
            ).script,
            FauxModelClient.text("SECRET-ANSWER").script,
        )
        runtime = self.runtime(model, trace=SessionTrace(self.layout))

        runtime.execute(UserInput("s1", "SECRET-PROMPT"), lambda _event: None)

        path = self.layout.session_events_path("s1")
        self.assertTrue(path.is_file())
        raw = path.read_text(encoding="utf-8")
        for secret in _SECRETS:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, raw)

        records = self.records()
        kinds = [item["type"] for item in records]
        self.assertEqual(kinds[0], "run_started")
        self.assertIn("tool_result", kinds)
        self.assertIn("run_finished", kinds)
        tool = next(
            item for item in records if item["type"] == "tool_result"
        )
        self.assertEqual(tool["tool"], "note")
        self.assertEqual(records[-1]["status"], "completed")

    def test_a_failed_run_ends_with_run_failed_and_no_message(self) -> None:
        model = FauxModelClient(
            [ModelErrorEvent("model_error", "SECRET-OUTPUT")]
        )
        runtime = self.runtime(model, trace=SessionTrace(self.layout))

        result = runtime.execute(UserInput("s1", "question"), lambda _event: None)

        self.assertEqual(result.status, "failed")
        records = self.records()
        self.assertEqual(records[-1]["type"], "run_failed")
        self.assertEqual(records[-1]["code"], "model_error")
        self.assertNotIn("SECRET-OUTPUT", json.dumps(records))

    def test_a_tool_name_that_is_model_text_is_not_written_verbatim(self) -> None:
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "SECRET-PROMPT is not a tool", "{}"
            ).script,
            FauxModelClient.text("answer").script,
        )
        runtime = self.runtime(model, trace=SessionTrace(self.layout))

        runtime.execute(UserInput("s1", "question"), lambda _event: None)

        records = self.records()
        tool = next(
            item for item in records if item["type"] == "tool_result"
        )
        self.assertEqual(tool["tool"], "unknown")
        self.assertNotIn("SECRET-PROMPT", json.dumps(records))

    def test_a_secret_in_the_environment_is_kept_out_of_the_trace(self) -> None:
        key = "sk-live-1234567890"
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "note", json.dumps({"text": key})
            ).script,
            FauxModelClient.text(key).script,
        )
        runtime = self.runtime(model, trace=SessionTrace(self.layout))

        with patch.dict("os.environ", {"NOTEAGENT_LLM_API_KEY": key}):
            runtime.execute(UserInput("s1", f"my key is {key}"), lambda _e: None)

        self.assertNotIn(key, self.layout.session_events_path("s1").read_text("utf-8"))

    def test_a_trace_that_fails_does_not_fail_the_run(self) -> None:
        class BrokenTrace:
            def started(self, run):
                raise OSError("disk full")

            def record(self, run, event):
                raise OSError("disk full")

        model = FauxModelClient.text("answer")
        runtime = self.runtime(model, trace=BrokenTrace())
        events = []

        result = runtime.execute(UserInput("s1", "question"), events.append)

        self.assertEqual(result.status, "completed")
        self.assertTrue(events)

    def test_a_session_delete_removes_its_trace(self) -> None:
        self.layout.session_events_path("s1").write_text("{}\n", encoding="utf-8")

        self.store.delete("s1")

        self.assertFalse(self.layout.session_events_path("s1").exists())


if __name__ == "__main__":
    unittest.main()
