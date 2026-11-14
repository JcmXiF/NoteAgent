"""G-Eval: the 1-5 score token, its weighting, and the judge request."""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from evals.golden.dataset import (
    CorpusPage,
    ExpectedTrace,
    GoldenQuery,
)
from evals.golden.geval import (
    DIMENSIONS,
    build_user_prompt,
    judge,
    judge_config,
    parse_score,
)
from noteagent.llm.client import (
    ChatMessage,
    LlmClient,
    LlmTurn,
    TokenLogprob,
)
from noteagent.llm.config import LlmConfig

TOP = (
    (" 4", 0.0),
    (" 5", -8.616),
    (" 3", -15.463),
    (" 1", -16.856),
    (" 0", -17.813),
)


def case(reference: str = "参考答案。") -> GoldenQuery:
    return GoldenQuery(
        id="case-1",
        type="fact",
        difficulty="easy",
        question="X 是什么？",
        corpus_pages=("concepts/x",),
        reference_answer=reference,
        key_points=("要点一", "要点二"),
        expected_trace=ExpectedTrace(
            tools_any=("search", "wiki_read"),
            wait="absent",
            max_tool_rounds=4,
            no_error=True,
        ),
        forbidden=("编造",),
        raw_sources=(),
        notes="",
    )


def page() -> CorpusPage:
    return CorpusPage(
        "concepts/x", "X", "X body.", Path("concepts/x.md")
    )


def score_logprobs(score: int = 4) -> tuple[TokenLogprob, ...]:
    return (TokenLogprob(f" {score}", 0.0, TOP),)


class ParseScoreTests(unittest.TestCase):
    def test_the_soft_score_uses_the_top_logprobs(self) -> None:
        result = parse_score(
            "Steps are done.\nScore: 4",
            score_logprobs(4),
            "grounding",
        )

        weights = {
            int(token.strip()): math.exp(logprob)
            for token, logprob in TOP
            if token.strip() in {"1", "2", "3", "4", "5"}
        }
        expected = sum(
            score * weight for score, weight in weights.items()
        ) / sum(weights.values())
        self.assertEqual(result.score, 4)
        self.assertAlmostEqual(result.soft, expected, places=6)
        self.assertEqual(result.reason, "Steps are done.")
        self.assertEqual(result.error, "")

    def test_without_logprobs_it_falls_back_to_the_discrete_score(self) -> None:
        result = parse_score("Score: 3", (), "coverage")

        self.assertEqual(result.score, 3)
        self.assertEqual(result.soft, 3.0)
        self.assertEqual(result.error, "")

    def test_no_score_is_an_error(self) -> None:
        result = parse_score("I cannot score this.", (), "uncertainty")

        self.assertEqual(result.score, 0)
        self.assertIn("geval_error", result.error)

    def test_reasoning_is_the_reason_when_content_is_only_the_score(
        self,
    ) -> None:
        result = parse_score(
            "Score: 4",
            score_logprobs(4),
            "grounding",
            "Step 1: list the claims. Step 2: check them.",
        )

        self.assertEqual(result.score, 4)
        self.assertEqual(
            result.reason,
            "Step 1: list the claims. Step 2: check them.",
        )


class FakeJudge:
    def __init__(self, scores: tuple[int, ...] = (4, 3, 5)) -> None:
        self.scores = list(scores)
        self.requests: list[list[ChatMessage]] = []
        self.kwargs: list[dict] = []

    def complete(self, messages, **kwargs) -> LlmTurn:
        self.requests.append(list(messages))
        self.kwargs.append(kwargs)
        score = self.scores[len(self.requests) - 1]
        return LlmTurn(
            content=f"Reason {score}.\nScore: {score}",
            logprobs=score_logprobs(score),
        )


class JudgeTests(unittest.TestCase):
    def test_three_dimensions_are_scored_in_order(self) -> None:
        client = FakeJudge((4, 3, 5))

        verdict = judge(
            case(), "answer", [page()], client=client
        )

        self.assertEqual(
            [item.dimension for item in verdict.scores],
            list(DIMENSIONS),
        )
        self.assertEqual(
            [item.score for item in verdict.scores], [4, 3, 5]
        )
        self.assertEqual(
            verdict.means,
            {
                "grounding": verdict.scores[0].soft,
                "coverage": verdict.scores[1].soft,
                "uncertainty": verdict.scores[2].soft,
            },
        )
        for kwargs in client.kwargs:
            self.assertTrue(kwargs["logprobs"])
            self.assertEqual(kwargs["top_logprobs"], 20)
            self.assertIs(kwargs["fallback_reasoning"], False)

    def test_the_request_carries_pages_and_key_points_not_reference(
        self,
    ) -> None:
        client = FakeJudge()

        judge(
            case(reference="SECRET-REFERENCE"),
            "answer",
            [page()],
            client=client,
        )

        text = "\n".join(
            message.content
            for request in client.requests
            for message in request
        )
        self.assertIn("X body.", text)
        self.assertIn("要点一", text)
        self.assertNotIn("SECRET-REFERENCE", text)

    def test_a_client_error_is_recorded(self) -> None:
        class Broken:
            def complete(self, messages, **kwargs):
                raise RuntimeError("boom")

        verdict = judge(
            case(), "answer", [page()], client=Broken()
        )

        self.assertIn("geval_error: boom", verdict.error)
        self.assertEqual(verdict.scores, ())

    def test_the_user_prompt_for_coverage_has_the_key_points(self) -> None:
        text = build_user_prompt(
            case(), "answer", [page()], "coverage"
        )

        self.assertIn("## Key points", text)
        self.assertIn("要点一", text)

    def test_the_judge_sees_the_wiki_index_and_purpose(self) -> None:
        client = FakeJudge()

        judge(
            case(),
            "answer",
            [page()],
            client=client,
            wiki_index=("concepts/x — X",),
            purpose="信息论 wiki",
        )

        text = "\n".join(
            message.content
            for request in client.requests
            for message in request
        )
        self.assertIn("## Wiki index", text)
        self.assertIn("concepts/x — X", text)
        self.assertIn("## Wiki purpose", text)
        self.assertIn("信息论 wiki", text)

    def test_the_judge_config_is_temperature_zero(self) -> None:
        config = judge_config(
            LlmConfig(
                "https://model.test/v1",
                "k",
                "m",
                temperature=0.1,
            )
        )

        self.assertEqual(config.temperature, 0.0)


class LlmLogprobTests(unittest.TestCase):
    def test_logprobs_are_requested_and_parsed(self) -> None:
        seen: dict = {}

        class FakeHTTP:
            def __init__(self, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def post(self, _url, *, headers=None, json=None):
                seen.update(json or {})
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "4"},
                                "logprobs": {
                                    "content": [
                                        {
                                            "token": "4",
                                            "logprob": 0.0,
                                            "top_logprobs": [
                                                {
                                                    "token": "4",
                                                    "logprob": 0.0,
                                                },
                                                {
                                                    "token": "5",
                                                    "logprob": -8.6,
                                                },
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                )

        client = LlmClient(
            LlmConfig("https://model.test/v1", "k", "m")
        )
        with patch("noteagent.llm.client.httpx.Client", FakeHTTP):
            turn = client.complete(
                [ChatMessage("user", "score")],
                logprobs=True,
                top_logprobs=20,
            )

        self.assertTrue(seen["logprobs"])
        self.assertEqual(seen["top_logprobs"], 20)
        self.assertEqual(turn.content, "4")
        self.assertEqual(turn.logprobs[0].token, "4")
        self.assertEqual(
            turn.logprobs[0].top_logprobs,
            (("4", 0.0), ("5", -8.6)),
        )
