"""The V3 quality runner: Pass@k, per-type strata, and the content verdict."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from evals.harness import CaseRun
from evals.quality.v3.judge import EXPECTED_KEYS, judge_item
from evals.quality.v3.run import main as run_main
from evals.quality.v3.judge import V3JudgeVerdict
from evals.quality.v3.runner import (
    ItemRun,
    ItemSpec,
    QualityReport,
    QualityScores,
    item_specs,
    run_item_once,
    run_quality_set,
)
from evals.quality.v3.set import (
    ESSENTIAL,
    IMPORTANT,
    VETO,
    QualitySet,
    Rubric,
    RubricDimension,
    load_quality_set,
)

from noteagent.agent.faux import FauxModelClient
from noteagent.agent.projection import project as read_projection
from noteagent.agent.run_marker import sections_digest
from noteagent.llm.client import LlmTurn
from noteagent.llm.config import LlmConfig
from noteagent.skills import SkillRootKind, default_registry
from noteagent.skills.index import render_index

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


def _dimension(rubric: Rubric, name: str) -> RubricDimension:
    return next(
        item for item in rubric.graded_dimensions() if item.name == name
    )


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

    def test_a_single_run_reports_verdicts_error_and_judge_usage(
        self,
    ) -> None:
        run = replace(
            _run("fact-1", "single", "fact", 0, False),
            verdicts={
                "grounding": "fail",
                "completeness": "pass",
                "uncertainty": "pass",
                "hallucination": "pass",
            },
            error="judge_error: boom",
            judge_tokens_in=120,
            judge_tokens_out=30,
            judge_prompt_sha256="c" * 64,
        )
        report = QualityReport(
            scores=QualityScores.from_runs([run], k=1),
            runs=(run,),
            set_version="v3.0",
            content_hash="hash",
            model="test-model",
            started_at="now",
            duration_s=1.0,
            temperature=0.1,
            base_url="http://127.0.0.1:1/v1",
        )

        payload = report.payload()
        item = payload["singleTurn"]["items"][0]

        self.assertEqual(item["details"][0]["verdicts"]["grounding"], "fail")
        self.assertEqual(item["details"][0]["error"], "judge_error: boom")
        self.assertEqual(
            (item["judgeTokensIn"], item["judgeTokensOut"]), (120, 30)
        )
        self.assertEqual(payload["judgePromptSha256"], "c" * 64)
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["baseUrl"], "http://127.0.0.1:1/v1")
        summary = report.markdown()
        self.assertIn("Single-turn run details", summary)
        self.assertIn("judge_error: boom", summary)


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

    def test_the_report_carries_the_agent_and_skill_hashes(self) -> None:
        quality = load_quality_set()
        agent = "a" * 64
        skill = "b" * 64

        def runner(spec, index):
            run = _run(spec.item_id, spec.kind, spec.type, index, True)
            return replace(
                run,
                agent_prompt_sha256=agent,
                skill_index_sha256=skill,
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_quality_set(
                quality,
                _config(),
                Path(tmp),
                k=1,
                run_item=runner,
            )

        payload = report.payload()
        self.assertEqual(payload["agentPromptSha256"], agent)
        self.assertEqual(payload["skillIndexSha256"], skill)
        self.assertEqual(payload["temperature"], _config().temperature)
        self.assertEqual(payload["baseUrl"], _config().base_url)
        self.assertIn("Agent prompt", report.markdown())


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

    def test_the_configured_model_name_reaches_the_request(self) -> None:
        quality = load_quality_set()
        spec = item_specs(quality)[0]
        model = FauxModelClient(FauxModelClient.text("答案。").script)
        judge = _Judge(_payload())

        with tempfile.TemporaryDirectory() as tmp:
            run_item_once(
                quality,
                spec,
                0,
                _config(),
                Path(tmp),
                model=model,
                judge_client=judge,
            )

        self.assertEqual(model.requests[0].model, "test-model")

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

    def test_a_run_seeds_the_product_prompt_and_records_its_hashes(
        self,
    ) -> None:
        quality = load_quality_set()
        spec = next(
            item for item in item_specs(quality) if item.type == "fact"
        )
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
            case = json.loads(
                (
                    Path(tmp) / spec.item_id / "run-1" / "case.json"
                ).read_text(encoding="utf-8")
            )

        sections = read_projection(case["entries"]).sections
        for key in ("identity", "tools", "project", "grounding", "skills"):
            self.assertIn(key, sections)
        self.assertIn("<available_skills>", sections["skills"])
        self.assertIn("- writing [bundled]", sections["skills"])
        self.assertNotIn("[user]", sections["skills"])
        self.assertEqual(
            item.agent_prompt_sha256, sections_digest(sections)
        )
        self.assertEqual(
            item.skill_index_sha256,
            hashlib.sha256(
                sections["skills"].encode("utf-8")
            ).hexdigest(),
        )

    def _spec(self, quality: QualitySet, type_: str) -> ItemSpec:
        return next(
            spec for spec in item_specs(quality) if spec.type == type_
        )

    def test_a_gap_item_gets_the_index_purpose_and_the_pages_it_read(
        self,
    ) -> None:
        quality = load_quality_set()
        spec = self._spec(quality, "gap")
        slug, title, _body = spec.pages[0]
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "call-1", "wiki_read", json.dumps({"slug": slug})
            ).script,
            FauxModelClient.text("答案。").script,
        )
        judge = _Judge(_payload())

        with tempfile.TemporaryDirectory() as tmp:
            run_item_once(
                quality,
                spec,
                0,
                _config(),
                Path(tmp),
                model=model,
                judge_client=judge,
            )

        message = judge.messages[1].content
        self.assertIn("## Wiki purpose", message)
        self.assertIn(quality.golden.corpus.purpose.strip(), message)
        self.assertIn(f"- {slug} — {title}", message)
        self.assertIn(f"## Pages the agent read\n- {slug}\n", message)
        self.assertIn(
            _dimension(quality.rubric, "grounding").must[0], message
        )

    def test_a_grounding_failure_fails_the_item(self) -> None:
        quality = load_quality_set()
        spec = self._spec(quality, "fact")
        model = FauxModelClient(FauxModelClient.text("答案。").script)
        judge = _Judge(_payload(grounding="fail"))

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

        self.assertFalse(item.passed)
        self.assertEqual(item.verdicts["grounding"], "fail")
        self.assertIn(
            _dimension(quality.rubric, "grounding").must_not[0],
            judge.messages[1].content,
        )


class SkillRootTests(unittest.TestCase):
    def test_a_case_keeps_the_users_skills_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = CaseRun(Path(tmp))
            try:
                run.start(FauxModelClient.text("answer"))
                registry = default_registry(run.layout.root)
                text = render_index(registry)
                user = next(
                    root.folder
                    for root in registry.roots
                    if root.kind == SkillRootKind.USER
                )
            finally:
                run.close()

        self.assertTrue(
            str(user).startswith(str(Path(tmp).resolve()))
        )
        self.assertIn("- writing [bundled]", text)
        self.assertNotIn("[user]", text)


class JudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = load_quality_set().rubric

    def _judge(
        self,
        payload=None,
        *,
        text: str | None = None,
        rubric: Rubric | None = None,
    ) -> tuple[V3JudgeVerdict, _Judge]:
        client = _Judge(payload, text=text)
        verdict = judge_item(
            rubric or self.rubric,
            question="X 是什么？",
            wiki=(("concepts/x", "X", "X body."),),
            conversation=(
                ("user", "X 是什么？"),
                ("assistant", "X。"),
            ),
            expected_points=("要点一",),
            pitfalls=("编造",),
            read_pages=("concepts/x",),
            wiki_index=("concepts/x — X",),
            purpose="Fixture corpus.",
            client=client,
            secret="key",
        )
        return verdict, client

    def test_all_four_verdicts_must_pass(self) -> None:
        verdict, client = self._judge(_payload())

        self.assertTrue(verdict.passed(self.rubric))
        self.assertIn("要点一", client.messages[1].content)
        self.assertIn("编造", client.messages[1].content)

    def test_the_message_carries_the_purpose_index_and_read_pages(
        self,
    ) -> None:
        _verdict, client = self._judge(_payload())
        message = client.messages[1].content

        self.assertIn("## Wiki purpose", message)
        self.assertIn("Fixture corpus.", message)
        self.assertIn("## Wiki index", message)
        self.assertIn("- concepts/x — X", message)
        self.assertIn(
            "## Pages the agent read\n- concepts/x\n", message
        )

    def test_the_message_carries_every_rubric_rule(self) -> None:
        _verdict, client = self._judge(_payload())
        message = client.messages[1].content

        for dimension in self.rubric.graded_dimensions():
            self.assertIn(
                f"### {dimension.name} ({dimension.weight})", message
            )
            for rule in (*dimension.must, *dimension.must_not):
                self.assertIn(rule, message)
        for key in self.rubric.verdict_keys():
            self.assertIn(f'"{key}":', message)

    def test_the_verdict_keys_come_from_the_rubric(self) -> None:
        rubric = Rubric(
            "v-test",
            (
                RubricDimension("alpha", ESSENTIAL),
                RubricDimension("beta", IMPORTANT),
            ),
            RubricDimension("gamma", VETO),
        )
        payload = {
            name: {"verdict": "pass", "reason": "", "evidence": []}
            for name in rubric.verdict_keys()
        }

        verdict, client = self._judge(payload, rubric=rubric)

        self.assertTrue(verdict.passed(rubric))
        self.assertEqual(
            verdict.verdicts(), dict.fromkeys(rubric.verdict_keys(), "pass")
        )
        self.assertIn('"alpha":', client.messages[1].content)

    def test_a_payload_with_other_keys_is_a_judge_error(self) -> None:
        verdict, _client = self._judge(
            {"grounding": {"verdict": "pass", "reason": "", "evidence": []}}
        )

        self.assertFalse(verdict.passed(self.rubric))
        self.assertIn("judge_error", verdict.error)

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
