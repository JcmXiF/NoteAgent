"""Run the golden benchmark against the configured model, on this machine only.

The deterministic evals are the CI gate. This one calls a provider, so it
refuses to run when CI is set: a pull request must never be able to reach a
live model. Artifacts land under ``build/evals/golden/runs/<UTC>-<variant>/``;
one row per variant is appended to ``build/evals/golden/history.csv``.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import replace
from pathlib import Path

from evals.golden import manifest, report, runner
from evals.golden.dataset import DEFAULT_GOLDEN, GoldenError, load_golden

RUNS_DIR = Path("build") / "evals" / "golden" / "runs"
HISTORY_PATH = Path("build") / "evals" / "golden" / "history.csv"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.golden",
        description=(
            "Run the golden quality benchmark against the configured model. "
            "This calls a live provider; CI is refused."
        ),
    )
    parser.add_argument(
        "--golden",
        default=str(DEFAULT_GOLDEN),
        help="the local golden set (default: .scratch/agent-eval/golden)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--variant",
        choices=runner.VARIANTS,
        default=None,
        help="which prompt variant to run (default: base)",
    )
    mode.add_argument(
        "--ab",
        action="store_true",
        help="run base and strict, then report the paired comparison",
    )
    parser.add_argument(
        "--artifacts",
        default="",
        help="the run directory (default: build/evals/golden/runs/<UTC>-...)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="run even when CI or GITHUB_ACTIONS is set",
    )
    return parser


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _in_ci() -> bool:
    return _flag("CI") or _flag("GITHUB_ACTIONS")


def _progress(line: str) -> None:
    print(line, flush=True)


def _run_dir(name: str) -> Path:
    return RUNS_DIR / name


def _print_variant_summary(
    variant: str, payload: dict[str, object]
) -> None:
    totals = payload["counts"]
    means = payload["means"]
    assert isinstance(totals, dict) and isinstance(means, dict)
    print(
        f"\n{variant}: {totals['tracePassed']}/{totals['queries']} "
        f"trace passed, {totals['judgeErrors']} judge errors; "
        f"means {_means_text(means)}",
        flush=True,
    )


def _run_single(
    golden,
    config,
    root: Path,
    variant: str,
    history_path: Path,
) -> int:
    print(
        f"golden eval: {len(golden.queries)} queries, variant {variant}",
        flush=True,
    )
    run = runner.run_variant(
        golden, config, root, variant=variant, progress=_progress
    )
    record = runner.record_for(run, golden, config)
    manifest.write_manifest(root / "manifest.json", record)
    manifest.append_history(history_path, record)
    history = manifest.read_history(history_path)
    payload = report.write_variant_report(
        run,
        record,
        history,
        json_path=root / "report.json",
        html_path=root / "report.html",
    )
    _print_variant_summary(variant, payload)
    print(f"report:  {root / 'report.html'}", flush=True)
    print(f"history: {history_path}", flush=True)
    return 0 if all(query.passed for query in run.queries) else 1


def _run_ab(
    golden,
    config,
    root: Path,
    pair_id: str,
    history_path: Path,
) -> int:
    print(
        f"golden eval: {len(golden.queries)} queries, A/B base vs strict",
        flush=True,
    )
    base_dir = root / "base"
    strict_dir = root / "strict"
    base = runner.run_variant(
        golden, config, base_dir, variant="base", progress=_progress
    )
    base_record = runner.record_for(
        base, golden, config, pair_id=pair_id
    )
    manifest.write_manifest(base_dir / "manifest.json", base_record)
    manifest.append_history(history_path, base_record)

    strict = runner.run_variant(
        golden, config, strict_dir, variant="strict", progress=_progress
    )
    strict_record = runner.record_for(
        strict, golden, config, pair_id=pair_id
    )
    manifest.write_manifest(strict_dir / "manifest.json", strict_record)
    manifest.append_history(history_path, strict_record)

    history = manifest.read_history(history_path)
    base_payload = report.write_variant_report(
        base,
        base_record,
        history,
        json_path=base_dir / "report.json",
        html_path=base_dir / "report.html",
    )
    strict_payload = report.write_variant_report(
        strict,
        strict_record,
        history,
        json_path=strict_dir / "report.json",
        html_path=strict_dir / "report.html",
    )
    paired = runner.pair_variants(base, strict)
    manifest.write_ab_manifest(
        root / "manifest.json",
        [base_record, strict_record],
        pair_id=pair_id,
        started_at=base.started_at,
    )
    report.write_ab_report(
        base,
        strict,
        base_record,
        strict_record,
        paired,
        history,
        pair_id=pair_id,
        json_path=root / "report.json",
        html_path=root / "report.html",
    )

    _print_variant_summary("base", base_payload)
    _print_variant_summary("strict", strict_payload)
    print(
        f"\npaired: strict wins {paired.strict_wins}, "
        f"base wins {paired.base_wins}, ties {paired.ties}; "
        f"mean delta {paired.mean_delta:+.3f} (strict - base)",
        flush=True,
    )
    print(f"report:  {root / 'report.html'}", flush=True)
    print(f"history: {history_path}", flush=True)
    passed = all(query.passed for query in base.queries) and all(
        query.passed for query in strict.queries
    )
    return 0 if passed else 1


def _means_text(means: dict[str, object]) -> str:
    return ", ".join(
        f"{name[0]}={value if value is not None else '-'}"
        for name, value in means.items()
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if _in_ci() and not args.force:
        print(
            "golden eval calls a live provider; run it locally "
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
        print(f"golden eval needs a model connection: {err}", file=sys.stderr)
        return 2
    # The benchmark fixes the agent at temperature 0; the judge does the same.
    config = replace(config, temperature=0.0)

    try:
        golden = load_golden(Path(args.golden))
    except GoldenError as err:
        print(f"golden eval data: {err}", file=sys.stderr)
        return 2

    moment = manifest.utc_now()
    stamp = manifest.run_id_for(moment)
    if args.artifacts:
        root = Path(args.artifacts)
    elif args.ab:
        root = _run_dir(f"{stamp}-ab")
    else:
        root = _run_dir(f"{stamp}-{args.variant or 'base'}")

    try:
        if args.ab:
            return _run_ab(
                golden, config, root, stamp, HISTORY_PATH
            )
        return _run_single(
            golden,
            config,
            root,
            args.variant or "base",
            HISTORY_PATH,
        )
    except Exception as err:  # noqa: BLE001 - the CLI reports the failure
        traceback.print_exc()
        print(f"golden eval: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
