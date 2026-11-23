"""The V4 judge, case A's fixtures, and the calibration against the labels."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from evals.quality.v4.calibration import calibrate_case_a
from evals.quality.v4.case_a import (
    CaseAResult,
    load_fixture,
    shape_checks,
)
from evals.quality.v4.judge import (
    POINT_IDS,
    JudgeMaterial,
    TaskCompliance,
    V4JudgeVerdict,
    judge_artifact,
)
from evals.quality.v4.quality_run import main as quality_main
from evals.report import PUBLISH, Check

from noteagent.llm.client import LlmTurn
from noteagent.llm.config import LlmConfig


@contextmanager
def _without_ci():
    """The CLI tests run in CI too; the refusal has its own test."""
    with patch.dict(
        os.environ, {"CI": "", "GITHUB_ACTIONS": ""}, clear=False
    ):
        yield


class _Judge:
    """A judge transport whose answer the test chooses per message."""

    def __init__(self, responder) -> None:
        self.responder = responder
        self.messages = []

    def complete(self, messages, **kwargs):
        self.messages = list(messages)
        return LlmTurn(
            json.dumps(self.responder(messages), ensure_ascii=False)
        )


def _evidence(artifact: str = "quote", material: str = "quote"):
    return [{"artifact": artifact, "material": material}]


def _good_payload(expected_count: int = 5) -> dict:
    return {
        "points": [
            {
                "id": point,
                "verdict": "pass",
                "reason": "ok",
                "evidence": _evidence(),
            }
            for point in POINT_IDS
        ],
        "expected_points": [
            {
                "point": f"p{index}",
                "covered": True,
                "artifact_quote": "q",
            }
            for index in range(expected_count)
        ],
        "failures": [],
    }


def _bad_payload(expected_count: int = 5) -> dict:
    failed = ("correctness", "completeness", "source_legality")
    payload = _good_payload(expected_count)
    payload["points"] = [
        {
            "id": point,
            "verdict": "fail" if point in failed else "pass",
            "reason": "bad" if point in failed else "ok",
            "evidence": _evidence("wrong", "right"),
        }
        for point in POINT_IDS
    ]
    payload["failures"] = [
        {
            "point": "correctness",
            "kind": "wrong_fact",
            "detail": "the KL formula is reversed",
        },
        {
            "point": "completeness",
            "kind": "missing_point",
            "detail": "the relation is absent",
        },
        {
            "point": "source_legality",
            "kind": "illegal_source",
            "detail": "a wiki page outside the allowed set",
        },
    ]
    return payload


def _judge(
    payload: dict, expected_count: int = 5
) -> tuple[V4JudgeVerdict, _Judge]:
    client = _Judge(lambda messages: payload)
    verdict = judge_artifact(
        task="写一页。",
        materials=(JudgeMaterial("raw/source.html", "Source", "材料正文"),),
        expected_points=tuple(
            f"p{index}" for index in range(expected_count)
        ),
        artifact_id="wiki/page.md",
        artifact="产物的文字",
        task_compliance=TaskCompliance(True, ("page exists",)),
        client=client,
    )
    return verdict, client


def _good_verdict() -> V4JudgeVerdict:
    verdict, _client = _judge(_good_payload())
    return verdict


class JudgeTests(unittest.TestCase):
    def test_a_valid_verdict_parses_all_five_points(self) -> None:
        verdict, _client = _judge(_good_payload())

        self.assertEqual(verdict.error, "")
        self.assertEqual(
            [point.id for point in verdict.points], list(POINT_IDS)
        )
        self.assertEqual(verdict.verdicts(), dict.fromkeys(POINT_IDS, "pass"))
        self.assertTrue(verdict.all_points_pass())
        self.assertEqual(len(verdict.expected_points), 5)
        self.assertTrue(verdict.prompt_sha256)

    def test_a_pass_without_an_artifact_quote_is_an_error(self) -> None:
        payload = _good_payload()
        payload["points"][0]["evidence"] = [
            {"artifact": "", "material": "quote"}
        ]

        verdict, _client = _judge(payload)

        self.assertIn("artifact quote", verdict.error)

    def test_a_point_without_evidence_is_an_error(self) -> None:
        payload = _good_payload()
        payload["points"][0]["evidence"] = []

        verdict, _client = _judge(payload)

        self.assertIn("has no evidence", verdict.error)

    def test_an_empty_reason_is_an_error(self) -> None:
        payload = _good_payload()
        payload["points"][0]["reason"] = ""

        verdict, _client = _judge(payload)

        self.assertIn("reason is empty", verdict.error)

    def test_evidence_needs_a_material_quote(self) -> None:
        payload = _good_payload()
        payload["points"][0]["evidence"] = [
            {"artifact": "quote", "material": ""}
        ]

        verdict, _client = _judge(payload)

        self.assertIn("material quote", verdict.error)

    def test_an_overall_key_is_refused(self) -> None:
        payload = _good_payload()
        payload["overall"] = {"passed": True}

        verdict, _client = _judge(payload)

        self.assertIn("unexpected keys", verdict.error)

    def test_a_missing_point_is_refused(self) -> None:
        payload = _good_payload()
        payload["points"] = payload["points"][:-1]

        verdict, _client = _judge(payload)

        self.assertIn("missing points", verdict.error)

    def test_a_failure_for_a_passing_point_is_refused(self) -> None:
        payload = _good_payload()
        payload["failures"] = [
            {
                "point": "honesty",
                "kind": "other",
                "detail": "inconsistent",
            }
        ]

        verdict, _client = _judge(payload)

        self.assertIn("passing point", verdict.error)

    def test_a_failed_point_needs_its_failure_entry(self) -> None:
        payload = _bad_payload()
        payload["failures"] = payload["failures"][:1]

        verdict, _client = _judge(payload)

        self.assertIn("no failure entry", verdict.error)

    def test_an_unknown_failure_kind_is_refused(self) -> None:
        payload = _bad_payload()
        payload["failures"][0]["kind"] = "bogus"

        verdict, _client = _judge(payload)

        self.assertIn("unknown failure kind", verdict.error)

    def test_the_message_carries_only_the_allowed_inputs(self) -> None:
        verdict, client = _judge(_good_payload())

        self.assertEqual(verdict.error, "")
        message = client.messages[1].content
        self.assertIn("## Task", message)
        self.assertIn("raw/source.html", message)
        self.assertIn("材料正文", message)
        self.assertIn("## Expected points", message)
        self.assertIn("## Artifact", message)
        self.assertIn("## Deterministic task-compliance", message)
        self.assertNotIn("Wiki index", message)
        self.assertNotIn("labels", message)
        self.assertNotIn("reference answer", message)
        self.assertNotIn("trace", message)


class CaseAFixtureTests(unittest.TestCase):
    def test_the_fixture_carries_the_source_points_and_samples(self) -> None:
        fixture = load_fixture()

        self.assertGreater(len(fixture.material_text), 3_000)
        self.assertTrue(fixture.material_id.startswith("raw/"))
        self.assertEqual(len(fixture.expected_points), 5)
        self.assertEqual(set(fixture.labels), {"A-good", "A-defective"})
        self.assertEqual(len(fixture.artifacts), 2)

    def test_shape_checks_pass_a_page_with_the_allowed_source(self) -> None:
        fixture = load_fixture()
        page = (
            "---\n"
            'title: "什么是信息熵"\n'
            f'sources: ["{fixture.material_id}#b1"]\n'
            "---\n\n"
            "# 什么是信息熵\n\n"
            "## 信息熵\n\n"
            "H(P) = -Σ_i P(i) log_a P(i)。\n"
        )

        checks = shape_checks(
            page, fixture.material_id, "已写入 wiki/sources/。"
        )

        self.assertTrue(all(check.passed for check in checks), checks)

    def test_shape_checks_refuse_a_page_without_the_allowed_source(
        self,
    ) -> None:
        fixture = load_fixture()
        page = (
            "---\ntitle: \"X\"\nsources: []\n---\n\n"
            "# X\n\n## A\n\n正文。\n"
        )

        checks = shape_checks(
            page, fixture.material_id, "已写入 wiki/sources/。"
        )

        source = next(
            check for check in checks if "cites" in check.name
        )
        self.assertFalse(source.passed)

    def test_a_generic_final_answer_does_not_name_the_page(self) -> None:
        fixture = load_fixture()
        page = (
            "---\n"
            'title: "什么是信息熵"\n'
            f'sources: ["{fixture.material_id}#b1"]\n'
            "---\n\n"
            "# 什么是信息熵\n\n"
            "## 信息熵\n\n"
            "H(P) = -Σ_i P(i) log_a P(i)。\n"
        )

        checks = shape_checks(page, fixture.material_id, "已写。")

        final = next(
            check for check in checks if "final answer" in check.name
        )
        self.assertFalse(final.passed)


class CalibrationTests(unittest.TestCase):
    def test_the_judge_matches_both_labels(self) -> None:
        good = _good_payload()
        bad = _bad_payload()
        judge = _Judge(
            lambda messages: (
                bad
                if "### A-defective" in messages[1].content
                else good
            )
        )

        report = calibrate_case_a(judge)

        self.assertEqual(report.matched_count, 2)
        self.assertEqual(
            [sample.sample for sample in report.samples],
            ["A-good", "A-defective"],
        )
        self.assertEqual(report.samples[1].mismatches, ())

    def test_a_mismatch_is_reported_point_by_point(self) -> None:
        judge = _Judge(lambda messages: _good_payload())

        report = calibrate_case_a(judge)

        self.assertEqual(report.matched_count, 1)
        mismatched = {
            point.point for point in report.samples[1].mismatches
        }
        self.assertEqual(
            mismatched,
            {"correctness", "completeness", "source_legality"},
        )


class QualityCliTests(unittest.TestCase):
    def _config(self) -> LlmConfig:
        return LlmConfig(
            base_url="http://127.0.0.1:1/v1",
            api_key="test-key",
            model="test-model",
        )

    def test_the_cli_writes_the_calibration_report(self) -> None:
        good = _good_payload()
        bad = _bad_payload()
        judge = _Judge(
            lambda messages: (
                bad
                if "### A-defective" in messages[1].content
                else good
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            with _without_ci():
                code = quality_main(
                    [
                        "--report",
                        str(report),
                        "--failures",
                        str(Path(tmp) / "failures"),
                    ],
                    config=self._config(),
                    judge_client=judge,
                    run_id="run-1",
                )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["calibration"]["matched"], 2)
        self.assertIsNone(payload["caseA"])

    def test_the_cli_runs_case_a_when_asked(self) -> None:
        good = _good_payload()
        bad = _bad_payload()
        judge = _Judge(
            lambda messages: (
                bad
                if "### A-defective" in messages[1].content
                else good
            )
        )
        case = CaseAResult(
            checks=(Check("the page exists", True, "", PUBLISH),),
            artifact="# T\n\n## S\n\n正文。",
            final_text="已写入。",
            verdict=_good_verdict(),
            duration_s=0.1,
        )
        calls: dict[str, object] = {}

        def runner(config, **kwargs):
            calls.update(kwargs)
            return case

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            with _without_ci():
                code = quality_main(
                    [
                        "--case-a",
                        "--report",
                        str(report),
                        "--failures",
                        str(Path(tmp) / "failures"),
                    ],
                    config=self._config(),
                    judge_client=judge,
                    case_runner=runner,
                    run_id="run-1",
                )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(calls["run_id"], "run-1")
        self.assertEqual(
            len(payload["caseA"]["judge"]["points"]), 5
        )
        self.assertEqual(payload["caseA"]["checks"][0]["name"], "the page exists")

    def test_ci_refuses_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            with patch.dict(os.environ, {"CI": "1"}, clear=False):
                code = quality_main(
                    [
                        "--report",
                        str(report),
                        "--failures",
                        str(Path(tmp) / "failures"),
                    ],
                    config=self._config(),
                    judge_client=_Judge(
                        lambda messages: _good_payload()
                    ),
                )
            written = report.is_file()

        self.assertEqual(code, 2)
        self.assertFalse(written)

    def test_a_missing_model_config_is_a_clear_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            with (
                _without_ci(),
                patch(
                    "noteagent.settings.install.install_view"
                ),
                patch(
                    "noteagent.llm.config.load_llm_config",
                    side_effect=RuntimeError("missing model"),
                ),
            ):
                code = quality_main(
                    [
                        "--report",
                        str(report),
                        "--failures",
                        str(Path(tmp) / "failures"),
                    ]
                )
            written = report.is_file()

        self.assertEqual(code, 2)
        self.assertFalse(written)


if __name__ == "__main__":
    unittest.main()
