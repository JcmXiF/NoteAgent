"""Case C: search the wiki, read three pages, answer their chain relation.

The answer may cite only the three allowed wiki pages; the judge sees those
pages, the expected points and the answer. There is no page to write and no
raw original to fetch.
"""

from __future__ import annotations

from pathlib import Path

from evals.quality.v4.case_spec import load_answer_case

CASE_DIR = Path(__file__).resolve().parent / "cases" / "C"

CASE_C = load_answer_case(
    case_id="C",
    directory=CASE_DIR,
    samples=("C-good", "C-bad"),
    pages=(
        ("concepts/信息熵", "信息熵", "信息熵.md"),
        ("concepts/联合熵", "联合熵", "联合熵.md"),
        ("concepts/条件熵", "条件熵", "条件熵.md"),
    ),
    required_reads=(
        "concepts/信息熵",
        "concepts/联合熵",
        "concepts/条件熵",
    ),
    scope_note=(
        "The three pages above are the allowed wiki material for this "
        "answer."
    ),
)

__all__ = ["CASE_C", "CASE_DIR"]
