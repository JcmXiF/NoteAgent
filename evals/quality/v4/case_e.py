"""Case E: two raw sources plus the existing wiki become one relationship page.

The agent fetches the 31k mutual-information and 59k entropy originals through
the local fake web, reads the existing wiki pages, and writes a new page that
says what overlaps, what is new, what is missing and what contradicts. The
judge sees both originals, the new page and the relevant wiki pages.
"""

from __future__ import annotations

from pathlib import Path

from evals.quality.v4.case_spec import load_page_case

CASE_DIR = Path(__file__).resolve().parent / "cases" / "E"
WIKI_FILE = "wiki/互信息与熵.md"
MI_URL = "https://zh.wikipedia.org/wiki/互信息"
MI_TITLE = "互信息"
ENTROPY_URL = "https://zh.wikipedia.org/wiki/熵_(信息论)"
ENTROPY_TITLE = "熵（信息论）"

CASE_E = load_page_case(
    case_id="E",
    directory=CASE_DIR,
    wiki_file=WIKI_FILE,
    page_title="互信息与熵",
    samples=("E-good", "E-bad"),
    sources=(
        (MI_URL, MI_TITLE, "mutual-information.html"),
        (ENTROPY_URL, ENTROPY_TITLE, "entropy.html"),
    ),
    wiki_pages=(
        ("concepts/信息熵", "信息熵", "信息熵.md"),
        ("concepts/联合熵", "联合熵", "联合熵.md"),
        ("concepts/条件熵", "条件熵", "条件熵.md"),
        ("concepts/互信息", "互信息", "互信息.md"),
        ("concepts/KL散度", "KL散度（相对熵）", "KL散度.md"),
    ),
    required_reads=(
        "concepts/信息熵",
        "concepts/互信息",
        "concepts/联合熵",
    ),
    scope_note=(
        "The two raw sources above are the only raw originals allowed. "
        "The five concept pages above are the complete concept layer of "
        "the wiki; an item absent from them is a gap."
    ),
)

__all__ = [
    "CASE_E",
    "CASE_DIR",
    "ENTROPY_TITLE",
    "ENTROPY_URL",
    "MI_TITLE",
    "MI_URL",
    "WIKI_FILE",
]
