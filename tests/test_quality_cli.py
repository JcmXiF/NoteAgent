"""The quality eval command: the CI gate, the config, and the report."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fakes import ALL_PASS, FakeJudge

try:
    from evals.quality.__main__ import main
    from evals.quality.assertions import AssertionResult
    from evals.quality.calibrate import CalibrationEntry
    from evals.quality.report import CaseSummary
    from noteagent.llm.config import LlmConfig, LlmConfigError
except ImportError:  # pragma: no cover - CI installs the API extra
    main = None  # type: ignore[assignment]
    AssertionResult = None  # type: ignore[assignment]
    CalibrationEntry = None  # type: ignore[assignment]
    CaseSummary = None  # type: ignore[assignment]
    LlmConfig = None  # type: ignore[assignment]
    LlmConfigError = None  # type: ignore[assignment]


def run(
    argv: list[str],
    *,
    runner=None,
    judge_client=None,
    calibration=None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    kwargs = {}
    if runner is not None:
        kwargs["runner"] = runner
    if judge_client is not None:
        kwargs["judge_client"] = judge_client
    if calibration is not None:
        kwargs["calibration"] = calibration
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, **kwargs)
    return code, out.getvalue(), err.getvalue()


def passing_case() -> "CaseSummary":
    return CaseSummary(
        name="single-page-qa",
        title="单页事实问答",
        passed=True,
        duration_s=0.1,
        assertions=(
            AssertionResult("tools_any", True, ["wiki_read"], ["wiki_read"]),
        ),
    )


def fake_runner(spec, config, artifacts):
    return passing_case()


@unittest.skipIf(main is None, "fastapi not installed")
class CiGateTests(unittest.TestCase):
    def test_ci_is_refused_before_the_config_is_read(self) -> None:
        with patch.dict(os.environ, {"CI": "true"}, clear=True):
            code, _out, err = run([])

        self.assertEqual(code, 2)
        self.assertIn("run it locally", err)

    def test_github_actions_is_refused(self) -> None:
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
            code, _out, err = run([])

        self.assertEqual(code, 2)
        self.assertIn("run it locally", err)

    def test_force_overrides_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CI": "true"}, clear=True), patch(
                "noteagent.settings.install.install_view"
            ), patch(
                "noteagent.llm.config.load_llm_config",
                return_value=LlmConfig("https://api.example.com/v1", "k", "m"),
            ):
                code, _out, _err = run(
                    [
                        "--force",
                        "--artifacts",
                        tmp,
                        "--report",
                        str(Path(tmp) / "report.json"),
                    ],
                    runner=fake_runner,
                )

        self.assertEqual(code, 0)

    def test_ci_false_is_not_ci(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CI": "false"}, clear=True), patch(
                "noteagent.settings.install.install_view"
            ), patch(
                "noteagent.llm.config.load_llm_config",
                return_value=LlmConfig("https://api.example.com/v1", "k", "m"),
            ):
                code, _out, _err = run(
                    [
                        "--artifacts",
                        tmp,
                        "--report",
                        str(Path(tmp) / "report.json"),
                    ],
                    runner=fake_runner,
                )

        self.assertEqual(code, 0)


@unittest.skipIf(main is None, "fastapi not installed")
class ConfigAndSelectionTests(unittest.TestCase):
    def test_a_missing_connection_names_the_missing_setting(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "noteagent.settings.install.install_view"
        ), patch(
            "noteagent.llm.config.load_llm_config",
            side_effect=LlmConfigError(
                "Missing LLM config: NOTEAGENT_LLM_MODEL"
            ),
        ):
            code, _out, err = run([])

        self.assertEqual(code, 2)
        self.assertIn("NOTEAGENT_LLM_MODEL", err)

    def test_an_unknown_case_is_refused(self) -> None:
        with patch.dict(os.environ, {}, clear=True), patch(
            "noteagent.settings.install.install_view"
        ), patch(
            "noteagent.llm.config.load_llm_config",
            return_value=LlmConfig("https://api.example.com/v1", "k", "m"),
        ):
            code, _out, err = run(["--case", "nope"])

        self.assertEqual(code, 2)
        self.assertIn("nope", err)


@unittest.skipIf(main is None, "fastapi not installed")
class ReportWiringTests(unittest.TestCase):
    def test_a_passing_case_prints_each_assertion_and_writes_the_report(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            with patch.dict(os.environ, {}, clear=True), patch(
                "noteagent.settings.install.install_view"
            ), patch(
                "noteagent.llm.config.load_llm_config",
                return_value=LlmConfig("https://api.example.com/v1", "k", "m"),
            ):
                code, out, _err = run(
                    [
                        "--case",
                        "single-page-qa",
                        "--artifacts",
                        tmp,
                        "--report",
                        str(report),
                    ],
                    runner=fake_runner,
                )

            self.assertEqual(code, 0)
            self.assertIn("1 passed, 0 failed", out)
            self.assertIn("PASS tools_any", out)
            self.assertIn("expected=['wiki_read']", out)
            self.assertIn("judge: none", out)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["cases"][0]["name"], "single-page-qa")
            self.assertTrue(payload["cases"][0]["passed"])
            self.assertEqual(payload["judge"]["model"], "m")
            self.assertEqual(payload["judge"]["temperature"], 0.0)


@unittest.skipIf(main is None, "fastapi not installed")
class CalibrateWiringTests(unittest.TestCase):
    def test_calibrate_runs_the_judge_only_and_exits_zero(self) -> None:
        entry = CalibrationEntry(
            name="good",
            case="single-page-qa",
            answer="熵衡量不确定性；交叉熵等于熵加 KL 散度。",
            expected={
                "grounding": "pass",
                "completeness": "pass",
                "uncertainty": "pass",
            },
        )
        with patch.dict(os.environ, {}, clear=True), patch(
            "noteagent.settings.install.install_view"
        ), patch(
            "noteagent.llm.config.load_llm_config",
            return_value=LlmConfig("https://api.example.com/v1", "k", "m"),
        ):
            code, out, _err = run(
                ["--calibrate"],
                calibration=(entry,),
                judge_client=FakeJudge(ALL_PASS),
            )

        self.assertEqual(code, 0)
        self.assertIn("PASS good", out)
        self.assertIn("1 passed, 0 failed", out)
