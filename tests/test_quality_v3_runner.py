"""The V3 quality runner: Pass@k, per-type strata, and the content verdict."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.quality.v3.judge import EXPECTED_KEYS, judge_item
from evals.quality.v3.run import main as run_main
from evals.quality.v3.runner import (
    ItemRun,
    QualityReport,
    QualityScores,
    item_specs,
    run_item_once,
    run_quality_set,
)
from evals.quality.v3.set import load_quality_set

from noteagent.agent.faux import FauxModelClient
from noteagent.llm.client import LlmTurn
from noteagent.llm.config import LlmConfig

_KEYS = EXPECTED_KEYS


def _config() -> LlmConfig:
    return LlmConfig(
        base_url="http://127.0.0.1:1/v1",
        api_key="test-key",
        model="test-model",
    )


def _payload(**overrides: str) -> dict[str, object]:
    payload = {
        name: {"verdict": "pass", "reason": "", "evidence": []}
        for name in _KEYS
    }
    for name, verdict in overrides.items():
        payload[name] = {"verdict": verdict, "reason": "", "evidence": []}
    return payload


class _Judge:
    def __init__(self, payload=None, *, text: str | None = None) -> None:
        self.payload = payload
        self.text = text
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages = list(messages)
        body = self.text if self.text is not None else json.dumps(self.payload)
        return LlmTurn(body)


def _run(
    item_id: str,
    kind: str,
    type_: str,
    index: int,
    passed: bool,
    *,
    diagnostics: dict[str, object] | None = None,
) -> ItemRun:
    return ItemRun(
        item_id=item_id,
        kind=kind,
        type=type_,
        run=index,
        passed=passed,
        verdicts={name: "pass" for name in _KEYS},
        diagnostics=diagnostics or {"passed": False},
        tokens_in=10,
        tokens_out=5,
        latency_s=0.1,
        cost=0.001,
    )


def _scores() -> tuple[QualityScores, tuple[ItemRun, ...]]:
    runs: list[ItemRun] = []
    singles = (
        ("fact-1", "fact", (False, True, False)),
        ("syn-1", "synthesis", (True, True, True)),
        ("gap-1", "gap", (False, False, False)),
        ("task-1", "task", (False, False, True)),
    )
    for item_id, type_, passes in singles:
        for index, passed in enumerate(passes):
            runs.append(_run(item_id, "single", type_, index, passed))
    for item_id, passes in (
        ("single-page-qa", (True, False, True)),
        ("cross-page-gap", (False, False, False)),
    ):
        for index, passed in enumerate(passes):
            runs.append(_run(item_id, "multi", "multi", index, passed))
    return QualityScores.from_runs(runs, k=3), tuple(runs)


class QualityScoresTests(unittest.TestCase):
    def test_overall_pass_at_k_and_pass_hat_k(self) -> None:
        scores, _runs = _scores()

        self.assertEqual(scores.k, 3)
        self.assertEqual(
            (scores.overall.pass_at_k.passed, scores.overall.pass_at_k.total),
            (3, 4),
        )
        self.assertEqual(
            (scores.overall.pass_hat_k.passed, scores.overall.pass_hat_k.total),
            (1, 4),
        )
        self.assertEqual(scores.overall.pass_at_k.rate, 0.75)
        self.assertEqual(scores.overall.pass_hat_k.rate, 0.25)

    def test_by_type_is_stratified(self) -> None:
        scores, _runs = _scores()

        by_type = {item.type: item for item in scores.by_type}

        self.assertEqual(
            (by_type["fact"].pass_at_k.passed, by_type["fact"].pass_at_k.total),
            (1, 1),
        )
        self.assertEqual(
            (by_type["fact"].pass_hat_k.passed, by_type["fact"].pass_hat_k.total),
            (0, 1),
        )
        self.assertEqual(
            (by_type["synthesis"].pass_hat_k.passed, by_type["synthesis"].pass_hat_k.total),
            (1, 1),
        )
        self.assertEqual(
            (by_type["gap"].pass_at_k.passed, by_type["gap"].pass_at_k.total),
            (0, 1),
        )

    def test_multi_turn_is_reported_one_by_one(self) -> None:
        scores, _runs = _scores()

        self.assertEqual(
            [item.item_id for item in scores.multi],
            ["single-page-qa", "cross-page-gap"],
        )
        self.assertEqual(scores.multi[0].runs, (True, False, True))
        self.assertEqual(scores.multi[1].runs, (False, False, False))


class ReportTests(unittest.TestCase):
    def test_the_payload_keeps_denominators_and_individual_multi_runs(
        self,
    ) -> None:
        scores, runs = _scores()
        report = QualityReport(
            scores=scores,
            runs=runs,
            set_version="v3.0",
            content_hash="hash",
            model="test-model",
            started_at="now",
            duration_s=1.0,
        )

        payload = report.payload()

        self.assertEqual(
            payload["singleTurn"]["overall"]["passAtK"],
            {"passed": 3, "total": 4, "rate": 0.75},
        )
        self.assertEqual(
            payload["singleTurn"]["byType"]["gap"]["passHatK"],
            {"passed": 0, "total": 1, "rate": 0.0},
        )
        self.assertEqual(len(payload["multiTurn"]), 2)
        self.assertNotIn(
            "passAtK", payload["multiTurn"][0]
        )
        self.assertEqual(
            len(payload["multiTurn"][0]["runs"]), 3
        )
        item = payload["singleTurn"]["items"][0]
        self.assertEqual(
            (item["tokensIn"], item["tokensOut"]), (30, 15)
        )
        summary = report.markdown()
        self.assertIn("Pass@k", summary)
        self.assertIn("Multi-turn (individual", summary)
        self.assertIn("single-page-qa", summary)
        self.assertIn("Grounding", summary)
        self.assertIn("pass", summary)


class RunSetTests(unittest.TestCase):
    def test_the_frozen_set_runs_k_times_and_reports_both_strata(self) -> None:
        quality = load_quality_set()

        def runner(spec, index):
            return _run(
                spec.item_id,
                spec.kind,
                spec.type,
                index,
                passed=index == 0,
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_quality_set(
                quality,
                _config(),
                Path(tmp),
                k=3,
                run_item=runner,
            )

        self.assertEqual(report.scores.k, 3)
        self.assertEqual(len(report.scores.single), 24)
        self.assertEqual(len(report.scores.multi), 2)
        self.assertEqual(report.scores.overall.pass_at_k.total, 24)
        self.assertEqual(report.scores.overall.pass_at_k.passed, 24)
        self.assertEqual(report.scores.overall.pass_hat_k.passed, 0)
        payload = report.payload()
        self.assertEqual(len(payload["multiTurn"]), 2)


class RunItemTests(unittest.TestCase):
    def test_content_passes_even_when_the_trace_diagnostic_fails(self) -> None:
        quality = load_quality_set()
        spec = item_specs(quality)[0]
        model = FauxModelClient(FauxModelClient.text("答案。").script)
        judge = _Judge(_payload())

        with tempfile.TemporaryDirectory() as tmp:
            item = run_item_once(
                quality,
                spec,
                0,
                _config(),
                Path(tmp),
                model=model,
                judge_client=judge,
            )

        self.assertTrue(item.passed)
        self.assertEqual(item.verdicts, {name: "pass" for name in _KEYS})
        self.assertFalse(item.diagnostics["passed"])
        self.assertEqual(item.error, "")
        self.assertIn(spec.expected_points[0], judge.messages[1].content)

    def test_a_known_model_gets_a_cost(self) -> None:
        quality = load_quality_set()
        spec = item_specs(quality)[0]
        model = FauxModelClient(FauxModelClient.text("答案。").script)
        judge = _Judge(_payload())
        config = LlmConfig(
            base_url="http://127.0.0.1:1/v1",
            api_key="test-key",
            model="gpt-4o-mini",
        )

        with tempfile.TemporaryDirectory() as tmp:
            item = run_item_once(
                quality,
                spec,
                0,
                config,
                Path(tmp),
                model=model,
                judge_client=judge,
            )

        self.assertEqual(item.cost, 0.0)


class JudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = load_quality_set().rubric

    def _judge(self, payload=None, *, text=None):
        client = _Judge(payload, text=text)
        verdict = judge_item(
            self.rubric,
            question="X 是什么？",
            wiki=(("X", "X body."),),
            conversation=(("user", "X 是什么？"), ("assistant", "X。")),
            expected_points=("要点一",),
            pitfalls=("编造",),
            client=client,
            secret="key",
        )
        return verdict, client

    def test_all_four_verdicts_must_pass(self) -> None:
        verdict, client = self._judge(_payload())

        self.assertTrue(verdict.passed(self.rubric))
        self.assertIn("要点一", client.messages[1].content)
        self.assertIn("编造", client.messages[1].content)

    def test_the_hallucination_veto_fails_the_item(self) -> None:
        verdict, _client = self._judge(
            _payload(hallucination="fail")
        )

        self.assertFalse(verdict.passed(self.rubric))

    def test_invalid_json_is_a_judge_error(self) -> None:
        verdict, _client = self._judge(text="not json")

        self.assertFalse(verdict.passed(self.rubric))
        self.assertIn("judge_error", verdict.error)


class CIRefusalTests(unittest.TestCase):
    def test_ci_refuses_and_writes_nothing(self) -> None:
        quality = load_quality_set()

        def runner(spec, index):
            return _run(
                spec.item_id, spec.kind, spec.type, index, passed=True
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            artifacts = Path(tmp) / "artifacts"
            with patch.dict(os.environ, {"CI": "1"}, clear=False):
                refused = run_main(
                    [
                        "--report",
                        str(report),
                        "--artifacts",
                        str(artifacts),
                    ],
                    quality=quality,
                    run_item=runner,
                    config=_config(),
                )
                written = report.is_file()

        self.assertEqual(refused, 2)
        self.assertFalse(written)


if __name__ == "__main__":
    unittest.main()
