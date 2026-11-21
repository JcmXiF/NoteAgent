"""Run the writing quality A/B on this machine only.

The deterministic evals are the CI gate. This command calls a live provider,
so it refuses to run when CI is set; the key comes from the environment or the
configured connection and is never written into the repository.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from evals.quality.writing.cases import WritingCase, writing_cases
from evals.quality.writing.report import (
    WritingReport,
    compare,
    read_baseline,
    utc_now,
    verdict_text,
    write_baseline,
)
from evals.quality.writing.runner import CasePair, run_pair

from noteagent.llm.config import LlmConfig, LlmConfigError, load_llm_config
from noteagent.settings.install import install_view

RUNS_DIR = Path("build") / "evals" / "quality" / "writing" / "runs"
BASELINE_PATH = (
    Path("build") / "evals" / "quality" / "writing" / "baseline.json"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality.writing",
        description=(
            "Run the writing quality A/B against the configured model. "
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
        help="the run directory (default: under build/evals/quality/writing/runs)",
    )
    parser.add_argument(
        "--report",
        default="",
        help="where to write report.json (default: inside the run directory)",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="the baseline file (default: build/evals/quality/writing/baseline.json)",
    )
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="write today's run as the new baseline",
    )
    return parser


def _cases(selected: str) -> tuple[WritingCase, ...]:
    cases = writing_cases()
    if not selected:
        return cases
    chosen = tuple(case for case in cases if case.name == selected)
    if not chosen:
        raise SystemExit(f"no writing case named {selected!r}")
    return chosen


def _refuse_in_ci(force: bool) -> None:
    if force:
        return
    marker = os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
    if marker:
        raise SystemExit(
            "the writing quality eval calls a live model; it is local-only.\n"
            "Run it by hand, outside CI (or pass --force knowingly)."
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _refuse_in_ci(args.force)
    # The saved settings screen is the model source; without applying it this
    # command would say "not configured" on a machine that is configured.
    install_view()
    try:
        config: LlmConfig = load_llm_config()
    except LlmConfigError as err:
        print(f"the model is not configured: {err}", file=sys.stderr)
        return 2
    started_at = utc_now()
    stamp = started_at.replace(":", "").replace("-", "")[:15]
    run_dir = (
        Path(args.artifacts)
        if args.artifacts
        else RUNS_DIR / stamp
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        Path(args.report)
        if args.report
        else run_dir / "report.json"
    )
    baseline_path = (
        Path(args.baseline) if args.baseline else BASELINE_PATH
    )
    started = time.monotonic()
    pairs: list[CasePair] = []
    for case in _cases(args.case):
        print(f"running {case.name} (skill + plain)…", flush=True)
        pair = run_pair(
            case,
            artifacts=run_dir,
            config=config,
        )
        pairs.append(pair)
        print(
            f"  {verdict_text(pair.verdict)}: judge "
            f"{pair.skill.judge.score()}/4 vs "
            f"{pair.plain.judge.score()}/4",
            flush=True,
        )
    judge_prompt = (
        pairs[0].skill.judge.prompt_sha256 if pairs else ""
    )
    report = WritingReport(
        tuple(pairs),
        started_at,
        time.monotonic() - started,
        model=config.model,
        base_url=config.base_url,
        judge_model=config.model,
        judge_prompt_sha256=judge_prompt,
    )
    report.write(report_path)
    existing = read_baseline(baseline_path)
    if existing is None or args.save_baseline:
        write_baseline(baseline_path, report)
        print(
            f"baseline: {baseline_path} (written)", flush=True
        )
    else:
        for note in compare(report, existing):
            print(f"baseline: {note}", flush=True)
    print(report.markdown(), flush=True)
    print(f"report: {report_path}", flush=True)
    if not report.usable:
        return 1
    if not report.passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
