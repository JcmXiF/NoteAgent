"""Case D: report a gap and a provenance from the existing wiki.

The task asks whether rate-distortion and channel capacity exist, and where the
joint entropy definition came from. The answer may cite the five concept pages
and the three source ledgers; the judge sees them, so an absence claim and the
raw locator can be checked against them.
"""

from __future__ import annotations

from pathlib import Path

from evals.quality.v4.case_spec import load_answer_case

CASE_DIR = Path(__file__).resolve().parent / "cases" / "D"

CASE_D = load_answer_case(
    case_id="D",
    directory=CASE_DIR,
    samples=("D-defective",),
    pages=(
        ("concepts/信息熵", "信息熵", "信息熵.md"),
        ("concepts/联合熵", "联合熵", "联合熵.md"),
        ("concepts/条件熵", "条件熵", "条件熵.md"),
        ("concepts/互信息", "互信息", "互信息.md"),
        ("concepts/KL散度", "KL散度（相对熵）", "KL散度.md"),
        ("sources/信息熵-clips", "信息熵 clips", "信息熵-clips.md"),
        ("sources/联合熵-clips", "联合熵 clips", "联合熵-clips.md"),
        (
            "sources/kl散度相对熵-clips",
            "KL散度/相对熵 clips",
            "kl散度相对熵-clips.md",
        ),
    ),
    required_reads=(
        "concepts/联合熵",
        "sources/联合熵-clips",
    ),
)

__all__ = ["CASE_D", "CASE_DIR"]
