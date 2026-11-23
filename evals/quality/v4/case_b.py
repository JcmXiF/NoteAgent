"""Case B: a 17k raw source and a 31k raw source become one relationship page.

The task asks for the relationship between entropy/KL and mutual information,
so both originals must be cited and both may support a verdict. The judge sees
the two readable originals, the expected points and the new page.
"""

from __future__ import annotations

from pathlib import Path

from evals.quality.v4.case_spec import load_case

CASE_DIR = Path(__file__).resolve().parent / "cases" / "B"
WIKI_FILE = "wiki/熵-KL-互信息的关系.md"
ENTROPY_URL = (
    "https://charlesliuyx.github.io/2018/09/24/"
    "什么是信息熵、交叉熵和相对熵/"
)
ENTROPY_TITLE = "什么是信息熵、交叉熵和相对熵"
MI_URL = "https://zh.wikipedia.org/wiki/互信息"
MI_TITLE = "互信息"

CASE_B = load_case(
    case_id="B",
    directory=CASE_DIR,
    wiki_file=WIKI_FILE,
    page_title="熵、KL、互信息的关系",
    samples=("B-defective",),
    sources=(
        (ENTROPY_URL, ENTROPY_TITLE, "entropy.html"),
        (MI_URL, MI_TITLE, "mutual-information.html"),
    ),
)

__all__ = [
    "CASE_B",
    "CASE_DIR",
    "ENTROPY_TITLE",
    "ENTROPY_URL",
    "MI_TITLE",
    "MI_URL",
    "WIKI_FILE",
]
