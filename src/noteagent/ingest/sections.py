"""Where the sections of a page body begin and end.

A section is read the way the rest of the app reads it: a heading (``##``
through ``######``) together with the stamped marker that owns it, running up to
the next such heading. Which marker owns a heading, and what the
gap between the two may hold, is the binding rule in
:mod:`noteagent.ingest.marker_lines`; what counts as code — where a line that
looks like a heading is a sample, not a boundary — is in
:mod:`noteagent.wiki.link_syntax`.

A ``na-clip`` marker names the material a section was written from, and it is
what puts the range where it is: a section a clip marker owns starts at that
marker's line rather than at its heading. No other line does that — an
``na-ecg`` marker, a comment that is not a marker, or a line of prose is a line
in the gap like any other, and the heading below it stands on its own.

Offsets are into the text exactly as it was handed in — line endings are not
folded and a missing opening frontmatter fence is not repaired — so a caller
holding the text read off disk can name the same stretch again.

**Offsets are character offsets, not byte offsets**, and the character is this
language's own: Python indexes ``str`` by code point. The TypeScript mirror
(:mod:`web/src/lib/sections`) indexes by UTF-16 code unit, so on text with a
character outside the basic plane the two produce different numbers for the same
section. That is fine because the numbers never cross the language boundary — the
write path sends the section's text and the core locates it again by itself. It
does mean a number is only meaningful next to the text it was measured on, and
that anything comparing the two sides has to compare the text they cut out, not
the offsets.

Two ranges never overlap and together they cover the text: the part before the
first heading (``preamble``) plus every section. That is the invariant the tests
check, because a gap or an overlap would let an edit move text nobody looked at.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from noteagent.ingest.marker_lines import CLIP_MARK_LINE, HEADING
from noteagent.wiki.link_syntax import code_spans

#: One line split into ``(start, content, content_end, next_start)``.
#:
#: ``content`` has no line ending, so a pattern written for a single line can
#: match it; ``content_end`` is where that content stops and ``next_start`` where
#: the following line begins, which differ only by the ending.
_Line = tuple[int, str, int, int]


@dataclass(frozen=True, slots=True)
class Section:
    """One ``##``–``######`` section, as offsets into the text read."""

    level: int
    heading: str
    #: First character of the boundary: the owning ``na-clip`` marker's line when
    #: one owns the section, and otherwise the heading's own line.
    start: int
    #: First character of the owning ``na-clip`` marker line, or ``None`` when the
    #: heading has none.
    marker_start: int | None
    heading_start: int
    #: End of the heading's text, before its line ending.
    heading_end: int
    #: First character after the heading's line — where the section's prose begins.
    body_start: int
    #: First character of the next boundary, or ``len(text)`` for the last section.
    end: int
    collection_id: str = ""
    block_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionedBody:
    """The whole body split into a preamble and its sections, in order."""

    #: ``(start, end)`` of everything before the first heading.
    preamble: tuple[int, int]
    sections: tuple[Section, ...]


def _split_lines(text: str) -> list[_Line]:
    """Lines with their offsets, keeping every line ending as it was."""
    lines: list[_Line] = []
    start = 0
    length = len(text)
    while start < length:
        newline = text.find("\n", start)
        if newline < 0:
            raw = text[start:]
            content = raw[:-1] if raw.endswith("\r") else raw
            lines.append((start, content, start + len(content), length))
            break
        raw = text[start:newline]
        content = raw[:-1] if raw.endswith("\r") else raw
        lines.append((start, content, start + len(content), newline + 1))
        start = newline + 1
    return lines


def _gap_above(lines: list[_Line], heading_index: int) -> int:
    """Walk up over the blank lines a marker may still bind across.

    A marker owns the heading below it when only blank lines stand between the
    two; a line of prose, a list or another marker ends the wait, so the earlier
    marker owns nothing.

    Returns the index of the line that ended the walk — the candidate owner, or
    ``-1``.
    """
    index = heading_index - 1
    while index >= 0 and lines[index][1].strip() == "":
        index -= 1
    return index


def _is_clip_marker(line: str) -> bool:
    """Whether *line* is the marker a boundary follows."""
    return CLIP_MARK_LINE.fullmatch(line) is not None


def _parse_blocks(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if value and value not in seen:
            seen.add(value)
            ids.append(value)
    return tuple(ids)


def _inside(offset: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= offset < end for start, end in spans)


def read_sections(text: str) -> SectionedBody:
    """Split *text* into a preamble and its sections, by exact offsets."""
    lines = _split_lines(text)
    code = code_spans(text)

    # One entry per heading: where the boundary starts, the heading's line index,
    # the na-clip marker's line index when one owns it (that is what moves the
    # boundary), and the heading match itself.
    boundaries: list[tuple[int, int, int | None, re.Match[str]]] = []
    for index, (start, content, _content_end, _next_start) in enumerate(lines):
        # A heading inside code — a fenced block or an inline backtick span — is a
        # sample, not a boundary: splitting on it would hand the write path a
        # section the page does not have.
        if _inside(start, code):
            continue
        heading_match = HEADING.match(content)
        if heading_match is None:
            continue
        above = _gap_above(lines, index)
        # Only a clip marker moves the boundary, and only from the line directly
        # above: the core pairs a section with itself by the text of the range, so
        # a range opening anywhere else would no longer start at the heading's
        # text.
        clip = above if above >= 0 and _is_clip_marker(lines[above][1]) else None
        boundaries.append(
            (
                lines[clip][0] if clip is not None else start,
                index,
                clip,
                heading_match,
            )
        )

    sections: list[Section] = []
    for position, (boundary_start, heading_index, marker, heading_match) in enumerate(
        boundaries
    ):
        is_last = position + 1 >= len(boundaries)
        next_start = boundaries[position + 1][0] if not is_last else len(text)
        heading_line = lines[heading_index]
        collection_id = ""
        block_ids: tuple[str, ...] = ()
        if marker is not None:
            marker_match = CLIP_MARK_LINE.fullmatch(lines[marker][1])
            if marker_match is not None:
                collection_id = (marker_match.group(1) or "").strip()
                block_ids = _parse_blocks(marker_match.group(2))
        sections.append(
            Section(
                level=len(heading_match.group(1)),
                heading=(heading_match.group(2) or "").strip(),
                start=boundary_start,
                marker_start=lines[marker][0] if marker is not None else None,
                heading_start=heading_line[0],
                heading_end=heading_line[2],
                body_start=heading_line[3],
                end=next_start,
                collection_id=collection_id,
                block_ids=block_ids,
            )
        )

    preamble = (0, boundaries[0][0] if boundaries else len(text))
    return SectionedBody(preamble=preamble, sections=tuple(sections))


__all__ = ["Section", "SectionedBody", "read_sections"]
