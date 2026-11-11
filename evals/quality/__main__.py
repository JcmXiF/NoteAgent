"""Run the quality evals against a live model, on this machine only.

The deterministic evals are the CI gate. This one calls a provider, so it
refuses to run when CI is set: a pull request must never be able to reach a
live model. The report and per-case artifacts land under
``build/evals/quality/runs/<UTC>/``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from evals.quality.cases import CaseError, load_cases
from evals.quality.report import (
    AgentInfo,
    CaseSummary,
    QualityReport,
    write_report,
)
from evals.quality.runner import run_case

CASES_DIR = Path(__file__).resolve().parent / "cases"
RUNS_DIR = Path("build") / "evals" / "quality" / "runs"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality",
        description=(
            "Run the local quality evals against the configured model. "
            "This calls a live provider; CI is refused."
        ),
    )
    parser.add_argument(
        "--case",
        default="",
        help="run only the case with this exact name",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even when CI or GITHUB_ACTIONS is set",
    )
    parser.add_argument(
        "--artifacts",
        default="",
        help="the run directory (default: build/evals/quality/runs/<UTC>)",
    )
    parser.add_argument(
        "--report",
        default="",
        help="where to write report.json (default: inside the run directory)",
    )
    return parser


def _flag(name: str) -> bool:
    """Whether an environment flag is set to a true value, not just present."""
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _in_ci() -> bool:
    return _flag("CI") or _flag("GITHUB_ACTIONS")


def main(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., CaseSummary] = run_case,
) -> int:
    args = _parser().parse_args(argv)
    if _in_ci() and not args.force:
        print(
            "quality eval calls a live provider; run it locally "
            "(CI is set; pass --force to override)",
            file=sys.stderr,
        )
        return 2

    try:
        from noteagent.llm.config import load_llm_config
        from noteagent.settings.install import install_view
        from noteagent.util.refusal import CodedError

        install_view()
        config = load_llm_config()
    except (CodedError, OSError) as err:
        print(f"quality eval needs a model connection: {err}", file=sys.stderr)
        return 2

    try:
        cases = load_cases(CASES_DIR)
    except CaseError as err:
        print(f"quality eval cases: {err}", file=sys.stderr)
        return 2

    if args.case:
        cases = tuple(case for case in cases if case.name == args.case)
        if not cases:
            print(f"no quality case named {args.case!r}", file=sys.stderr)
            return 2

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifacts = Path(args.artifacts) if args.artifacts else RUNS_DIR / run_id
    report_path = (
        Path(args.report) if args.report else artifacts / "report.json"
    )
    started_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    started = time.monotonic()

    results = []
    for spec in cases:
        result = runner(spec, config, artifacts)
        results.append(result)
        passed_checks = sum(1 for item in result.assertions if item.passed)
        verdict = "PASS" if result.passed else "FAIL"
        print(
            f"{verdict} {result.name} "
            f"({passed_checks}/{len(result.assertions)} checks, "
            f"{result.duration_s:.1f}s)",
            flush=True,
        )
        for item in result.assertions:
            status = "PASS" if item.passed else "FAIL"
            print(
                f"  {status} {item.name} "
                f"expected={item.expected!r} actual={item.actual!r}",
                flush=True,
            )
            if not item.passed and item.detail:
                print(f"       {item.detail}", flush=True)
        if result.error:
            last_line = result.error.strip().splitlines()[-1]
            print(f"  ! {last_line}", flush=True)

    report = QualityReport(
        started_at=started_at,
        duration_s=time.monotonic() - started,
        agent=AgentInfo(
            model=config.model,
            base_url=config.base_url,
            temperature=config.temperature,
        ),
        cases=tuple(results),
    )
    path = write_report(report, report_path)
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    print(
        f"\n{passed} passed, {failed} failed "
        f"in {report.duration_s:.1f}s",
        flush=True,
    )
    print(f"report: {path}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
