"""Run the V3 quality set against a live model, locally only.

CI never reaches a provider: the command refuses when CI or GITHUB_ACTIONS is
set. The report keeps Pass@k/Pass^k for the 24 single-turn items and each
multi-turn run beside it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from evals.quality.v3.runner import (
    K_DEFAULT,
    run_quality_set,
)
from evals.quality.v3.set import load_quality_set
from evals.support import in_ci


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality.v3.run",
        description=(
            "Run the frozen V3 quality set with a live model; local only."
        ),
    )
    parser.add_argument("--k", type=int, default=K_DEFAULT)
    parser.add_argument(
        "--report",
        default="build/evals/quality/v3/report.json",
        help="where to write the JSON report (a .md goes beside it)",
    )
    parser.add_argument(
        "--artifacts",
        default="build/evals/quality/v3/runs",
        help=(
            "where each item run keeps its evidence; a dedicated folder "
            "this command clears on every run"
        ),
    )
    parser.add_argument("--input-cost-per-mtok", type=float, default=None)
    parser.add_argument("--output-cost-per-mtok", type=float, default=None)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    quality: Any | None = None,
    run_item: Any | None = None,
    config: Any | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be at least 1")
    if in_ci():
        print(
            "the V3 quality eval calls a live provider; run it locally "
            "(CI is set)",
            file=sys.stderr,
        )
        return 2
    quality = quality or load_quality_set()
    if config is None:
        from noteagent.llm.config import load_llm_config
        from noteagent.settings.install import install_view

        install_view()
        config = load_llm_config()
    report = run_quality_set(
        quality,
        config,
        Path(args.artifacts),
        k=args.k,
        run_item=run_item,
        input_cost_per_mtok=args.input_cost_per_mtok,
        output_cost_per_mtok=args.output_cost_per_mtok,
    )
    path = report.write(Path(args.report))
    overall = report.scores.overall
    print(
        f"quality set {report.set_version}: "
        f"Pass@k {overall.pass_at_k.passed}/{overall.pass_at_k.total}, "
        f"Pass^k {overall.pass_hat_k.passed}/{overall.pass_hat_k.total}"
    )
    print(f"report: {path}")
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
