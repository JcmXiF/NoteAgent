"""What a ``[[wikilink]]`` is, where one counts, and how to write one back.

Reading and writing are the two sides of one rule, so the rule lives here and
``links`` builds the resolution on top of it. Kept in step with
``web/src/lib/wikilink.ts``, which compiles the same pattern string.

The rule, in full:

- A link is ``[[target]]`` or ``[[target|label]]``; the target runs to the first
  ``|`` and the label is what the page shows instead of it.
- ``![[…]]`` is an embed — it asks for a file's content, which nothing here can
  resolve — so it is not a link.
- A link never spans a line end. A ``[[`` with no ``]]`` after it is just text.
- Inside code — a fenced block or an inline backtick span — brackets are code. A
  ``[[x]]`` in an example is not a link to a page called ``x``.

A target cannot hold ``|``, ``]`` or a line end, because any of them would end
the link early and hand the reader a different target than the writer meant. A
label may hold ``|`` and ``[``; only ``]`` and a line end cut it short.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

#: What counts as a link. Both languages compile this same string.
WIKILINK_SOURCE = r"(?<!!)\[\[([^\]|\n]+)(?:\|([^\]\n]*))?\]\]"
WIKILINK = re.compile(WIKILINK_SOURCE)

#: A fence opens on its own line, with three or more backticks or tildes.
_FENCE_LINE = re.compile(r"^[ \t]*(`{3,}|~{3,})")

#: Characters that would end the link early, per position. A label may hold
#: ``|`` and ``[`` — only ``]`` and a line end cut it short.
_TARGET_FORBIDDEN = ("|", "]", "\n", "\r")
_LABEL_FORBIDDEN = ("]", "\n", "\r")


@dataclass(frozen=True, slots=True)
class WikiLink:
    """One ``[[…]]`` in the text, and the span it occupies."""

    target: str
    label: str | None
    start: int
    end: int

    @property
    def display(self) -> str:
        """What the link reads as: its label when it has one, else its target."""
        alias = (self.label or "").strip()
        return alias or self.target


def code_spans(text: str) -> tuple[tuple[int, int], ...]:
    """The regions a reader treats as code, as ``(start, end)`` offsets.

    Fences are taken first and swallow everything between them, so a backtick
    inside a fenced block does not open an inline span. A fence closes on a run
    of its own character at least as long as the one that opened it — that is
    what keeps a ```` ``` ````-delimited example inside a four-backtick block.
    An unclosed fence runs to the end of the text. Lines end at ``\\n``, the only
    line end a page is written with.
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    opened: tuple[str, int, int] | None = None
    for line in re.split(r"(?<=\n)", text):
        fence = _FENCE_LINE.match(line)
        if fence:
            run = fence.group(1)
            if opened is None:
                opened = (run[0], len(run), offset)
            elif run[0] == opened[0] and len(run) >= opened[1]:
                spans.append((opened[2], offset + len(line)))
                opened = None
        offset += len(line)
    if opened is not None:
        spans.append((opened[2], len(text)))
    spans.extend(_inline_spans(text, spans))
    return tuple(sorted(spans))


def _inline_spans(text: str, fenced: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Backtick spans outside the fences, each closed by a run of its own width."""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text[index] != "`" or _inside(index, fenced):
            index += 1
            continue
        width = _run_width(text, index)
        closed = _closing_run(text, index + width, width)
        if closed is None:
            index += width
            continue
        spans.append((index, closed))
        index = closed
    return spans


def _run_width(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index] == "`":
        index += 1
    return index - start


def _closing_run(text: str, start: int, width: int) -> int | None:
    """Index just past the run of exactly *width* backticks that closes a span."""
    index = start
    while index < len(text) and text[index] != "\n":
        if text[index] != "`":
            index += 1
            continue
        run = _run_width(text, index)
        if run == width:
            return index + run
        index += run
    return None


def _inside(index: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= index < end for start, end in spans)


def find_wikilinks(text: str) -> tuple[WikiLink, ...]:
    """Every link in *text*, in the order it appears, embeds and code aside."""
    body = text or ""
    skip = code_spans(body)
    found: list[WikiLink] = []
    for match in WIKILINK.finditer(body):
        if _inside(match.start(), skip):
            continue
        target = match.group(1).strip()
        if not target:
            continue
        label = match.group(2)
        found.append(
            WikiLink(
                target=target,
                label=label.strip() if label is not None else None,
                start=match.start(),
                end=match.end(),
            )
        )
    return tuple(found)


def format_wikilink(target: str, label: str | None = None) -> str:
    """Write a link in the form a reader of the same text will recognise.

    A label that repeats the target is dropped rather than written out, so the
    shortest form that still means the same thing is the one that reaches the
    page. Raises ``ValueError`` on a target or label holding a character that
    would end the link: writing a link that reads as a different target is worse
    than not writing one.
    """
    body = (target or "").strip()
    if not body:
        raise ValueError("a wikilink needs a target")
    _reject(body, "target", _TARGET_FORBIDDEN)
    alias = (label or "").strip()
    if alias:
        _reject(alias, "label", _LABEL_FORBIDDEN)
    if alias and alias != body:
        return f"[[{body}|{alias}]]"
    return f"[[{body}]]"


def _reject(text: str, what: str, forbidden: Sequence[str]) -> None:
    for char in forbidden:
        if char in text:
            shown = "newline" if char in "\n\r" else char
            raise ValueError(f"a wikilink {what} cannot contain {shown!r}: {text!r}")
