"""Run the deterministic behaviour evals.

``python -m evals`` runs every case, prints a report, writes it beside the
build output, and exits non-zero if any case failed. A failed case keeps its
temporary project and event logs under the artifacts folder, so the run can be
reproduced without guessing what the session looked like.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from evals.cases import CASES
from evals.harness import run_case
from evals.report import EvalReport, utc_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Run the deterministic agent-harness behaviour evals.",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="run only the cases whose name contains this text",
    )
    parser.add_argument(
        "--report",
        default="build/evals/report.json",
        help="where to write the JSON report (a .md summary goes beside it)",
    )
    parser.add_argument(
        "--artifacts",
        default="build/evals/artifacts",
        help="where a failed case's project and event logs are kept",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cases = [case for case in CASES if args.filter in case.name]
    if not cases:
        print(f"no eval case matches {args.filter!r}", file=sys.stderr)
        return 2

    started_at = utc_now()
    started = time.monotonic()
    artifacts = Path(args.artifacts)
    results = []
    for case in cases:
        result = run_case(case, artifacts)
        results.append(result)
        verdict = "PASS" if result.passed else "FAIL"
        print(
            f"{verdict} {result.name} "
            f"({len(result.passed_checks)}/{len(result.checks)} checks, "
            f"{result.duration_s:.1f}s)",
            flush=True,
        )
        for item in result.failed_checks:
            print(f"  - {item.name}: {item.detail}", flush=True)
        if result.error:
            print(f"  ! {result.error_line}", flush=True)

    report = EvalReport(
        tuple(results),
        started_at,
        time.monotonic() - started,
    )
    path = report.write(Path(args.report))
    print(
        f"\n{report.passed_count} passed, {report.failed_count} failed "
        f"in {report.duration_s:.1f}s",
        flush=True,
    )
    print(f"report: {path}", flush=True)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
