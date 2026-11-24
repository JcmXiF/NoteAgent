"""Case A: a 17k raw source becomes a source-summary page.

The fixtures live beside this module; the generic runner in ``case_run.py``
drives the tools and the judge. The task covers information entropy, cross
entropy and KL divergence, from this one raw source only.
"""

from __future__ import annotations

from pathlib import Path

from evals.quality.v4.case_spec import load_page_case

CASE_DIR = Path(__file__).resolve().parent / "cases" / "A"
WIKI_FILE = "wiki/sources/什么是信息熵-交叉熵-相对熵.md"
SOURCE_URL = (
    "https://charlesliuyx.github.io/2018/09/24/"
    "什么是信息熵、交叉熵和相对熵/"
)
SOURCE_TITLE = "什么是信息熵、交叉熵和相对熵"

CASE_A = load_page_case(
    case_id="A",
    directory=CASE_DIR,
    wiki_file=WIKI_FILE,
    page_title=SOURCE_TITLE,
    samples=("A-good", "A-defective"),
    sources=((SOURCE_URL, SOURCE_TITLE, "source.html"),),
)

__all__ = [
    "CASE_A",
    "CASE_DIR",
    "SOURCE_TITLE",
    "SOURCE_URL",
    "WIKI_FILE",
]
