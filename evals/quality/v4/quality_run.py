"""Run the V4 quality arm: judge calibration, then case A if asked.

Calibration is two judge calls on the fixed good/defective samples and costs
little. ``--case-a`` also runs the live agent on the 17k raw source, which
costs model tokens. Both are record-only: the report keeps the per-point
verdicts, and a failure keeps its project under the failure library.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from evals.quality.judge_client import JudgeClient, make_judge_client
from evals.quality.v4.calibration import (
    CalibrationReport,
    calibrate_case_a,
)
from evals.quality.v4.case_a import CaseAResult, run_case_a
from evals.quality.v4.runner import V4_REPORT_VERSION, new_run_id
from evals.report import markdown_cell
from evals.support import in_ci

from noteagent.llm.config import LlmConfig

CaseRunner = Callable[..., CaseAResult]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality.v4.quality_run",
        description=(
            "Calibrate the V4 judge on case A and, with --case-a, run the "
            "live case. Local only, record-only."
        ),
    )
    parser.add_argument(
        "--case-a",
        action="store_true",
        help="also run the live case A agent and judge its page",
    )
    parser.add_argument(
        "--report",
        default="build/evals/quality/v4/quality.json",
        help="where to write the JSON report (a .md goes beside it)",
    )
    parser.add_argument(
        "--failures",
        default="build/evals/quality/v4/failures",
        help="where a failed case A keeps its project and event logs",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    config: LlmConfig | None = None,
    judge_client: JudgeClient | None = None,
    case_runner: CaseRunner | None = None,
    run_id: str | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if in_ci():
        print(
            "the V4 quality arm calls a live model; run it locally "
            "(CI is set)",
            file=sys.stderr,
        )
        return 2
    if config is None:
        try:
            from noteagent.llm.config import load_llm_config
            from noteagent.settings.install import install_view

            install_view()
            config = load_llm_config()
        except Exception as err:  # noqa: BLE001 - the reason is the report
            print(
                f"the V4 quality arm needs a model connection: {err}",
                file=sys.stderr,
            )
            return 2
    judge = judge_client or make_judge_client(config)
    identifier = run_id or new_run_id()
    started = time.monotonic()
    calibration = calibrate_case_a(
        judge, secret=config.api_key
    )
    case: CaseAResult | None = None
    if args.case_a:
        runner = case_runner or run_case_a
        case = runner(
            config,
            failures_root=Path(args.failures),
            run_id=identifier,
            judge_client=judge,
        )
    duration = time.monotonic() - started
    report = _QualityReport(
        run_id=identifier,
        model=config.model,
        calibration=calibration,
        case=case,
        duration_s=duration,
    )
    path = report.write(Path(args.report))
    _print_calibration(calibration)
    if case is not None:
        _print_case(case)
    print(
        f"\nreport: {path}",
        flush=True,
    )
    return 0


@dataclass(frozen=True, slots=True)
class _QualityReport:
    run_id: str
    model: str
    calibration: CalibrationReport
    case: CaseAResult | None
    duration_s: float

    def payload(self) -> dict[str, object]:
        return {
            "version": V4_REPORT_VERSION,
            "setVersion": "v4",
            "kind": "v4-quality",
            "runId": self.run_id,
            "model": self.model,
            "durationSeconds": round(self.duration_s, 3),
            "calibration": self.calibration.payload(),
            "caseA": self.case.to_payload() if self.case else None,
        }

    def markdown(self) -> str:
        lines = [
            "# V4 quality arm",
            "",
            f"- Run: {self.run_id}",
            f"- Model: {self.model}",
            f"- Duration: {self.duration_s:.1f}s",
            "",
            self.calibration.markdown().strip(),
        ]
        if self.case is not None:
            lines.extend(
                [
                    "",
                    "# Case A",
                    "",
                    f"- Path: "
                    f"{'written' if self.case.artifact.strip() else 'missing'}",
                    f"- Tokens in/out: "
                    f"{self.case.tokens_in}/{self.case.tokens_out}",
                    "",
                    "| Check | Result | Detail |",
                    "|---|---|---|",
                ]
            )
            for check in self.case.checks:
                lines.append(
                    f"| {check.name} | "
                    f"{'PASS' if check.passed else 'FAIL'} | "
                    f"{markdown_cell(check.detail)} |"
                )
            lines.extend(
                [
                    "",
                    "| Point | Verdict | Reason |",
                    "|---|---|---|",
                ]
            )
            for point in self.case.verdict.points:
                lines.append(
                    f"| {point.id} | {point.verdict} | "
                    f"{markdown_cell(point.reason)} |"
                )
            if self.case.verdict.error:
                lines.extend(
                    [
                        "",
                        "## Judge error",
                        "",
                        "```",
                        self.case.verdict.error,
                        "```",
                    ]
                )
            if self.case.error:
                lines.extend(
                    ["", "```", self.case.error.strip().splitlines()[-1], "```"]
                )
            if self.case.artifacts:
                lines.append(f"- artifacts: {self.case.artifacts}")
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        return path


def _print_calibration(report: CalibrationReport) -> None:
    print("\nCalibration", flush=True)
    for sample in report.samples:
        verdict = "PASS" if sample.matched else "FAIL"
        points = len(sample.points) - len(sample.mismatches)
        print(
            f"{verdict} {sample.sample} "
            f"({points}/{len(sample.points)} points match)",
            flush=True,
        )
        for point in sample.mismatches:
            print(
                f"  - {point.point}: label {point.expected or '-'}, "
                f"judge {point.actual or '-'} ({point.reason})",
                flush=True,
            )
        if sample.verdict.error:
            print(f"  ! {sample.verdict.error}", flush=True)


def _print_case(case: CaseAResult) -> None:
    print("\nCase A", flush=True)
    verdict = "PASS" if case.passed else "FAIL"
    checks = len(case.checks) - len(case.failed_checks)
    points = sum(
        1 for point in case.verdict.points if point.verdict == "pass"
    )
    print(
        f"{verdict} case-A ({checks}/{len(case.checks)} checks, "
        f"{points}/{len(case.verdict.points)} points)",
        flush=True,
    )
    for check in case.failed_checks:
        print(f"  - {check.name}: {check.detail}", flush=True)
    for point in case.verdict.points:
        if point.verdict != "pass":
            print(
                f"  - {point.id}: {point.reason}", flush=True
            )
    if case.verdict.error:
        print(f"  ! {case.verdict.error}", flush=True)
    if case.error:
        print(
            f"  ! {case.error.strip().splitlines()[-1]}", flush=True
        )
    if case.artifacts:
        print(f"  artifacts: {case.artifacts}", flush=True)


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
