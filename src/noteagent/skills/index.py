"""Render the ``skills`` system section the model reads.

The index discloses name, description and source only; a body is loaded by the
``skill`` tool on demand. It has a character budget: descriptions shrink
first, and whole entries drop last, so the section never crowds out the rest
of the prompt.
"""

from __future__ import annotations

from noteagent.skills import SkillRegistry, SkillRootKind
from noteagent.skills.types import SkillCatalogEntry

MAX_INDEX_CHARS = 4_000
_OPEN = "<available_skills>"
_CLOSE = "</available_skills>"


def render_index(
    registry: SkillRegistry, *, max_chars: int = MAX_INDEX_CHARS
) -> str:
    """The index section, or ``""`` when no skill fits the budget."""
    entries = registry.entries()
    if not entries or max_chars <= 0:
        return ""
    opening = f"{_OPEN}\n"
    closing = f"\n{_CLOSE}"
    budget = max_chars - len(opening) - len(closing)
    if budget <= 0:
        return ""
    kept = list(entries)
    while kept and _rendered_chars(kept, 0) > budget:
        kept.pop()
    if not kept:
        return ""
    longest = max(len(_description(entry)) for entry, _kind in kept)
    low, high = 0, longest
    while low < high:
        middle = (low + high + 1) // 2
        if _rendered_chars(kept, middle) <= budget:
            low = middle
        else:
            high = middle - 1
    lines = [
        _line(entry, kind, low) for entry, kind in kept
    ]
    return opening + "\n".join(lines) + closing


def _description(entry: SkillCatalogEntry) -> str:
    """One line, so a stray newline cannot break the list."""
    return " ".join(entry.description.split())


def _line(
    entry: SkillCatalogEntry, kind: SkillRootKind, limit: int
) -> str:
    description = _description(entry)
    if len(description) > limit:
        if limit <= 1:
            description = "…" if limit else ""
        else:
            description = description[: limit - 1].rstrip() + "…"
    return f"- {entry.name} [{kind}]: {description}"


def _rendered_chars(
    entries: list[tuple[SkillCatalogEntry, SkillRootKind]], limit: int
) -> int:
    """The rendered lines plus the newlines between them."""
    if not entries:
        return 0
    return (
        sum(len(_line(entry, kind, limit)) for entry, kind in entries)
        + len(entries)
        - 1
    )
