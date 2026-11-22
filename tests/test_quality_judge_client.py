"""The judge transport: the conversation view and the length retry."""

from __future__ import annotations

import json
import unittest

from evals.quality.judge_client import (
    JUDGE_MAX_TOKENS,
    RetryingJudge,
    conversation_from,
    judge_config,
)
from evals.quality.v3.judge import EXPECTED_KEYS, judge_item
from evals.quality.v3.set import load_quality_set

from noteagent.llm.client import LlmError, LlmTurn
from noteagent.llm.config import LlmConfig

_KEYS = EXPECTED_KEYS


def _config() -> LlmConfig:
    return LlmConfig(
        base_url="http://127.0.0.1:1/v1",
        api_key="k",
        model="test-model",
    )


def _payload_json() -> str:
    return json.dumps(
        {
            name: {"verdict": "pass", "reason": "", "evidence": []}
            for name in _KEYS
        }
    )


class _SequenceJudge:
    """A judge that answers from a list, one call each."""

    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


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


class JudgeRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = load_quality_set().rubric

    def _verdict(self, judge):
        return judge_item(
            self.rubric,
            question="X 是什么？",
            wiki=(("concepts/x", "X", "X body."),),
            conversation=(("user", "X 是什么？"), ("assistant", "X。")),
            expected_points=("要点一",),
            pitfalls=(),
            client=judge,
        )

    def _retrying(self, results):
        sequence = _SequenceJudge(results)
        budgets: list[int] = []

        def client_for(tokens: int):
            budgets.append(tokens)
            return sequence

        return RetryingJudge(client_for, JUDGE_MAX_TOKENS), sequence, budgets

    def test_a_length_turn_is_retried_with_double_the_budget(self) -> None:
        judge, sequence, budgets = self._retrying(
            [
                LlmTurn(
                    "",
                    finish_reason="length",
                    input_tokens=100,
                    output_tokens=10,
                ),
                LlmTurn(
                    _payload_json(),
                    finish_reason="stop",
                    input_tokens=200,
                    output_tokens=20,
                ),
            ]
        )

        verdict = self._verdict(judge)

        self.assertTrue(verdict.passed(self.rubric))
        self.assertEqual(sequence.calls, 2)
        self.assertEqual(
            budgets, [JUDGE_MAX_TOKENS, JUDGE_MAX_TOKENS * 2]
        )
        self.assertEqual(
            (verdict.input_tokens, verdict.output_tokens), (300, 30)
        )

    def test_a_length_error_keeps_its_usage_and_retries(self) -> None:
        judge, sequence, _budgets = self._retrying(
            [
                LlmError(
                    "LLM returned empty message content",
                    finish_reason="length",
                    input_tokens=400,
                    output_tokens=16_384,
                ),
                LlmTurn(
                    _payload_json(),
                    finish_reason="stop",
                    input_tokens=200,
                    output_tokens=20,
                ),
            ]
        )

        verdict = self._verdict(judge)

        self.assertTrue(verdict.passed(self.rubric))
        self.assertEqual(sequence.calls, 2)
        self.assertEqual(
            (verdict.input_tokens, verdict.output_tokens),
            (600, 16_404),
        )

    def test_a_finished_turn_is_not_retried(self) -> None:
        judge, sequence, budgets = self._retrying(
            [LlmTurn(_payload_json(), finish_reason="stop")]
        )

        verdict = self._verdict(judge)

        self.assertTrue(verdict.passed(self.rubric))
        self.assertEqual(sequence.calls, 1)
        self.assertEqual(budgets, [JUDGE_MAX_TOKENS])

    def test_the_base_budget_does_not_drift_across_retries(self) -> None:
        judge, _sequence, budgets = self._retrying(
            [
                LlmTurn("", finish_reason="length"),
                LlmTurn(_payload_json(), finish_reason="stop"),
                LlmTurn("", finish_reason="length"),
                LlmTurn(_payload_json(), finish_reason="stop"),
            ]
        )

        self._verdict(judge)
        self._verdict(judge)

        self.assertEqual(
            budgets,
            [
                JUDGE_MAX_TOKENS,
                JUDGE_MAX_TOKENS * 2,
                JUDGE_MAX_TOKENS * 2,
            ],
        )
        self.assertEqual(judge.max_tokens, JUDGE_MAX_TOKENS)

    def test_the_judge_budget_fits_reasoning_and_stays_cold(self) -> None:
        config = judge_config(_config())

        self.assertEqual(config.max_tokens, 16_384)
        self.assertEqual(config.temperature, 0.0)


if __name__ == "__main__":
    unittest.main()
