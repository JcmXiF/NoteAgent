"""What a finished quality run leaves behind, before assertions read it."""

from __future__ import annotations

import unittest

from evals.quality.assertions import evidence_from


def assistant(*tool_names: str) -> dict[str, object]:
    return {
        "type": "message",
        "role": "assistant",
        "content": "",
        "toolCalls": [
            {"id": f"c{index}", "name": name, "arguments": "{}"}
            for index, name in enumerate(tool_names)
        ],
    }


class EvidenceTests(unittest.TestCase):
    def test_tool_rounds_count_assistant_turns_with_tool_calls(self) -> None:
        entries = [
            {"type": "message", "role": "user", "content": "问"},
            assistant("wiki_read", "search"),
            {"type": "message", "role": "toolResult", "content": "页"},
            assistant("wiki_read"),
            {"type": "message", "role": "assistant", "content": "答"},
        ]

        evidence = evidence_from(entries=entries)

        self.assertEqual(evidence.tool_rounds, 2)

    def test_tools_come_from_the_trace(self) -> None:
        trace = [
            {"type": "run_started"},
            {"type": "tool_result", "tool": "wiki_read"},
            {"type": "tool_result", "tool": "search"},
            {"type": "run_finished", "status": "completed"},
        ]

        evidence = evidence_from(trace=trace)

        self.assertEqual(evidence.tools, ("wiki_read", "search"))

    def test_errors_include_sse_failures_and_bad_tool_records(self) -> None:
        events = [
            ("status", {"step": "think"}),
            ("error", {"code": "internal", "message": "the run failed"}),
        ]
        trace = [
            {"type": "tool_result", "tool": "wiki_read", "codes": ["handler_error"]},
            {"type": "run_failed", "code": "internal"},
        ]

        evidence = evidence_from(trace=trace, events=events)

        self.assertEqual(len(evidence.errors), 3)
        self.assertTrue(any("internal" in item for item in evidence.errors))
        self.assertTrue(any("handler_error" in item for item in evidence.errors))

    def test_a_cancel_is_not_a_run_error(self) -> None:
        evidence = evidence_from(
            trace=[{"type": "run_cancelled", "code": "cancelled"}],
            events=[
                (
                    "error",
                    {"code": "cancelled", "message": "the run was cancelled"},
                )
            ],
        )

        self.assertEqual(evidence.errors, ())

    def test_a_plain_tool_error_is_not_a_handler_error(self) -> None:
        evidence = evidence_from(
            trace=[{"type": "tool_result", "tool": "wiki_read", "error": True}]
        )

        self.assertEqual(evidence.errors, ())

    def test_a_clean_run_has_no_errors(self) -> None:
        evidence = evidence_from(
            trace=[{"type": "run_finished", "status": "completed"}],
            events=[("done", {"run": {"status": "completed"}})],
        )

        self.assertEqual(evidence.errors, ())
