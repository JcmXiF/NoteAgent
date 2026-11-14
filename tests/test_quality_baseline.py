"""Baseline: save a run's verdicts and compare the next run to them."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.quality.assertions import AssertionResult
from evals.quality.baseline import (
    BaselineError,
    compare,
    load_baseline,
    save_baseline,
)
from evals.quality.judge import DimensionVerdict, JudgeVerdict
from evals.quality.report import (
    AgentInfo,
    CaseSummary,
    JudgeInfo,
    QualityReport,
)

DIMENSIONS = ("grounding", "completeness", "uncertainty")


def judge(*verdicts: str, prompt: str = "hash") -> JudgeVerdict:
    return JudgeVerdict(
        dimensions=tuple(
            DimensionVerdict(name, verdict, verdict)
            for name, verdict in zip(DIMENSIONS, verdicts)
        ),
        prompt_sha256=prompt,
    )


def case(
    name: str,
    *,
    passed: bool = True,
    assertions: dict[str, bool] | None = None,
    verdicts: tuple[str, str, str] = ("pass", "pass", "pass"),
    prompt: str = "hash",
) -> CaseSummary:
    return CaseSummary(
        name=name,
        title=name,
        passed=passed,
        duration_s=1.0,
        assertions=tuple(
            AssertionResult(key, value, key, key)
            for key, value in (assertions or {"tools_any": True}).items()
        ),
        judge=judge(*verdicts, prompt=prompt),
    )


def report(
    cases: tuple[CaseSummary, ...],
    *,
    model: str = "a-model",
    base_url: str = "https://api.example.com/v1",
    temperature: float = 0.1,
    judge_model: str = "a-model",
) -> QualityReport:
    return QualityReport(
        started_at="2026-11-11T01:00:00.000Z",
        duration_s=1.0,
        agent=AgentInfo(
            model=model, base_url=base_url, temperature=temperature
        ),
        cases=cases,
        judge=JudgeInfo(model=judge_model, temperature=0.0),
    )


class SaveAndLoadTests(unittest.TestCase):
    def test_a_baseline_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            save_baseline(
                path,
                report(
                    (
                        case(
                            "sample",
                            assertions={"tools_any": True, "wait": True},
                        ),
                    )
                ),
            )

            baseline = load_baseline(path)

            self.assertIsNotNone(baseline)
            self.assertEqual(baseline.created_at, "2026-11-11T01:00:00.000Z")
            self.assertEqual(baseline.agent["model"], "a-model")
            self.assertEqual(baseline.judge["temperature"], 0.0)
            sample = baseline.cases["sample"]
            self.assertTrue(sample.passed)
            self.assertEqual(
                sample.assertions, {"tools_any": True, "wait": True}
            )
            self.assertEqual(sample.judge["grounding"], "pass")
            self.assertEqual(sample.prompt_sha256, "hash")

    def test_a_missing_baseline_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                load_baseline(Path(tmp) / "missing.json")
            )

    def test_a_corrupt_baseline_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text("{not json", encoding="utf-8")

            with self.assertRaises(BaselineError):
                load_baseline(path)

    def test_a_mistyped_baseline_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "created_at": "",
                        "agent": {},
                        "judge": {},
                        "cases": {
                            "sample": {
                                "passed": "yes",
                                "assertions": {},
                                "judge": {},
                                "prompt_sha256": "",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(BaselineError):
                load_baseline(path)

    def test_the_baseline_never_holds_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            save_baseline(
                path,
                report(
                    (case("sample"),),
                    base_url="https://user:secret@api.example.com/v1",
                ),
            )

            text = path.read_text(encoding="utf-8")

        self.assertNotIn("secret", text)


class CompareTests(unittest.TestCase):
    def baseline(self, *cases: CaseSummary):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            save_baseline(path, report(tuple(cases)))
            return load_baseline(path)

    def test_statuses_are_new_fixed_regressed_or_unchanged(self) -> None:
        baseline = self.baseline(
            case("same"),
            case("fixed", passed=False),
            case("regressed", passed=True),
            case("dropped"),
        )
        current = report(
            (
                case("same"),
                case("fixed", passed=True),
                case("regressed", passed=False),
                case("new"),
            )
        )

        delta = compare(current, baseline)

        statuses = {item.name: item.status for item in delta.cases}
        self.assertEqual(
            statuses,
            {
                "same": "unchanged",
                "fixed": "fixed",
                "regressed": "regressed",
                "new": "new",
            },
        )
        self.assertNotIn("dropped", statuses)

    def test_assertion_and_judge_changes_are_named(self) -> None:
        baseline = self.baseline(
            case(
                "sample",
                assertions={"tools_any": True, "wait": True},
            )
        )
        current = report(
            (
                case(
                    "sample",
                    assertions={"tools_any": False, "wait": True},
                    verdicts=("fail", "pass", "pass"),
                ),
            )
        )

        delta = compare(current, baseline)

        self.assertEqual(delta.cases[0].assertions, ("tools_any",))
        self.assertEqual(delta.cases[0].judge, ("grounding",))

    def test_config_changes_are_named(self) -> None:
        baseline = self.baseline(case("sample"))
        current = report(
            (case("sample"),),
            model="other-model",
            temperature=0.2,
            judge_model="judge-model",
        )

        delta = compare(current, baseline)

        self.assertEqual(
            delta.config_changes,
            ("agent.model", "agent.temperature", "judge.model"),
        )

    def test_a_prompt_hash_change_is_named(self) -> None:
        baseline = self.baseline(case("sample", prompt="old"))
        current = report((case("sample", prompt="new"),))

        delta = compare(current, baseline)

        self.assertEqual(delta.prompt_changes, ("sample",))

    def test_a_clean_repeat_has_no_changes(self) -> None:
        baseline = self.baseline(case("sample"))
        current = report((case("sample"),))

        delta = compare(current, baseline)

        self.assertEqual(delta.cases[0].status, "unchanged")
        self.assertEqual(delta.cases[0].assertions, ())
        self.assertEqual(delta.cases[0].judge, ())
        self.assertEqual(delta.config_changes, ())
        self.assertEqual(delta.prompt_changes, ())


class PayloadTests(unittest.TestCase):
    def test_the_saved_schema_is_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "baseline.json"
            save_baseline(path, report((case("sample"),)))
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema"], 1)
        self.assertEqual(
            payload["agent"]["base_url"], "https://api.example.com/v1"
        )
        self.assertIn("prompt_sha256", payload["judge"])
