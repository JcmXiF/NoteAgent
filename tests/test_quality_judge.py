"""The judge: strict JSON, the request it builds, and what it keeps."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from evals.quality.cases import load_case
from evals.quality.judge import (
    DIMENSIONS,
    conversation_from,
    judge_case,
    parse_verdict,
    prompt_digest,
)
from fakes import ALL_PASS, FakeJudge

CASE_DIR = (
    Path(__file__).resolve().parent.parent
    / "evals"
    / "quality"
    / "cases"
    / "single-page-qa"
)


def conversation() -> list[dict[str, object]]:
    return [
        {
            "type": "message",
            "role": "user",
            "content": "熵和交叉熵的区别是什么？",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": "熵衡量不确定性；交叉熵等于熵加 KL 散度。",
        },
    ]


class ParseVerdictTests(unittest.TestCase):
    def test_a_well_formed_verdict_is_parsed(self) -> None:
        verdict = parse_verdict(ALL_PASS)

        self.assertEqual(
            [item.dimension for item in verdict.dimensions], list(DIMENSIONS)
        )
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.error, "")

    def test_a_code_fence_is_refused(self) -> None:
        verdict = parse_verdict("```json\n" + ALL_PASS + "\n```")

        self.assertIn("judge_error", verdict.error)
        self.assertFalse(verdict.passed)

    def test_a_missing_dimension_is_an_error(self) -> None:
        payload = {
            "grounding": {"verdict": "pass"},
            "completeness": {"verdict": "pass"},
        }

        verdict = parse_verdict(json.dumps(payload))

        self.assertIn("judge_error", verdict.error)
        self.assertIn("uncertainty", verdict.error)
        self.assertFalse(verdict.passed)

    def test_an_invalid_verdict_is_an_error(self) -> None:
        payload = json.loads(ALL_PASS)
        payload["grounding"]["verdict"] = "maybe"

        verdict = parse_verdict(json.dumps(payload))

        self.assertIn("judge_error", verdict.error)
        self.assertIn("grounding", verdict.error)

    def test_a_non_json_reply_is_an_error_and_keeps_the_raw(self) -> None:
        verdict = parse_verdict("I think it is fine.")

        self.assertIn("judge_error", verdict.error)
        self.assertIn("I think it is fine.", verdict.raw)

    def test_an_unexpected_dimension_is_an_error(self) -> None:
        payload = json.loads(ALL_PASS)
        payload["style"] = {"verdict": "pass"}

        verdict = parse_verdict(json.dumps(payload))

        self.assertIn("judge_error", verdict.error)
        self.assertIn("style", verdict.error)


class JudgeRequestTests(unittest.TestCase):
    def test_the_request_carries_the_wiki_conversation_and_rules(self) -> None:
        case = load_case(CASE_DIR)
        client = FakeJudge(ALL_PASS)

        verdict = judge_case(
            case, conversation(), client=client, secret="secret-key"
        )

        self.assertTrue(verdict.passed, verdict.error)
        self.assertTrue(client.requests)
        text = "\n".join(
            f"{message.role}:\n{message.content}"
            for message in client.requests[0]
        )
        self.assertIn("熵与交叉熵", text)
        self.assertIn("熵衡量一个分布的不确定性", text)
        self.assertIn("熵和交叉熵的区别是什么？", text)
        self.assertIn("交叉熵等于熵加 KL 散度", text)
        self.assertIn("每个事实断言都能在 seeded wiki 中找到依据", text)
        self.assertNotIn("tool_result", text)
        self.assertNotIn("secret-key", text)
        self.assertEqual(
            client.kwargs["response_format"], {"type": "json_object"}
        )
        self.assertIs(client.kwargs["fallback_reasoning"], False)

    def test_no_assistant_answer_skips_the_judge(self) -> None:
        case = load_case(CASE_DIR)
        client = FakeJudge(ALL_PASS)

        verdict = judge_case(
            case,
            [{"type": "message", "role": "user", "content": "熵是什么？"}],
            client=client,
        )

        self.assertTrue(verdict.skipped)
        self.assertFalse(verdict.passed)
        self.assertEqual(client.requests, [])

    def test_the_raw_and_reasons_are_redacted(self) -> None:
        case = load_case(CASE_DIR)
        reply = ALL_PASS.replace("有 wiki 依据", "secret-key leaked")
        client = FakeJudge(reply)

        verdict = judge_case(
            case, conversation(), client=client, secret="secret-key"
        )

        self.assertNotIn("secret-key", verdict.raw)
        self.assertNotIn(
            "secret-key", json.dumps(verdict.to_payload(), ensure_ascii=False)
        )

    def test_the_prompt_digest_changes_with_the_rules(self) -> None:
        self.assertNotEqual(
            prompt_digest("prompt", "rule a"),
            prompt_digest("prompt", "rule b"),
        )


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
