"""The V4 judge, the A/B/C/D fixtures, and the calibration against the labels."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from evals.quality.v4.calibration import calibrate_case, calibrate_cases
from evals.quality.v4.case_a import CASE_A
from evals.quality.v4.case_b import CASE_B
from evals.quality.v4.case_c import CASE_C
from evals.quality.v4.case_d import CASE_D
from evals.quality.v4.case_e import CASE_E
from evals.quality.v4.case_run import (
    CaseRunResult,
    run_case,
    shape_checks,
)
from evals.quality.v4.case_spec import ANSWER_KIND, PAGE_KIND
from evals.quality.v4.judge import (
    POINT_IDS,
    JudgeMaterial,
    TaskCompliance,
    V4JudgeVerdict,
    judge_artifact,
)
from evals.quality.v4.quality_run import main as quality_main
from evals.report import PUBLISH, Check

from noteagent.agent.faux import FauxModelClient
from noteagent.llm.client import LlmTurn
from noteagent.llm.config import LlmConfig

CASES = (CASE_A, CASE_B, CASE_C, CASE_D, CASE_E)
EXPECTED = {"A": 5, "B": 5, "C": 4, "D": 3, "E": 5}
DEFECTS = {
    "A-defective": ("correctness", "completeness", "source_legality"),
    "B-defective": ("correctness", "completeness", "source_legality"),
    "C-bad": ("correctness", "completeness"),
    "D-defective": (
        "correctness",
        "completeness",
        "source_legality",
        "honesty",
    ),
    "E-bad": (
        "correctness",
        "completeness",
        "source_legality",
        "honesty",
    ),
}
KINDS = {
    "correctness": "wrong_fact",
    "completeness": "missing_point",
    "source_legality": "illegal_source",
    "honesty": "hidden_gap",
}


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


def _payload(case_id: str, failed: tuple[str, ...] = ()) -> dict:
    expected = EXPECTED[case_id]
    return {
        "points": [
            {
                "id": point,
                "verdict": "fail" if point in failed else "pass",
                "reason": "bad" if point in failed else "ok",
                "evidence": _evidence(
                    "wrong" if point in failed else "quote",
                    "right" if point in failed else "quote",
                ),
            }
            for point in POINT_IDS
        ],
        "expected_points": [
            {
                "point": f"p{index}",
                "covered": index < expected - len(failed),
                "artifact_quote": "q",
            }
            for index in range(expected)
        ],
        "failures": [
            {
                "point": point,
                "kind": KINDS[point],
                "detail": f"{point} failed",
            }
            for point in failed
        ],
    }


def _sample_payloads() -> dict[str, dict]:
    payloads = {}
    for sample, failed in DEFECTS.items():
        case_id = sample[0]
        payloads[sample] = _payload(case_id, failed)
    payloads["A-good"] = _payload("A")
    payloads["C-good"] = _payload("C")
    payloads["E-good"] = _payload("E")
    return payloads


def _responder(payloads: dict[str, dict]):
    def answer(messages):
        content = messages[1].content
        for sample, payload in payloads.items():
            if f"### {sample}" in content:
                return payload
        raise AssertionError("no sample matched the judge message")

    return answer


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
    verdict, _client = _judge(_payload("A"))
    return verdict


class JudgeTests(unittest.TestCase):
    def test_a_valid_verdict_parses_all_five_points(self) -> None:
        verdict, _client = _judge(_payload("A"))

        self.assertEqual(verdict.error, "")
        self.assertEqual(
            [point.id for point in verdict.points], list(POINT_IDS)
        )
        self.assertEqual(verdict.verdicts(), dict.fromkeys(POINT_IDS, "pass"))
        self.assertTrue(verdict.all_points_pass())
        self.assertEqual(len(verdict.expected_points), 5)
        self.assertTrue(verdict.prompt_sha256)

    def test_a_pass_without_an_artifact_quote_is_an_error(self) -> None:
        payload = _payload("A")
        payload["points"][0]["evidence"] = [
            {"artifact": "", "material": "quote"}
        ]

        verdict, _client = _judge(payload)

        self.assertIn("artifact quote", verdict.error)

    def test_a_point_without_evidence_is_an_error(self) -> None:
        payload = _payload("A")
        payload["points"][0]["evidence"] = []

        verdict, _client = _judge(payload)

        self.assertIn("has no evidence", verdict.error)

    def test_an_empty_reason_is_an_error(self) -> None:
        payload = _payload("A")
        payload["points"][0]["reason"] = ""

        verdict, _client = _judge(payload)

        self.assertIn("reason is empty", verdict.error)

    def test_evidence_needs_a_material_quote(self) -> None:
        payload = _payload("A")
        payload["points"][0]["evidence"] = [
            {"artifact": "quote", "material": ""}
        ]

        verdict, _client = _judge(payload)

        self.assertIn("material quote", verdict.error)

    def test_an_overall_key_is_refused(self) -> None:
        payload = _payload("A")
        payload["overall"] = {"passed": True}

        verdict, _client = _judge(payload)

        self.assertIn("unexpected keys", verdict.error)

    def test_harmless_metadata_is_ignored(self) -> None:
        payload = _payload("A")
        payload["type"] = "verdict"

        verdict, _client = _judge(payload)

        self.assertEqual(verdict.error, "")
        self.assertTrue(verdict.all_points_pass())

    def test_a_missing_point_is_refused(self) -> None:
        payload = _payload("A")
        payload["points"] = payload["points"][:-1]

        verdict, _client = _judge(payload)

        self.assertIn("missing points", verdict.error)

    def test_a_failure_for_a_passing_point_is_refused(self) -> None:
        payload = _payload("A")
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
        payload = _payload("D", DEFECTS["D-defective"])
        payload["failures"] = payload["failures"][:1]

        verdict, _client = _judge(payload, expected_count=3)

        self.assertIn("no failure entry", verdict.error)

    def test_an_unknown_failure_kind_is_refused(self) -> None:
        payload = _payload("D", DEFECTS["D-defective"])
        payload["failures"][0]["kind"] = "bogus"

        verdict, _client = _judge(payload, expected_count=3)

        self.assertIn("unknown failure kind", verdict.error)

    def test_the_message_carries_only_the_allowed_inputs(self) -> None:
        verdict, client = _judge(_payload("A"))

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


class FixtureTests(unittest.TestCase):
    def test_case_a_carries_its_source_points_and_samples(self) -> None:
        self.assertEqual(CASE_A.kind, PAGE_KIND)
        self.assertEqual(len(CASE_A.materials), 1)
        self.assertGreater(len(CASE_A.materials[0].text), 3_000)
        self.assertTrue(
            CASE_A.materials[0].id.startswith("raw/")
        )
        self.assertEqual(len(CASE_A.expected_points), 5)
        self.assertEqual(
            CASE_A.sample_names, ("A-good", "A-defective")
        )

    def test_case_b_carries_two_sources_and_one_defect(self) -> None:
        self.assertEqual(CASE_B.kind, PAGE_KIND)
        self.assertEqual(len(CASE_B.materials), 2)
        self.assertTrue(
            all(material.id.startswith("raw/") for material in CASE_B.materials)
        )
        self.assertEqual(len(set(CASE_B.material_ids)), 2)
        self.assertEqual(len(CASE_B.expected_points), 5)
        self.assertEqual(CASE_B.sample_names, ("B-defective",))

    def test_case_c_carries_three_wiki_pages_and_two_samples(self) -> None:
        self.assertEqual(CASE_C.kind, ANSWER_KIND)
        self.assertEqual(len(CASE_C.materials), 3)
        self.assertEqual(
            set(CASE_C.material_ids),
            {"concepts/信息熵", "concepts/联合熵", "concepts/条件熵"},
        )
        self.assertEqual(len(CASE_C.expected_points), 4)
        self.assertEqual(CASE_C.sample_names, ("C-good", "C-bad"))

    def test_case_d_carries_the_five_concept_pages_and_one_defect(
        self,
    ) -> None:
        self.assertEqual(CASE_D.kind, ANSWER_KIND)
        self.assertEqual(len(CASE_D.materials), 8)
        self.assertEqual(
            len(
                [
                    material
                    for material in CASE_D.materials
                    if material.id.startswith("sources/")
                ]
            ),
            3,
        )
        self.assertEqual(len(CASE_D.expected_points), 3)
        self.assertEqual(CASE_D.sample_names, ("D-defective",))
        joint = next(
            material
            for material in CASE_D.materials
            if material.id == "concepts/联合熵"
        )
        self.assertIn("联合熵-3fcc20b258ec.html", joint.text)

    def test_case_e_carries_two_raws_and_the_existing_wiki(self) -> None:
        self.assertEqual(CASE_E.kind, PAGE_KIND)
        self.assertEqual(len(CASE_E.materials), 2)
        self.assertEqual(len(CASE_E.source_ids), 2)
        self.assertEqual(len(CASE_E.wiki_materials), 5)
        self.assertEqual(len(CASE_E.expected_points), 5)
        self.assertEqual(CASE_E.sample_names, ("E-good", "E-bad"))
        self.assertEqual(len(CASE_E.required_reads), 3)

    def test_case_e_requires_the_wiki_reads(self) -> None:
        case = CASE_E
        body = (
            "---\n"
            'title: "互信息与熵"\n'
            "sources: ["
            + ", ".join(
                f'"{source}#b1"' for source in case.source_ids
            )
            + "]\n---\n\n"
            "# 互信息与熵\n\n"
            "## 关系\n\n"
            "正文。\n"
        )
        entries = [
            {
                "role": "assistant",
                "toolCalls": [
                    {
                        "name": "search",
                        "arguments": '{"query": "互信息"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/信息熵"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/互信息"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/联合熵"}',
                    },
                ],
            }
        ]

        checks = shape_checks(
            case,
            body,
            f"已写入 {case.wiki_file}。",
            entries=entries,
        )
        self.assertTrue(all(check.passed for check in checks), checks)

        missing = shape_checks(
            case, body, f"已写入 {case.wiki_file}。"
        )
        read = next(
            check for check in missing if "read the pages" in check.name
        )
        self.assertFalse(read.passed)

    def test_a_page_shape_check_passes_a_page_citing_every_source(
        self,
    ) -> None:
        case = CASE_B
        body = (
            "---\n"
            'title: "熵、KL、互信息的关系"\n'
            "sources: ["
            + ", ".join(
                f'"{material}#b1"' for material in case.material_ids
            )
            + "]\n---\n\n"
            "# 熵、KL、互信息的关系\n\n"
            "## 熵与 KL\n\n"
            "H(P) = -Σ_i P(i) log_a P(i)。\n"
        )

        checks = shape_checks(
            case, body, f"已写入 {case.wiki_file}。"
        )

        self.assertTrue(all(check.passed for check in checks), checks)

    def test_a_page_shape_check_refuses_a_page_missing_one_source(
        self,
    ) -> None:
        case = CASE_B
        body = (
            "---\n"
            'title: "熵、KL、互信息的关系"\n'
            f'sources: ["{case.material_ids[0]}#b1"]\n'
            "---\n\n"
            "# 熵、KL、互信息的关系\n\n"
            "## 熵与 KL\n\n"
            "正文。\n"
        )

        checks = shape_checks(
            case, body, f"已写入 {case.wiki_file}。"
        )

        source = next(
            check for check in checks if "cites" in check.name
        )
        self.assertFalse(source.passed)

    def test_an_answer_shape_check_requires_an_answer(self) -> None:
        entries = [
            {
                "role": "assistant",
                "toolCalls": [
                    {
                        "name": "search",
                        "arguments": '{"query": "链式关系"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/信息熵"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/联合熵"}',
                    },
                    {
                        "name": "wiki_read",
                        "arguments": '{"slug": "concepts/条件熵"}',
                    },
                ],
            }
        ]

        checks = shape_checks(
            CASE_C, "答案在正文。", entries=entries
        )
        self.assertTrue(all(check.passed for check in checks), checks)
        self.assertFalse(
            all(
                check.passed for check in shape_checks(CASE_C, "")
            )
        )

    def test_an_answer_without_tools_fails_the_trace_checks(self) -> None:
        checks = shape_checks(CASE_C, "答案在正文。")

        searched = next(
            check for check in checks if "searched" in check.name
        )
        read = next(
            check for check in checks if "read the pages" in check.name
        )
        self.assertFalse(searched.passed)
        self.assertFalse(read.passed)

    def test_the_real_answer_runner_searches_reads_and_answers(self) -> None:
        config = LlmConfig(
            base_url="http://127.0.0.1:1/v1",
            api_key="test-key",
            model="test-model",
        )
        model = FauxModelClient(
            FauxModelClient.tool_call(
                "c1", "search", '{"query": "链式关系"}'
            ).script,
            FauxModelClient.tool_call(
                "c2", "wiki_read", '{"slug": "concepts/信息熵"}'
            ).script,
            FauxModelClient.tool_call(
                "c3", "wiki_read", '{"slug": "concepts/联合熵"}'
            ).script,
            FauxModelClient.tool_call(
                "c4", "wiki_read", '{"slug": "concepts/条件熵"}'
            ).script,
            FauxModelClient.text(
                "链式关系：H(X,Y)=H(X)+H(Y|X)。"
            ).script,
        )
        judge = _Judge(lambda messages: _payload("C"))

        with tempfile.TemporaryDirectory() as tmp:
            result = run_case(
                CASE_C,
                config,
                failures_root=Path(tmp) / "failures",
                run_id="run-1",
                model=model,
                judge_client=judge,
            )

        self.assertFalse(result.error, result.error)
        self.assertTrue(
            result.passed,
            [check.name for check in result.failed_checks],
        )
        self.assertIn("the agent searched the wiki", [
            check.name for check in result.checks
        ])
        self.assertIn("the agent read the pages the task names", [
            check.name for check in result.checks
        ])

    def test_a_generic_final_answer_does_not_name_the_page(self) -> None:
        case = CASE_A
        body = (
            "---\n"
            'title: "什么是信息熵"\n'
            f'sources: ["{case.material_ids[0]}#b1"]\n'
            "---\n\n"
            "# 什么是信息熵\n\n"
            "## 信息熵\n\n"
            "H(P) = -Σ_i P(i) log_a P(i)。\n"
        )

        checks = shape_checks(case, body, "已写。")

        final = next(
            check for check in checks if "final answer" in check.name
        )
        self.assertFalse(final.passed)

    def test_the_judge_message_carries_both_b_sources(self) -> None:
        client = _Judge(lambda messages: _payload("B"))
        verdict = judge_artifact(
            task=CASE_B.task,
            materials=CASE_B.judge_materials(),
            expected_points=CASE_B.expected_points,
            artifact_id=CASE_B.wiki_file,
            artifact="产物的文字",
            task_compliance=TaskCompliance(True, ("page exists",)),
            client=client,
        )

        self.assertEqual(verdict.error, "")
        message = client.messages[1].content
        for material in CASE_B.materials:
            self.assertIn(material.id, message)
            self.assertIn(material.text[:60], message)

    def test_the_judge_message_carries_the_c_wiki_pages(self) -> None:
        client = _Judge(lambda messages: _payload("C"))
        verdict = judge_artifact(
            task=CASE_C.task,
            materials=CASE_C.judge_materials(),
            expected_points=CASE_C.expected_points,
            artifact_id="case-C answer",
            artifact="答案",
            task_compliance=TaskCompliance(True, ("an answer is present",)),
            client=client,
        )

        self.assertEqual(verdict.error, "")
        message = client.messages[1].content
        for material in CASE_C.materials:
            self.assertIn(material.id, message)


class CalibrationTests(unittest.TestCase):
    def test_the_judge_matches_every_case(self) -> None:
        judge = _Judge(_responder(_sample_payloads()))

        reports = calibrate_cases(CASES, judge)

        self.assertEqual(
            [report.case_id for report in reports],
            ["A", "B", "C", "D", "E"],
        )
        self.assertTrue(all(report.matched for report in reports))
        self.assertEqual(
            [len(report.samples) for report in reports],
            [2, 1, 2, 1, 2],
        )

    def test_a_mismatch_is_reported_point_by_point(self) -> None:
        judge = _Judge(lambda messages: _payload("B"))

        report = calibrate_case(CASE_B, judge)

        self.assertFalse(report.matched)
        self.assertEqual(report.matched_count, 0)
        mismatched = {
            point.point
            for point in report.samples[0].mismatches
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

    def test_the_cli_writes_every_calibration(self) -> None:
        judge = _Judge(_responder(_sample_payloads()))

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
        self.assertEqual(
            payload["calibrations"]["A"]["samplesMatched"], 2
        )
        self.assertEqual(
            payload["calibrations"]["B"]["samplesMatched"], 1
        )
        self.assertEqual(
            payload["calibrations"]["C"]["samplesMatched"], 2
        )
        self.assertEqual(
            payload["calibrations"]["D"]["samplesMatched"], 1
        )
        self.assertEqual(
            payload["calibrations"]["E"]["samplesMatched"], 2
        )
        self.assertEqual(payload["cases"], {})

    def test_the_cli_runs_the_page_cases_when_asked(self) -> None:
        judge = _Judge(_responder(_sample_payloads()))
        calls: list[tuple[str, object, object]] = []

        def runner(case, config, **kwargs):
            calls.append(
                (
                    case.id,
                    kwargs.get("run_id"),
                    kwargs.get("failures_root"),
                )
            )
            return CaseRunResult(
                case_id=case.id,
                checks=(Check("the page exists", True, "", PUBLISH),),
                artifact="# T\n\n## S\n\n正文。",
                final_text=f"已写入 {case.wiki_file}。",
                verdict=_good_verdict(),
                duration_s=0.1,
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            failures = Path(tmp) / "failures"
            with _without_ci():
                code = quality_main(
                    [
                        "--live",
                        "A,B",
                        "--report",
                        str(report),
                        "--failures",
                        str(failures),
                    ],
                    config=self._config(),
                    judge_client=judge,
                    case_runner=runner,
                    run_id="run-1",
                )
            payload = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in calls], ["A", "B"])
        self.assertEqual([call[1] for call in calls], ["run-1", "run-1"])
        self.assertEqual([call[2] for call in calls], [failures, failures])
        self.assertEqual(set(payload["cases"]), {"A", "B"})

    def test_the_cli_runs_the_answer_cases_when_asked(self) -> None:
        judge = _Judge(_responder(_sample_payloads()))
        seen: list[str] = []

        def runner(case, config, **kwargs):
            seen.append(case.kind)
            return CaseRunResult(
                case_id=case.id,
                checks=(Check("an answer is present", True, "", PUBLISH),),
                artifact="答案正文。",
                final_text="答案正文。",
                verdict=_good_verdict(),
                duration_s=0.1,
            )

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "quality.json"
            with _without_ci():
                code = quality_main(
                    [
                        "--live",
                        "C,D",
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
        self.assertEqual(set(payload["cases"]), {"C", "D"})
        self.assertEqual(seen, [ANSWER_KIND, ANSWER_KIND])

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
                        lambda messages: _payload("A")
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
                patch("noteagent.settings.install.install_view"),
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
