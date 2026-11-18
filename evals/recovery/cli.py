"""The ``python -m evals.recovery`` command: run the experiment locally.

This harness needs a real provider, a real server child and a real kill, so it
refuses to run under CI unless ``--force`` is passed. It is a measurement, not
a gate: the exit status reports harness health, not whether every metric hit
its target.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evals.recovery.dataset import SCENARIOS
from evals.recovery.report import counts as report_counts
from evals.recovery.report import write_report
from evals.recovery.runner import run_eval
from evals.recovery.metrics import compute_metrics

__all__ = ["main"]

DEFAULT_REPORT = "build/evals/recovery/report.json"
DEFAULT_ARTIFACTS = "build/evals/recovery/artifacts"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.recovery",
        description=(
            "Measure chat-run recovery and effect-journal rollback with a "
            "real provider, a real server child and real SIGKILL. "
            "Local only: CI runs are refused unless --force is given."
        ),
    )
    parser.add_argument(
        "--suite",
        choices=("all", "recovery", "rollback"),
        default="all",
        help="which suite to run (default: all)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=3,
        help="repetitions per scenario (default: 3)",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="only scenarios whose id contains this text",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"where to write JSON and Markdown (default: {DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--artifacts",
        default=DEFAULT_ARTIFACTS,
        help=f"where to keep each run's project and logs (default: {DEFAULT_ARTIFACTS})",
    )
    parser.add_argument(
        "--tool-delay-ms",
        type=float,
        default=300.0,
        help="delay each wiki tool handler by this many milliseconds",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even when CI/GITHUB_ACTIONS is set",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the scenarios and exit",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.id}  [{scenario.suite}]  {scenario.title}")
        return 0

    if (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")) and not args.force:
        print(
            "refusing to run the recovery eval under CI: it needs a real "
            "provider and kills processes. Pass --force to override.",
            file=sys.stderr,
        )
        return 2

    try:
        from noteagent.llm.config import load_llm_config
        from noteagent.settings.install import install_view

        install_view()
        config = load_llm_config()
    except Exception as error:  # noqa: BLE001 - setup failure is a refusal
        print(f"the recovery eval needs a model connection: {error}", file=sys.stderr)
        return 2

    suites = (
        ("recovery", "rollback") if args.suite == "all" else (args.suite,)
    )
    report = run_eval(
        suites=suites,
        reps=max(1, args.reps),
        filter_text=args.filter,
        artifacts=Path(args.artifacts),
        model=config.model,
        delay_ms=max(0.0, args.tool_delay_ms),
    )
    written = write_report(report, Path(args.report))
    metrics = compute_metrics(report.runs)
    counts = report_counts(report.runs)
    print(
        "recovery eval: "
        f"{counts['passed']} passed, {counts['failed']} failed, "
        f"{counts['missed']} missed, {counts['errors']} infra"
    )
    print(
        f"detection {_pct(metrics.detection.value)} | "
        f"false interrupts {_pct(metrics.false_interrupt.value)} | "
        f"preservation {_pct(metrics.preservation.value)} | "
        f"resume {_pct(metrics.resume_success.value)} | "
        f"overwrite {_pct(metrics.overwrite.value)} | "
        f"idempotency {_pct(metrics.idempotency.value)}"
    )
    print(f"report: {written}")
    return 1 if counts["errors"] else 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


if __name__ == "__main__":
    raise SystemExit(main())
