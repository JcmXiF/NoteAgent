"""The machine-checked half of a quality case: trace assertions."""

from __future__ import annotations

import unittest

from evals.quality.assertions import Evidence, evaluate
from evals.quality.cases import AssertionSpec


def evidence(
    *,
    tools: tuple[str, ...] = (),
    rounds: int = 0,
    errors: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(tools=tools, tool_rounds=rounds, errors=errors)


class ToolAssertionTests(unittest.TestCase):
    def test_tools_any_passes_when_one_named_tool_ran(self) -> None:
        spec = AssertionSpec(tools_any=("wiki_read", "search"))

        [result] = evaluate(spec, evidence(tools=("search", "wiki_read")))

        self.assertTrue(result.passed)
        self.assertEqual(result.name, "tools_any")
        self.assertEqual(result.actual, ["search", "wiki_read"])

    def test_tools_any_fails_when_no_named_tool_ran(self) -> None:
        spec = AssertionSpec(tools_any=("wiki_read", "search"))

        [result] = evaluate(spec, evidence(tools=("propose",)))

        self.assertFalse(result.passed)
        self.assertIn("wiki_read", result.detail)

    def test_tools_all_requires_every_name(self) -> None:
        spec = AssertionSpec(tools_all=("wiki_read", "search"))

        [result] = evaluate(spec, evidence(tools=("wiki_read",)))

        self.assertFalse(result.passed)
        self.assertIn("search", result.detail)

    def test_tools_none_fails_when_a_forbidden_tool_ran(self) -> None:
        spec = AssertionSpec(tools_none=("propose",))

        [result] = evaluate(spec, evidence(tools=("propose",)))

        self.assertFalse(result.passed)
        self.assertEqual(result.actual, ["propose"])


class RoundAssertionTests(unittest.TestCase):
    def test_max_tool_rounds_is_inclusive(self) -> None:
        spec = AssertionSpec(max_tool_rounds=4)

        [at_limit] = evaluate(spec, evidence(rounds=4))
        [over] = evaluate(spec, evidence(rounds=5))

        self.assertTrue(at_limit.passed)
        self.assertFalse(over.passed)
        self.assertIn("4", over.detail)

    def test_no_error_fails_on_a_recorded_error(self) -> None:
        [result] = evaluate(
            AssertionSpec(no_error=True),
            evidence(errors=("handler_error: wiki_read failed",)),
        )

        self.assertFalse(result.passed)
        self.assertIn("handler_error", result.detail)

    def test_no_error_passes_on_a_clean_run(self) -> None:
        [result] = evaluate(AssertionSpec(no_error=True), evidence())

        self.assertTrue(result.passed)


class ResultOrderTests(unittest.TestCase):
    def test_every_declared_assertion_gets_one_result_in_order(self) -> None:
        spec = AssertionSpec(
            tools_any=("wiki_read",),
            tools_all=("wiki_read",),
            tools_none=("propose",),
            max_tool_rounds=4,
            no_error=True,
        )

        results = evaluate(spec, evidence(tools=("wiki_read",), rounds=1))

        self.assertEqual(
            [result.name for result in results],
            [
                "tools_any",
                "tools_all",
                "tools_none",
                "max_tool_rounds",
                "no_error",
            ],
        )
        self.assertTrue(all(result.passed for result in results))
