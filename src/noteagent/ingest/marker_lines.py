"""How a stamped marker line and the heading it owns are read.

``na-clip`` and ``na-ecg`` are the two anchors the pipeline writes into a body.
Both are HTML comments standing on a line of their own — out of band, so a
marker keeps its bytes wherever the prose around it is edited — and both own the
heading that follows them. Reading that rule from one place is what stops the
two readers, and the two languages, from drifting apart.

The rule, in full:

- A marker occupies its own line; leading and trailing spaces are fine.
- It owns the next line that has content in it. Blank lines may separate the
  two; nothing else may.
- That line has to be a section heading (``##`` through ``######``). ``# Title``
  is the page's own name, not a section, so it never owns a marker.
- A marker with no heading under it owns nothing. It is left on disk untouched
  and reported as unowned; stepping on to a later heading would attribute a
  section to material it was not written from.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: ``## Heading`` through ``###### Heading``, with a name on it.
HEADING = re.compile(r"^(#{2,6})[ \t]+(.+?)\s*$")

#: One ``na-clip`` marker and nothing else on the line.
CLIP_MARK_LINE = re.compile(
    r"[ \t]*<!--\s*na-clip:([^\s>]+)(?:\s+([bB]\d+(?:\s*,\s*[bB]\d+)*))?\s*-->[ \t]*",
    re.IGNORECASE,
)

#: The ``na-ecg`` marker and nothing else on the line.
ECG_MARK_LINE = re.compile(r"[ \t]*<!--\s*na-ecg\s*-->[ \t]*", re.IGNORECASE)

#: The marker ``ecg`` stamps, and the literal its line pattern matches.
ECG_MARK = "<!-- na-ecg -->"


def split_lines(text: str) -> list[str]:
    """Body text as lines, with one line ending throughout."""
    return text.replace("\r\n", "\n").split("\n")


def heading_text(line: str) -> str | None:
    """The section name *line* carries, or ``None`` when it is not a heading."""
    match = HEADING.match(line)
    if match is None:
        return None
    return (match.group(2) or "").strip() or None


def next_content_line(lines: Sequence[str], start: int) -> int | None:
    """Index of the first line at or after *start* that has visible text."""
    for index in range(start, len(lines)):
        if lines[index].strip():
            return index
    return None
