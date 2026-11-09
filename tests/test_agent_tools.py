"""One tool call, end to end: checked, run, bounded, and reported.

The registry is the seam every action goes through, so the checks the model can
never skip live here: the schema its arguments must satisfy, the time the handler
may take, what happens to an exception, and how much of a result is handed on.
"""

from __future__ import annotations

import threading
import time
import unittest

from noteagent.agent.model import ToolCall
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import (
    Diagnostic,
    OutputLimits,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


def _echo(arguments, _context):
    return ToolResult(content=f"echo: {arguments['text']}", meta={"text": arguments["text"]})


def _spec(handler=_echo, **overrides) -> ToolSpec:
    values = {
        "name": "echo",
        "description": "Echo the text.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "handler": handler,
    }
    values.update(overrides)
    return ToolSpec(**values)


class DispatchTests(unittest.TestCase):
    def test_arguments_that_do_not_match_the_schema_never_reach_the_handler(self) -> None:
        seen: list[object] = []

        def handler(arguments, _context):
            seen.append(arguments)
            return ToolResult(content="ok")

        registry = ToolRegistry([_spec(handler)])

        result = registry.dispatch("echo", '{"text": 3}', ToolContext("s1"))

        self.assertEqual(seen, [])
        self.assertTrue(result.meta["error"])
        self.assertIn("text", result.content)

    def test_a_missing_required_argument_is_refused(self) -> None:
        registry = ToolRegistry([_spec()])

        result = registry.dispatch("echo", "{}", ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("required", result.content)

    def test_an_enum_is_checked(self) -> None:
        registry = ToolRegistry(
            [
                _spec(
                    parameters={
                        "type": "object",
                        "properties": {
                            "scope": {"type": "string", "enum": ["wiki", "web"]}
                        },
                        "required": ["scope"],
                    }
                )
            ]
        )

        result = registry.dispatch("echo", '{"scope": "notes"}', ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("wiki", result.content)

    def test_invalid_json_is_an_error_observation(self) -> None:
        registry = ToolRegistry([_spec()])

        result = registry.dispatch("echo", "{not json", ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("json", result.content.lower())

    def test_an_unknown_tool_says_so_without_running_anything(self) -> None:
        registry = ToolRegistry()

        result = registry.dispatch("nope", "{}", ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("unknown tool", result.content)

    def test_a_disabled_tool_is_not_callable(self) -> None:
        registry = ToolRegistry([_spec(enabled=False)])

        result = registry.dispatch("echo", '{"text": "hi"}', ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("unknown tool", result.content)

    def test_a_handler_that_raises_becomes_an_error_observation(self) -> None:
        def broken(_arguments, _context):
            raise ValueError("the page exploded")

        registry = ToolRegistry([_spec(broken)])

        result = registry.dispatch("echo", '{"text": "hi"}', ToolContext("s1"))

        self.assertTrue(result.meta["error"])
        self.assertIn("the page exploded", result.content)
        self.assertEqual(result.diagnostics[0].code, "handler_error")

    def test_a_handler_that_runs_too_long_is_stopped_and_reported(self) -> None:
        release = threading.Event()

        def slow(_arguments, _context):
            release.wait(2)
            return ToolResult(content="late")

        registry = ToolRegistry([_spec(slow, timeout_s=0.05)])
        started = time.monotonic()

        result = registry.dispatch("echo", '{"text": "hi"}', ToolContext("s1"))
        elapsed = time.monotonic() - started
        release.set()

        self.assertLess(elapsed, 1.0)
        self.assertTrue(result.meta["error"])
        self.assertTrue(result.meta["timeout"])
        self.assertIn("timed out", result.content)

    def test_a_cancelled_call_is_not_turned_into_an_error_observation(self) -> None:
        def stopped(_arguments, _context):
            raise RunCancelled("stopped")

        registry = ToolRegistry([_spec(stopped)])

        with self.assertRaises(RunCancelled):
            registry.dispatch("echo", '{"text": "hi"}', ToolContext("s1"))


class OutputLimitTests(unittest.TestCase):
    def test_a_long_result_is_clipped_and_says_so(self) -> None:
        def long_answer(_arguments, _context):
            return ToolResult(content="长" * 500)

        registry = ToolRegistry(
            [_spec(long_answer, output_limits=OutputLimits(max_chars_total=120))]
        )

        result = registry.dispatch("echo", '{"text": "hi"}', ToolContext("s1"))

        self.assertLessEqual(len(result.content), 120)
        self.assertIn("truncated", result.content)
        self.assertTrue(result.meta["truncated"])
        self.assertIn(
            Diagnostic("warning", "truncated", ""),
            tuple(Diagnostic(item.severity, item.code) for item in result.diagnostics),
        )


class ObservationTests(unittest.TestCase):
    def test_an_observation_carries_only_the_fields_an_interface_knows(self) -> None:
        result = ToolResult(
            content="the body stays with the model",
            meta={"path": "wiki/entropy.md", "slug": "entropy", "secret": "not for UI"},
        )

        card = result.observation("wiki_read")

        self.assertEqual(
            card,
            {"tool": "wiki_read", "path": "wiki/entropy.md", "slug": "entropy"},
        )


class ParallelDispatchTests(unittest.TestCase):
    def test_calls_in_one_batch_run_at_the_same_time_and_keep_their_order(self) -> None:
        together = threading.Barrier(2, timeout=2)

        def meet(arguments, _context):
            together.wait()
            return ToolResult(content=f"done {arguments['name']}")

        def spec(name):
            return _spec(
                meet,
                name=name,
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            )

        registry = ToolRegistry([spec("first"), spec("second")])

        results = registry.dispatch_many(
            [
                ToolCall("1", "first", '{"name": "first"}'),
                ToolCall("2", "second", '{"name": "second"}'),
            ],
            ToolContext("s1"),
        )

        self.assertEqual([result.content for result in results], ["done first", "done second"])

    def test_slow_and_fast_calls_are_reported_in_the_order_they_were_asked(self) -> None:
        def slow(_arguments, _context):
            time.sleep(0.08)
            return ToolResult(content="slow")

        def fast(_arguments, _context):
            return ToolResult(content="fast")

        registry = ToolRegistry(
            [_spec(slow, name="slow"), _spec(fast, name="fast")]
        )

        results = registry.dispatch_many(
            [
                ToolCall("1", "slow", '{"text": "x"}'),
                ToolCall("2", "fast", '{"text": "x"}'),
            ],
            ToolContext("s1"),
        )

        self.assertEqual([result.content for result in results], ["slow", "fast"])

    def test_one_bad_call_does_not_take_down_the_others(self) -> None:
        def broken(_arguments, _context):
            raise ValueError("boom")

        registry = ToolRegistry([_spec(broken, name="broken"), _spec(name="echo")])

        results = registry.dispatch_many(
            [
                ToolCall("1", "broken", '{"text": "x"}'),
                ToolCall("2", "echo", '{"text": "hi"}'),
            ],
            ToolContext("s1"),
        )

        self.assertTrue(results[0].meta["error"])
        self.assertEqual(results[1].content, "echo: hi")


if __name__ == "__main__":
    unittest.main()
