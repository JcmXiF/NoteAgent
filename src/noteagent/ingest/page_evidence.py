"""The passages a page says it was built from, resolved back to their words.

A merge is asked to produce one page from two pages' worth of prose, and until
now that was all it got: two accounts, neither of them the source. The page's
frontmatter already names the passages behind it, and those passages can still be
read — so the third text is available without changing what a page is or where
anything lives.

Only the page's *existing* locators are resolved. The material this write brought
arrives as prose that was written from its own source moments ago, so its
passages add nothing; the page on disk is the one whose account has had time to
drift.

The text is a check, not a new source of prose. The merge prompt says the same:
quoting a passage into the page would turn a wiki that says what a source holds
into one that pastes it.
"""

from __future__ import annotations

from noteagent.ingest.clip_ledger import split_source_locator
from noteagent.ingest.fingerprint import derived_block_texts
from noteagent.ingest.page_merge import page_sources, source_key
from noteagent.project.layout import ProjectLayout

#: The heading the prompt puts these under.
EVIDENCE_HEADING = "## The passages the page was built from"
#: Whole budget for the block. A merge prompt already carries two pages.
MAX_EVIDENCE_CHARS = 6_000
#: One passage, so a single long block cannot crowd out the others.
MAX_PASSAGE_CHARS = 1_200


def evidence_for_page(
    layout: ProjectLayout,
    page_text: str,
    *,
    max_chars: int = MAX_EVIDENCE_CHARS,
) -> str:
    """The page's own passages, as prompt text. Empty when none resolve.

    A locator that names no block is skipped: resolving the whole file would put
    an unread document in front of the model, which is the one thing the
    selection step exists to prevent. A source that no longer exists on disk is
    skipped for the same reason it cannot be cited.
    """
    locators = page_sources(page_text)
    if not locators:
        return ""

    # One extraction per source, however many of its blocks are cited.
    by_source: dict[str, dict[str, str] | None] = {}
    entries: list[str] = []
    seen: set[str] = set()
    spent = 0

    for locator in locators:
        key = source_key(locator)
        if not key or key in seen:
            continue
        seen.add(key)
        path, block_id = split_source_locator(locator)
        if not path or not block_id:
            continue
        if path not in by_source:
            by_source[path] = derived_block_texts(layout, path)
        table = by_source[path]
        if not table:
            continue
        passage = (table.get(block_id) or "").strip()
        if not passage:
            continue
        if len(passage) > MAX_PASSAGE_CHARS:
            passage = passage[:MAX_PASSAGE_CHARS].rstrip() + "…"
        entry = f"### `{locator}`\n\n{passage}\n"
        if spent + len(entry) > max_chars:
            break
        entries.append(entry)
        spent += len(entry)

    if not entries:
        return ""
    return "\n".join(
        [
            EVIDENCE_HEADING,
            "",
            "The page's own `sources` entries, resolved to the text they name. The",
            "page was written from these; what it says now may have drifted from",
            "them. Use them to check the page's account, not as text to copy.",
            "",
            *entries,
        ]
    )


def page_passages(layout: ProjectLayout):
    """A ``passages(page_text) -> str`` resolver for :func:`build_page_merger`."""

    def passages(page_text: str) -> str:
        return evidence_for_page(layout, page_text)

    return passages
