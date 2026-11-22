"""Write the frozen V3 quality set's manifest after a version bump."""

from __future__ import annotations

import argparse
from pathlib import Path

from evals.golden.dataset import DEFAULT_GOLDEN
from evals.quality.v3.set import (
    CASES_ROOT,
    MANIFEST_PATH,
    RUBRIC_PATH,
    write_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.quality.v3",
        description="Write the V3 quality set's sha256 manifest.",
    )
    parser.add_argument("--version", required=True)
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--cases", default=str(CASES_ROOT))
    parser.add_argument("--rubric", default=str(RUBRIC_PATH))
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path = write_manifest(
        Path(args.manifest),
        version=args.version,
        golden_root=Path(args.golden),
        cases_root=Path(args.cases),
        rubric_path=Path(args.rubric),
    )
    print(f"quality set manifest: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
