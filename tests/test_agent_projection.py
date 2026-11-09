"""Entries become a prompt and a transcript, the same way every time."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.agent.model import ToolCall
from noteagent.agent.projection import ProjectionDiagnostic, project
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore


def _entry(ident: str, kind: str, **fields) -> dict[str, object]:
    return {
        "v": 1,
        "id": ident,
        "parentId": None,
        "ts": "2026-11-10T09:00:00Z",
        "type": kind,
        **fields,
    }


def _assistant(ident: str, content: str, calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    return _entry(
        ident,
        "message",
        role="assistant",
        content=content,
        **({"toolCalls": calls} if calls is not None else {}),
    )


def _tool_result(
    ident: str,
    call_id: str,
    content: str,
    name: str = "wiki_read",
    meta: dict[str, object] | None = None,
) -> dict[str, object]:
    return _entry(
        ident,
        "message",
        role="toolResult",
        toolCallId=call_id,
        name=name,
        content=content,
        **({"meta": meta} if meta is not None else {}),
    )


def _call(call_id: str, name: str = "wiki_read", arguments: object = '{"targets":["a"]}') -> dict[str, object]:
    return {"id": call_id, "name": name, "arguments": arguments}


class ProjectionGoldenTests(unittest.TestCase):
    def test_messages_sections_and_tool_pairs(self) -> None:
        entries = [
            _entry("e1", "message", role="user", content="什么是熵？"),
            _entry(
                "e2",
                "system_section",
                sections={"identity": "你是 NoteAgent。", "tools": "wiki_read"},
            ),
            _assistant("e3", "我先查一下。", [_call("t1", arguments={"targets": ["concepts/entropy"]})]),
            _tool_result("e4", "t1", "熵是……"),
            _entry(
                "e5",
                "message",
                role="assistant",
                content="熵衡量不确定性。",
                stopReason="stop",
                meta={"cards": [{"title": "熵"}]},
            ),
            _entry("e6", "system_section", sections={"tools": None, "index": "1. 熵"}),
            _entry("e7", "future_thing", payload={}),
        ]

        projection = project(entries)

        self.assertEqual(
            projection.sections,
            {"identity": "你是 NoteAgent。", "index": "1. 熵"},
        )
        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [
                ("user", "什么是熵？"),
                ("assistant", "我先查一下。"),
                ("tool", "熵是……"),
                ("assistant", "熵衡量不确定性。"),
            ],
        )
        self.assertEqual(
            projection.messages[1].tool_calls,
            (ToolCall("t1", "wiki_read", '{"targets":["concepts/entropy"]}'),),
        )
        self.assertEqual(projection.messages[2].tool_call_id, "t1")
        self.assertEqual(projection.messages[2].name, "wiki_read")
        self.assertEqual(
            [(item.role, item.content, item.is_step) for item in projection.transcript],
            [
                ("user", "什么是熵？", False),
                ("assistant", "我先查一下。", True),
                ("assistant", "熵衡量不确定性。", False),
            ],
        )
        self.assertEqual(projection.transcript[2].cards, ({"title": "熵"},))
        self.assertEqual(
            projection.diagnostics,
            (ProjectionDiagnostic("unknown_type", "e7", "unknown entry type: future_thing"),),
        )

    def test_a_tool_result_card_joins_the_step_that_asked_for_it(self) -> None:
        card = {
            "tool": "wiki_read",
            "title": "Entropy",
            "path": "wiki/concepts/entropy.md",
        }
        entries = [
            _entry("e1", "message", role="user", content="什么是熵？"),
            _assistant("e2", "我先查一下。", [_call("t1")]),
            _tool_result("e3", "t1", "熵是……", meta=card),
            _entry(
                "e4",
                "message",
                role="assistant",
                content="熵衡量不确定性。",
                stopReason="stop",
            ),
        ]

        projection = project(entries)

        step = projection.transcript[1]
        self.assertTrue(step.is_step)
        self.assertEqual(step.cards, (card,))
        self.assertEqual(projection.transcript[2].cards, ())

    def test_a_silent_step_still_gets_a_row_for_its_card(self) -> None:
        card = {
            "tool": "wiki_read",
            "title": "Entropy",
            "path": "wiki/concepts/entropy.md",
        }
        entries = [
            _entry("e1", "message", role="user", content="什么是熵？"),
            _assistant("e2", "", [_call("t1")]),
            _tool_result("e3", "t1", "熵是……", meta=card),
            # The run stopped after the tool round; the card must survive it.
        ]

        projection = project(entries)

        self.assertEqual(
            [(item.role, item.content, item.is_step) for item in projection.transcript],
            [("user", "什么是熵？", False), ("assistant", "", True)],
        )
        self.assertEqual(projection.transcript[1].cards, (card,))

    def test_a_completed_wait_becomes_a_job_result_message(self) -> None:
        entries = [
            _entry("e1", "message", role="user", content="安排一下"),
            _entry(
                "w1",
                "wait_proposed",
                waitId="w1",
                job="plan",
                interface="source",
                completion="start_plan",
                proposedAt="2026-11-10T09:00:00Z",
            ),
            _entry("w2", "wait_entered", waitId="w1", jobId="job-9"),
            _entry(
                "w3",
                "wait_completed",
                waitId="w1",
                jobId="job-9",
                job="plan",
                status="completed",
                summary="planned 2 pages",
                artifacts=[
                    {
                        "kind": "write",
                        "id": "concepts/entropy",
                        "label": "Entropy",
                    }
                ],
            ),
            _entry("w4", "resume_started", waitId="w1"),
            _assistant("e2", "规划好了，接下来开始写。"),
        ]

        projection = project(entries)

        self.assertEqual(
            [message.role for message in projection.messages],
            ["user", "system", "assistant"],
        )
        self.assertEqual(projection.messages[0].content, "安排一下")
        self.assertEqual(projection.messages[2].content, "规划好了，接下来开始写。")
        result = projection.messages[1].content
        self.assertTrue(result.startswith("Job result (plan, completed"))
        self.assertIn("planned 2 pages", result)
        self.assertIn("write:concepts/entropy", result)
        self.assertEqual(
            [(item.role, item.content) for item in projection.transcript],
            [("user", "安排一下"), ("assistant", "规划好了，接下来开始写。")],
        )
        self.assertEqual(projection.diagnostics, ())

    def test_a_missing_tool_result_is_filled_in_place(self) -> None:
        entries = [
            _assistant("e1", "", [_call("t1"), _call("t2", name="search")]),
            _tool_result("e2", "t1", "the page"),
        ]

        projection = project(entries)

        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [
                ("assistant", ""),
                ("tool", "the page"),
                ("tool", "missing_result"),
            ],
        )
        self.assertEqual(projection.messages[2].tool_call_id, "t2")
        self.assertEqual(projection.messages[2].name, "search")
        self.assertEqual(
            projection.diagnostics,
            (
                ProjectionDiagnostic(
                    "missing_result",
                    "e1",
                    "tool call t2 has no result",
                ),
            ),
        )

    def test_a_late_tool_result_is_moved_after_its_call(self) -> None:
        entries = [
            _assistant("e1", "", [_call("t1")]),
            _entry("e2", "message", role="user", content="继续"),
            _tool_result("e3", "t1", "the page"),
        ]

        projection = project(entries)

        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [("assistant", ""), ("tool", "the page"), ("user", "继续")],
        )
        self.assertEqual([item.content for item in projection.transcript], ["继续"])

    def test_an_orphan_tool_result_is_reported_and_left_out(self) -> None:
        entries = [_tool_result("e1", "t1", "the page")]

        projection = project(entries)

        self.assertEqual(projection.messages, ())
        self.assertEqual(
            projection.diagnostics,
            (
                ProjectionDiagnostic(
                    "orphan_tool_result",
                    "e1",
                    "tool result t1 has no call",
                ),
            ),
        )

    def test_a_compaction_keeps_its_summary_and_the_entries_it_kept(self) -> None:
        entries = [
            _entry("e1", "message", role="user", content="old question"),
            _assistant("e2", "old answer"),
            _entry(
                "e3",
                "compaction",
                summary="we talked about entropy",
                firstKeptEntryId="e2",
                tokensBefore=42000,
            ),
            _entry("e4", "message", role="user", content="继续"),
        ]

        projection = project(entries)

        self.assertEqual(projection.summary, "we talked about entropy")
        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [("assistant", "old answer"), ("user", "继续")],
        )

    def test_a_compaction_cut_never_splits_a_tool_pair(self) -> None:
        entries = [
            _entry("e1", "message", role="user", content="old question"),
            _assistant("e2", "", [_call("t1")]),
            _tool_result("e3", "t1", "the page"),
            _entry(
                "e4",
                "compaction",
                summary="earlier",
                firstKeptEntryId="e3",
            ),
            _entry("e5", "message", role="user", content="继续"),
        ]

        projection = project(entries)

        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [("assistant", ""), ("tool", "the page"), ("user", "继续")],
        )
        self.assertFalse(
            any(item.kind == "orphan_tool_result" for item in projection.diagnostics)
        )

    def test_a_reset_starts_a_new_context(self) -> None:
        entries = [
            _entry("e1", "message", role="user", content="before"),
            _entry("e2", "reset", head="self"),
            _entry("e3", "message", role="user", content="after"),
        ]

        projection = project(entries)

        self.assertEqual(
            [message.content for message in projection.messages],
            ["after"],
        )


class GoldenSessionTests(unittest.TestCase):
    def test_a_fixed_jsonl_file_projects_to_the_expected_turns(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "session-golden.jsonl"
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(Path(tmp))
            layout.sessions_dir.mkdir(parents=True, exist_ok=True)
            layout.session_path("golden").write_text(
                fixture.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            snapshot = JsonlSessionStore(layout).load("golden")
            projection = project(snapshot.entries)

        self.assertEqual(snapshot.title, "什么是熵？")
        self.assertEqual(
            projection.sections,
            {"identity": "你是 NoteAgent。", "index": "1. 熵"},
        )
        self.assertEqual(
            [(message.role, message.content) for message in projection.messages],
            [
                ("user", "什么是熵？"),
                ("assistant", "我先查一下。"),
                ("tool", "熵是……"),
                ("assistant", "熵衡量不确定性。"),
            ],
        )
        self.assertEqual(
            [(item.role, item.is_step) for item in projection.transcript],
            [("user", False), ("assistant", True), ("assistant", False)],
        )


if __name__ == "__main__":
    unittest.main()
