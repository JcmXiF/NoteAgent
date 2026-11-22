"""The judge's conversation view: answers, not tool preambles."""

from __future__ import annotations

import unittest

from evals.quality.judge_client import conversation_from


class ConversationTests(unittest.TestCase):
    def test_tool_preambles_are_not_graded_as_answers(self) -> None:
        entries = [
            {"role": "user", "content": "问题"},
            {
                "role": "assistant",
                "content": "我先读一页。",
                "toolCalls": [{"id": "c1", "name": "wiki_read"}],
            },
            {"role": "toolResult", "content": "页"},
            {"role": "assistant", "content": "最终答案"},
        ]

        self.assertEqual(
            conversation_from(entries),
            (("user", "问题"), ("assistant", "最终答案")),
        )

    def test_a_silent_proposal_uses_its_reason_as_the_answer(self) -> None:
        entries = [
            {"type": "message", "role": "user", "content": "整理成对比页"},
            {
                "type": "wait_proposed",
                "job": "compile",
                "intent": "对比页",
                "reason": "我会整理成对比页，需要你在计划板进入编译。",
            },
        ]

        self.assertEqual(
            conversation_from(entries),
            (
                ("user", "整理成对比页"),
                ("assistant", "我会整理成对比页，需要你在计划板进入编译。"),
            ),
        )

    def test_an_assistant_answer_wins_over_the_proposal_reason(self) -> None:
        entries = [
            {"type": "message", "role": "user", "content": "问题"},
            {"type": "message", "role": "assistant", "content": "答案"},
            {
                "type": "wait_proposed",
                "job": "compile",
                "reason": "提案理由",
            },
        ]

        self.assertEqual(
            conversation_from(entries),
            (("user", "问题"), ("assistant", "答案")),
        )


if __name__ == "__main__":
    unittest.main()
