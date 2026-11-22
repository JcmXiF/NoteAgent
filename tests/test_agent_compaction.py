"""Keeping a long session inside the model's window."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds, seed_long_history
from noteagent.agent.compaction import (
    CompactionLimits,
    choose_cut,
    estimate_tokens,
    trim_tool_results,
)
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelDoneEvent,
    ModelStartEvent,
    ModelStreamError,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
    Usage,
)
from noteagent.agent.projection import project
from noteagent.agent.runtime import UserInput
from noteagent.agent.tools import ToolRegistry, ToolResult, ToolSpec
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import kernel_runtime
from noteagent.llm.config import load_llm_config
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


def _entry(ident: str, role: str, content: str = "", **fields) -> dict[str, object]:
    return {
        "v": 1,
        "id": ident,
        "parentId": None,
        "ts": "2026-11-10T09:00:00Z",
        "type": "message",
        "role": role,
        "content": content,
        **fields,
    }


def _assistant(ident: str, content: str, calls: list[dict[str, object]] | None = None):
    return _entry(
        ident,
        "assistant",
        content,
        **({"toolCalls": calls} if calls is not None else {}),
    )


def _call(call_id: str, name: str = "wiki_read") -> dict[str, object]:
    return {"id": call_id, "name": name, "arguments": "{}"}


def _echo_tools() -> ToolRegistry:
    def echo(_arguments, _context):
        return ToolResult(content="ok", meta={"title": "ok"})

    return ToolRegistry(
        [
            ToolSpec(
                name="echo",
                description="Echo.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                handler=echo,
            )
        ]
    )


def _tool_with_usage(call_id: str, input_tokens: int) -> list:
    call = ToolCall(call_id, "echo", '{"text": "x"}')
    return [
        ModelStartEvent("faux"),
        ModelToolCallEnd(call),
        ModelDoneEvent(
            AssistantMessage(
                stop_reason="tool_calls",
                usage=Usage(input_tokens=input_tokens, output_tokens=5),
            )
        ),
    ]


class ContextWindowTests(unittest.TestCase):
    def _config(self):
        with patch.dict(
            os.environ,
            {
                "NOTEAGENT_LLM_BASE_URL": "http://model.test/v1",
                "NOTEAGENT_LLM_API_KEY": "key",
                "NOTEAGENT_LLM_MODEL": "model",
            },
            clear=False,
        ):
            return load_llm_config()

    def test_the_environment_sets_the_window(self) -> None:
        with patch.dict(
            os.environ,
            {"NOTEAGENT_LLM_CONTEXT_WINDOW": "64000"},
            clear=False,
        ):
            self.assertEqual(self._config().context_window, 64_000)

    def test_an_invalid_value_falls_back(self) -> None:
        for raw in ("", "nope", "0", "-5"):
            with self.subTest(raw=raw):
                with patch.dict(
                    os.environ,
                    {"NOTEAGENT_LLM_CONTEXT_WINDOW": raw},
                    clear=False,
                ):
                    self.assertEqual(self._config().context_window, 32_000)


class TrimTests(unittest.TestCase):
    def test_old_tool_results_are_truncated_and_recent_ones_are_kept(self) -> None:
        long = "x" * 4_000
        messages = (
            Message("user", "look at these"),
            Message(
                "assistant",
                "",
                tool_calls=(ToolCall("t1", "wiki_read", "{}"),),
            ),
            Message("tool", long, tool_call_id="t1", name="wiki_read"),
            Message("assistant", "one done"),
            Message(
                "assistant",
                "",
                tool_calls=(ToolCall("t2", "wiki_read", "{}"),),
            ),
            Message("tool", long, tool_call_id="t2", name="wiki_read"),
            Message("user", "continue"),
        )
        limits = CompactionLimits(
            context_tokens=100_000,
            reserve_tokens=0,
            keep_recent_tokens=50,
            chars_per_token=4,
            tool_result_chars=200,
        )

        trimmed = trim_tool_results(messages, limits)

        self.assertLess(len(trimmed[2].content), len(long))
        self.assertIn("truncated", trimmed[2].content)
        self.assertEqual(trimmed[5].content, long)
        self.assertEqual(trimmed[0].content, "look at these")
        self.assertEqual(trimmed[6].content, "continue")
        self.assertEqual(trimmed[2].tool_call_id, "t1")

    def test_nothing_changes_without_an_old_tool_result(self) -> None:
        messages = (Message("user", "hello"), Message("assistant", "hi"))
        limits = CompactionLimits(chars_per_token=4, tool_result_chars=10)

        self.assertEqual(trim_tool_results(messages, limits), messages)


class EstimateTests(unittest.TestCase):
    def test_content_and_tool_arguments_count(self) -> None:
        messages = (
            Message("user", "a" * 400),
            Message(
                "assistant",
                "",
                tool_calls=(ToolCall("t1", "search", '{"query": "' + "b" * 400 + '"}'),),
            ),
        )

        self.assertGreaterEqual(
            estimate_tokens(messages, chars_per_token=4), 200
        )


class LimitTests(unittest.TestCase):
    def test_a_small_window_clamps_the_recent_and_summary_budgets(
        self,
    ) -> None:
        limits = CompactionLimits(context_tokens=10_000)

        self.assertLess(limits.keep_recent_tokens, limits.threshold_tokens)
        self.assertLess(limits.max_summary_tokens, limits.threshold_tokens)


class CutTests(unittest.TestCase):
    def test_the_cut_keeps_the_recent_messages(self) -> None:
        entries = [
            _entry("e1", "user", "old " * 400),
            _entry("e2", "assistant", "old answer " * 200),
            _entry("e3", "user", "recent question"),
            _entry("e4", "assistant", "recent answer"),
        ]
        limits = CompactionLimits(
            keep_recent_tokens=20, chars_per_token=4
        )

        cut = choose_cut(entries, limits)

        self.assertEqual(cut, "e3")

    def test_the_cut_does_not_split_a_tool_pair(self) -> None:
        entries = [
            _entry("e1", "user", "old"),
            _assistant("e2", "preamble " * 100, [_call("t1")]),
            _entry(
                "e3",
                "toolResult",
                "the page",
                toolCallId="t1",
                name="wiki_read",
            ),
            _entry("e4", "user", "recent"),
            _entry("e5", "assistant", "answer"),
        ]
        limits = CompactionLimits(keep_recent_tokens=10, chars_per_token=4)

        cut = choose_cut(entries, limits)

        self.assertEqual(cut, "e2")

    def test_the_latest_job_result_is_kept(self) -> None:
        entries = [
            _entry("e1", "user", "old " * 500),
            _entry("e2", "assistant", "old answer " * 200),
            {
                "v": 1,
                "id": "w1",
                "parentId": "e2",
                "ts": "2026-11-10T09:00:00Z",
                "type": "wait_completed",
                "waitId": "w1",
                "jobId": "job-1",
                "job": "plan",
                "status": "completed",
                "summary": "planned 2 pages",
            },
            _entry("e4", "user", "recent"),
            _entry("e5", "assistant", "answer"),
        ]
        limits = CompactionLimits(keep_recent_tokens=10, chars_per_token=4)

        cut = choose_cut(entries, limits)

        self.assertEqual(cut, "w1")

    def test_the_turns_since_the_wait_started_are_kept(self) -> None:
        entries = [
            _entry("e1", "user", "old " * 500),
            _entry("e2", "assistant", "old answer " * 200),
            {
                "type": "wait_proposed",
                "id": "w1",
                "waitId": "w1",
                "job": "plan",
                "interface": "source",
                "completion": "start_plan",
                "proposedAt": "2026-11-10T09:00:00Z",
            },
            {"type": "wait_entered", "id": "w2", "waitId": "w1", "jobId": "j1"},
            {
                "type": "wait_completed",
                "id": "w3",
                "waitId": "w1",
                "jobId": "j1",
                "job": "plan",
                "status": "completed",
                "summary": "planned",
            },
            _entry("e4", "user", "recent"),
            _entry("e5", "assistant", "answer"),
        ]
        limits = CompactionLimits(keep_recent_tokens=10, chars_per_token=4)

        cut = choose_cut(entries, limits)

        self.assertEqual(cut, "e2")

    def test_a_session_with_nothing_to_summarize_has_no_cut(self) -> None:
        entries = [_entry("e1", "user", "only")]
        limits = CompactionLimits(keep_recent_tokens=10, chars_per_token=4)

        self.assertIsNone(choose_cut(entries, limits))


class _OverflowThenAnswer:
    """Overflows once, then answers the summary and the retried turn."""

    def __init__(self) -> None:
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise ModelStreamError("context_overflow", "the prompt is too long")
        text = (
            "summary of old turns"
            if not request.tools
            else "answer after overflow"
        )
        yield from FauxModelClient.text(text).stream(request)


class _OverflowTwice:
    """Overflows, compacts, calls a tool, then overflows again."""

    def __init__(self) -> None:
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        number = len(self.requests)
        if number in (1, 4):
            raise ModelStreamError("context_overflow", "the prompt is too long")
        if number in (2, 5):
            yield from FauxModelClient.text("summary of old turns").stream(request)
            return
        if number == 3:
            yield from FauxModelClient.tool_call("t1", "echo", "{}").stream(request)
            return
        yield from FauxModelClient.text("final answer").stream(request)


class _SummaryFails:
    """The summary call itself fails."""

    def __init__(self) -> None:
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        raise ModelStreamError("model_error", "the summarizer failed")
        yield  # pragma: no cover - makes this a generator


class KernelCompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def seed_long_history(self) -> None:
        seed_long_history(self.store)

    def runtime(
        self,
        model,
        limits: CompactionLimits | None = None,
        tools: ToolRegistry | None = None,
    ):
        return KernelRuntime(
            model=model,
            sessions=self.store,
            tools=tools or ToolRegistry(),
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
            compaction=limits,
        )

    def test_a_long_prompt_is_summarized_before_the_answer(self) -> None:
        self.seed_long_history()
        model = FauxModelClient(
            FauxModelClient.text("the earlier turns were about entropy").script,
            FauxModelClient.text("answer after compaction").script,
        )
        limits = CompactionLimits(
            context_tokens=100,
            reserve_tokens=50,
            keep_recent_tokens=20,
            max_summary_tokens=20,
            chars_per_token=4,
        )

        result = self.runtime(model, limits).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        entries = self.store.load("s1").entries
        compaction = next(
            entry for entry in entries if entry["type"] == "compaction"
        )
        self.assertEqual(
            compaction["summary"], "the earlier turns were about entropy"
        )
        self.assertTrue(compaction["firstKeptEntryId"])
        self.assertGreater(compaction["tokensBefore"], 50)

        summary_request = model.requests[0]
        self.assertEqual(summary_request.tools, ())
        self.assertEqual(summary_request.max_tokens, 20)
        main = model.requests[1]
        joined = "\n".join(message.content for message in main.messages)
        self.assertIn("Summary of earlier conversation", joined)
        self.assertIn("the earlier turns were about entropy", joined)
        self.assertIn("You are NoteAgent.", joined)
        self.assertIn("new question", joined)
        self.assertNotIn("old question 0", joined)

    def seed_short_history(self) -> None:
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "user",
                "content": "old question " * 20,
            },
        )
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "assistant",
                "content": "old answer " * 20,
            },
        )

    def usage_limits(self) -> CompactionLimits:
        return CompactionLimits(
            context_tokens=10_000,
            reserve_tokens=1_000,
            keep_recent_tokens=20,
            max_summary_tokens=20,
            summary_input_tokens=500,
            chars_per_token=4,
        )

    def test_the_provider_prompt_count_triggers_compaction(self) -> None:
        self.seed_short_history()
        model = FauxModelClient(
            _tool_with_usage("t1", 9_500),
            FauxModelClient.text("summary of earlier turns").script,
            FauxModelClient.text(
                "answer after usage compaction"
            ).script,
        )

        result = self.runtime(
            model, self.usage_limits(), tools=_echo_tools()
        ).execute(UserInput("s1", "new question"), lambda _event: None)

        self.assertEqual(result.status, "completed")
        entries = self.store.load("s1").entries
        compaction = next(
            entry for entry in entries if entry["type"] == "compaction"
        )
        self.assertGreaterEqual(compaction["tokensBefore"], 9_500)
        self.assertEqual(
            compaction["summary"], "summary of earlier turns"
        )

    def test_a_new_run_starts_from_the_last_calls_usage(self) -> None:
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "user",
                "content": "old question " * 20,
            },
        )
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "assistant",
                "content": "old answer " * 20,
                "usage": {
                    "inputTokens": 9_500,
                    "outputTokens": 10,
                    "cacheReadTokens": 0,
                },
            },
        )
        model = FauxModelClient(
            FauxModelClient.text("summary of earlier turns").script,
            FauxModelClient.text("answer after baseline").script,
        )

        result = self.runtime(model, self.usage_limits()).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        entries = self.store.load("s1").entries
        compaction = next(
            entry for entry in entries if entry["type"] == "compaction"
        )
        self.assertGreaterEqual(compaction["tokensBefore"], 9_500)

    def test_a_small_provider_count_does_not_compact(self) -> None:
        self.seed_short_history()
        model = FauxModelClient(
            _tool_with_usage("t1", 100),
            FauxModelClient.text("answer without compaction").script,
        )

        result = self.runtime(
            model, self.usage_limits(), tools=_echo_tools()
        ).execute(UserInput("s1", "new question"), lambda _event: None)

        self.assertEqual(result.status, "completed")
        self.assertFalse(
            any(
                entry["type"] == "compaction"
                for entry in self.store.load("s1").entries
            )
        )
        self.assertEqual(len(model.requests), 2)

    def test_old_tool_output_is_trimmed_before_the_prompt(self) -> None:
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "look"}
        )
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "assistant",
                "content": "",
                "toolCalls": [
                    {"id": "t1", "name": "wiki_read", "arguments": "{}"}
                ],
            },
        )
        self.store.append(
            "s1",
            {
                "type": "message",
                "role": "toolResult",
                "toolCallId": "t1",
                "name": "wiki_read",
                "content": "z" * 4_000,
            },
        )
        self.store.append(
            "s1", {"type": "message", "role": "assistant", "content": "done"}
        )
        model = FauxModelClient.text("answer")
        limits = CompactionLimits(
            context_tokens=100_000,
            reserve_tokens=0,
            keep_recent_tokens=0,
            chars_per_token=4,
            tool_result_chars=200,
        )

        result = self.runtime(model, limits).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        tool_messages = [
            message
            for message in model.requests[0].messages
            if message.role == "tool"
        ]
        self.assertEqual(len(tool_messages), 1)
        self.assertLess(len(tool_messages[0].content), 4_000)
        self.assertIn("truncated", tool_messages[0].content)

    def test_manual_compaction_writes_the_entry_with_its_usage(self) -> None:
        self.seed_long_history()
        model = FauxModelClient(
            [
                ModelStartEvent("faux"),
                ModelDoneEvent(
                    AssistantMessage(
                        blocks=(TextBlock("a compact summary"),),
                        stop_reason="stop",
                        usage=Usage(input_tokens=12, output_tokens=3),
                    )
                ),
            ]
        )
        runtime = self.runtime(
            model,
            CompactionLimits(
                context_tokens=100_000,
                reserve_tokens=0,
                keep_recent_tokens=20,
                max_summary_tokens=20,
                chars_per_token=4,
            ),
        )

        self.assertTrue(runtime.compact("s1"))

        entries = self.store.load("s1").entries
        compaction = next(
            entry for entry in entries if entry["type"] == "compaction"
        )
        self.assertEqual(compaction["summary"], "a compact summary")
        self.assertEqual(
            compaction["usage"],
            {
                "inputTokens": 12,
                "outputTokens": 3,
                "cacheReadTokens": 0,
            },
        )
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0].tools, ())

    def test_compacting_twice_does_not_empty_the_prompt(self) -> None:
        self.seed_long_history()
        model = FauxModelClient(
            FauxModelClient.text("first summary").script,
            FauxModelClient.text("second summary").script,
        )
        runtime = self.runtime(
            model,
            CompactionLimits(
                context_tokens=100_000,
                reserve_tokens=0,
                keep_recent_tokens=0,
                max_summary_tokens=20,
                chars_per_token=4,
            ),
        )

        self.assertTrue(runtime.compact("s1"))
        self.assertTrue(runtime.compact("s1"))

        projection = project(self.store.load("s1").entries)
        self.assertTrue(projection.messages)
        self.assertEqual(projection.summary, "second summary")

    def test_a_failed_summary_ends_the_run_with_its_code(self) -> None:
        self.seed_long_history()
        model = _SummaryFails()
        limits = CompactionLimits(
            context_tokens=100,
            reserve_tokens=50,
            keep_recent_tokens=20,
            max_summary_tokens=20,
            chars_per_token=4,
        )
        events = []

        result = self.runtime(model, limits).execute(
            UserInput("s1", "new question"), events.append
        )

        self.assertEqual(result.status, "failed")
        from noteagent.agent.events import ErrorEvent

        self.assertTrue(
            any(
                isinstance(event, ErrorEvent)
                and event.code == "compaction_failed"
                for event in events
            )
        )
        self.assertEqual(len(model.requests), 1)
        self.assertFalse(
            any(
                entry["type"] == "compaction"
                for entry in self.store.load("s1").entries
            )
        )

    def test_the_summary_request_is_bounded(self) -> None:
        self.store.append(
            "s1",
            {
                "type": "system_section",
                "sections": {"identity": "You are NoteAgent."},
            },
        )
        for index in range(30):
            self.store.append(
                "s1",
                {
                    "type": "message",
                    "role": "user",
                    "content": f"question {index} " + "x" * 4_000,
                },
            )
            self.store.append(
                "s1",
                {
                    "type": "message",
                    "role": "assistant",
                    "content": f"answer {index} " + "y" * 4_000,
                },
            )
        model = FauxModelClient(
            FauxModelClient.text("summary").script,
            FauxModelClient.text("answer").script,
        )
        limits = CompactionLimits(
            context_tokens=2_000,
            reserve_tokens=1_000,
            keep_recent_tokens=100,
            max_summary_tokens=20,
            chars_per_token=4,
        )

        result = self.runtime(model, limits).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        summary_input = model.requests[0].messages[1].content
        self.assertLessEqual(len(summary_input), 4_000 + 100)

    def test_manual_compaction_with_nothing_to_cut_does_nothing(self) -> None:
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "only"}
        )
        model = FauxModelClient.text("unused")

        self.assertFalse(self.runtime(model).compact("s1"))

        self.assertEqual(model.requests, [])
        self.assertFalse(
            any(
                entry["type"] == "compaction"
                for entry in self.store.load("s1").entries
            )
        )

    def test_a_later_overflow_gets_its_own_compaction(self) -> None:
        self.seed_long_history()

        def echo(_arguments, _context):
            return ToolResult(content="ok", meta={"title": "ok"})

        tools = ToolRegistry(
            [
                ToolSpec(
                    name="echo",
                    description="Echo.",
                    parameters={"type": "object"},
                    handler=echo,
                )
            ]
        )
        model = _OverflowTwice()
        limits = CompactionLimits(
            context_tokens=100_000,
            reserve_tokens=0,
            keep_recent_tokens=20,
            max_summary_tokens=20,
            chars_per_token=4,
        )

        result = self.runtime(model, limits, tools).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 6)
        entries = self.store.load("s1").entries
        self.assertEqual(
            sum(1 for entry in entries if entry["type"] == "compaction"),
            2,
        )
        messages = [entry for entry in entries if entry["type"] == "message"]
        self.assertEqual(messages[-1]["content"], "final answer")
        self.assertTrue(
            any(
                entry.get("role") == "toolResult"
                and entry.get("content") == "ok"
                for entry in entries
            )
        )

    def test_an_overflowing_prompt_is_compacted_and_retried_once(self) -> None:
        self.seed_long_history()
        model = _OverflowThenAnswer()
        limits = CompactionLimits(
            context_tokens=100_000,
            reserve_tokens=0,
            keep_recent_tokens=20,
            max_summary_tokens=20,
            chars_per_token=4,
        )

        result = self.runtime(model, limits).execute(
            UserInput("s1", "new question"), lambda _event: None
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(model.requests), 3)
        self.assertEqual(model.requests[1].tools, ())
        joined = "\n".join(
            message.content for message in model.requests[2].messages
        )
        self.assertIn("summary of old turns", joined)
        self.assertTrue(
            any(
                entry["type"] == "compaction"
                for entry in self.store.load("s1").entries
            )
        )


class KernelWiringTests(unittest.TestCase):
    def test_the_configured_window_reaches_the_kernel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(Path(tmp))
            store = JsonlSessionStore(
                layout, clock=FixedClock(), ids=SequentialIds()
            )
            model = FauxModelClient(FauxModelClient.text("x").script)
            with patch.dict(
                os.environ,
                {
                    "NOTEAGENT_LLM_BASE_URL": "http://model.test/v1",
                    "NOTEAGENT_LLM_API_KEY": "key",
                    "NOTEAGENT_LLM_MODEL": "model",
                    "NOTEAGENT_LLM_CONTEXT_WINDOW": "64000",
                },
                clear=False,
            ):
                runtime = kernel_runtime(
                    AppDeps(model=model, sessions=store),
                    store,
                    None,
                    ToolRegistry(),
                )

        self.assertEqual(
            runtime.compaction_limits.context_tokens, 64_000
        )


if __name__ == "__main__":
    unittest.main()
