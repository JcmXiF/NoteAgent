"""Run the V4 quality eval, locally only.

The local atoms are deterministic; the live atoms and complex tasks arrive
later. Record-only: the command completes whether or not an atom passed, and
writes what it saw. A failed atom keeps its project and event logs under the
failure library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.quality.v4.atoms import ATOMS
from evals.quality.v4.runner import (
    AtomCase,
    RunAtom,
    run_atoms,
)
from evals.support import in_ci


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality.v4.run",
        description=(
            "Run the V4 quality eval's local atoms; local only, record-only."
        ),
    )
    parser.add_argument(
        "--filter",
        default="",
        help="run only the atoms whose name contains this text",
    )
    parser.add_argument(
        "--report",
        default="build/evals/quality/v4/report.json",
        help="where to write the JSON report (a .md goes beside it)",
    )
    parser.add_argument(
        "--failures",
        default="build/evals/quality/v4/failures",
        help=(
            "the failure library; a failed atom keeps its project and "
            "event logs here, and an older failure is not cleared"
        ),
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    atoms: tuple[AtomCase, ...] | None = None,
    run_atom: RunAtom | None = None,
    run_id: str | None = None,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if in_ci():
        print(
            "the V4 quality eval is local only; it does not run in CI",
            file=sys.stderr,
        )
        return 2
    selected = tuple(
        atom for atom in (atoms or ATOMS) if args.filter in atom.name
    )
    if not selected:
        print(
            f"no atom matches {args.filter!r}", file=sys.stderr
        )
        return 2
    report = run_atoms(
        selected,
        Path(args.failures),
        run_id=run_id,
        run_atom=run_atom,
    )
    for atom in report.atoms:
        verdict = "PASS" if atom.passed else "FAIL"
        print(
            f"{verdict} {atom.name} "
            f"({len(atom.checks) - len(atom.failed_checks)}/"
            f"{len(atom.checks)} checks, {atom.duration_s:.1f}s, "
            f"{atom.tokens_in}/{atom.tokens_out} tokens)",
            flush=True,
        )
        for check in atom.failed_checks:
            print(f"  - {check.name}: {check.detail}", flush=True)
        if atom.error:
            print(f"  ! {atom.error_line}", flush=True)
        if atom.artifacts:
            print(f"  artifacts: {atom.artifacts}", flush=True)
    path = report.write(Path(args.report))
    print(
        f"\n{report.passed_count} passed, {report.failed_count} failed "
        f"in {report.duration_s:.1f}s",
        flush=True,
    )
    print(f"report: {path}", flush=True)
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
