"""The golden trace: tools, read pages, wait, propose and errors."""

from __future__ import annotations

import unittest

from evals.golden.dataset import ExpectedTrace, GoldenQuery
from evals.golden.trace import Evidence, evaluate, evidence_from


def case(
    *,
    pages: tuple[str, ...] = ("concepts/x",),
    expected: ExpectedTrace | None = None,
) -> GoldenQuery:
    return GoldenQuery(
        id="case-1",
        type="fact",
        difficulty="easy",
        question="X 是什么？",
        corpus_pages=pages,
        reference_answer="参考答案。",
        key_points=("要点一", "要点二"),
        expected_trace=expected
        or ExpectedTrace(
            tools_any=("search", "wiki_read"),
            wait="absent",
            max_tool_rounds=4,
            no_error=True,
        ),
        forbidden=("编造",),
        raw_sources=(),
        notes="",
    )


def propose_case() -> GoldenQuery:
    return case(
        expected=ExpectedTrace(
            tools_any=("search", "wiki_read"),
            wait="proposed",
            max_tool_rounds=4,
            no_error=True,
            propose_valid=True,
            propose_job="plan",
            propose_interface="source",
        )
    )


def assistant(*calls: tuple[str, str]) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "toolCalls": [
            {"id": f"c{index}", "name": name, "arguments": arguments}
            for index, (name, arguments) in enumerate(calls)
        ],
    }


class EvidenceTests(unittest.TestCase):
    def test_it_reads_tools_pages_propose_and_wait(self) -> None:
        entries = [
            assistant(
                ("wiki_read", '{"slug": "concepts/x"}'),
                (
                    "propose",
                    '{"job": "plan", "interface": "source"}',
                ),
            )
        ]
        trace = [
            {"type": "tool_result", "tool": "wiki_read"},
            {"type": "tool_result", "tool": "propose"},
        ]
        session = {
            "wait": {
                "status": "proposed",
                "job": "plan",
                "interface": "source",
            }
        }

        evidence = evidence_from(
            entries=entries, trace=trace, session=session
        )

        self.assertEqual(evidence.tools, ("wiki_read", "propose"))
        self.assertEqual(evidence.read_pages, ("concepts/x",))
        self.assertEqual(
            evidence.propose_calls, (("plan", "source"),)
        )
        self.assertEqual(evidence.wait_status, "proposed")
        self.assertEqual(evidence.wait_job, "plan")
        self.assertEqual(evidence.wait_interface, "source")
        self.assertEqual(evidence.tool_rounds, 1)

    def test_a_control_action_failure_is_an_error(self) -> None:
        evidence = evidence_from(
            trace=[
                {
                    "type": "tool_result",
                    "tool": "propose",
                    "codes": ["propose_not_alone"],
                }
            ]
        )

        self.assertTrue(
            any(
                "propose_not_alone" in item
                for item in evidence.errors
            )
        )

    def test_a_cancel_is_not_an_error(self) -> None:
        evidence = evidence_from(
            events=[("error", {"code": "cancelled"})]
        )

        self.assertEqual(evidence.errors, ())


class EvaluateTests(unittest.TestCase):
    def test_a_propose_case_passes_its_checks(self) -> None:
        evidence = Evidence(
            tools=("wiki_read",),
            read_pages=("concepts/x",),
            tool_rounds=1,
            wait_status="proposed",
            wait_job="plan",
            wait_interface="source",
        )

        checks = evaluate(propose_case(), evidence)

        self.assertEqual(
            [check.name for check in checks],
            [
                "tools_any",
                "wait",
                "propose_job",
                "propose_interface",
                "propose_valid",
                "no_error",
            ],
        )
        self.assertTrue(all(check.passed for check in checks))

    def test_tool_rounds_are_evidence_not_a_check(self) -> None:
        evidence = Evidence(
            tools=("search",),
            read_pages=(),
            tool_rounds=99,
        )

        checks = evaluate(case(), evidence)

        self.assertNotIn(
            "max_tool_rounds", [check.name for check in checks]
        )

    def test_an_invalid_pair_fails_the_propose_checks(self) -> None:
        evidence = Evidence(
            tools=("wiki_read",),
            read_pages=("concepts/x",),
            tool_rounds=1,
            wait_status="proposed",
            wait_job="plan",
            wait_interface="compile_board",
        )

        checks = evaluate(propose_case(), evidence)
        failed = {check.name for check in checks if not check.passed}

        self.assertIn("propose_interface", failed)
        self.assertIn("propose_valid", failed)

    def test_a_wrong_job_fails_the_job_check(self) -> None:
        evidence = Evidence(
            tools=("wiki_read",),
            read_pages=("concepts/x",),
            tool_rounds=1,
            wait_status="proposed",
            wait_job="compile",
            wait_interface="source",
        )

        checks = evaluate(propose_case(), evidence)
        failed = {check.name for check in checks if not check.passed}

        self.assertIn("propose_job", failed)
        self.assertIn("propose_valid", failed)

    def test_a_missing_wait_fails_the_propose_checks(self) -> None:
        evidence = Evidence(
            tools=("wiki_read",),
            read_pages=("concepts/x",),
            tool_rounds=1,
        )

        checks = evaluate(propose_case(), evidence)
        failed = {check.name for check in checks if not check.passed}

        self.assertIn("wait", failed)
        self.assertIn("propose_valid", failed)

    def test_an_error_fails_no_error(self) -> None:
        evidence = Evidence(
            tools=("wiki_read",),
            read_pages=("concepts/x",),
            tool_rounds=1,
            errors=("internal: boom",),
        )

        checks = evaluate(case(), evidence)
        failed = {check.name for check in checks if not check.passed}

        self.assertIn("no_error", failed)

    def test_read_pages_are_evidence_not_a_check(self) -> None:
        evidence = Evidence(
            tools=("search",),
            read_pages=(),
            tool_rounds=1,
        )

        checks = evaluate(case(), evidence)

        self.assertNotIn("read_any", [check.name for check in checks])
        self.assertTrue(all(check.passed for check in checks))
