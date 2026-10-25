"""Read the ``---FILE:`` blocks one generation answer asks to write.

A generation answer is a stream of whole pages, each fenced by its own pair of
lines::

    ---FILE: wiki/<slug>.md---
    <the page>
    ---END FILE---

What this module has to get right is small, but each part of it has cost a page
before:

- An opener is a line on its own: ``---FILE:``, a path, optional trailing dashes.
  A line that only mentions an opener inside prose is not one.
- A closer is a line on its own too, and only closes while no code sample is
  open. A page whose body shows a ``---END FILE---`` inside a fence must keep it.
- A block that never closes was cut off with the stream. It is dropped: half a
  page is not a page. The next block found after it is kept, because one broken
  block must not cost the answers around it.
- A path has to be writable: under ``wiki/``, no absolute or drive prefix, no
  ``..``, and a name Windows can create.
- Every block that is dropped is reported as a warning with its reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A line that opens a block. The trailing dashes are optional, so a model that
#: puts its own ``---`` frontmatter fence on the next line still parses.
OPENER = re.compile(r"^---\s*FILE:\s*(.*?)\s*(?:---)?\s*$", re.IGNORECASE)

#: A line that closes a block.
CLOSER = re.compile(r"^---\s*END\s+FILE\s*---\s*$", re.IGNORECASE)

#: A CommonMark code fence: up to three spaces, then three or more backticks or
#: tildes. Only its first character and length matter, for matching the closer.
FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")

#: Device names Windows reserves. They cannot name a file or a folder, with or
#: without an extension.
RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

#: Windows forbids these anywhere in a file name, and a name may not end in a
#: dot or a space.
_ILLEGAL_IN_NAME = re.compile(r'[<>:"|?*]')
_TRAILING_DOT_OR_SPACE = re.compile(r"[ .]$")
_DRIVE_PREFIX = re.compile(r"^[a-zA-Z]:")


@dataclass(frozen=True, slots=True)
class FileBlock:
    """One page a generation asked to write."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class ParseFileBlocksResult:
    """The blocks that may be written, and why the others were dropped."""

    blocks: tuple[FileBlock, ...]
    warnings: tuple[str, ...]


def is_safe_ingest_path(path: str) -> bool:
    """Whether *path* is a non-empty, Windows-safe page path under ``wiki/``."""
    if not isinstance(path, str) or not path.strip():
        return False
    if any(ord(ch) < 32 for ch in path):
        return False
    if path.startswith(("/", "\\")):
        return False
    if _DRIVE_PREFIX.match(path):
        return False
    segments = path.replace("\\", "/").split("/")
    if ".." in segments:
        return False
    if not all(_is_writable_name(segment) for segment in segments):
        return False
    return path.replace("\\", "/").startswith("wiki/")


def _is_writable_name(segment: str) -> bool:
    """Whether one path segment is a name Windows can create."""
    if not segment:
        return False
    if _ILLEGAL_IN_NAME.search(segment):
        return False
    if _TRAILING_DOT_OR_SPACE.search(segment):
        return False
    return segment.split(".", 1)[0].upper() not in RESERVED_NAMES


class _SampleTracker:
    """Whether the line being read is inside a fenced code sample.

    A fence toggles on any run of three or more backticks or tildes. A fence only
    closes on the same character and at least the same length, which is what
    CommonMark requires and what keeps a nested sample inside a longer fence.
    """

    def __init__(self) -> None:
        self._char: str | None = None
        self._length = 0

    @property
    def open(self) -> bool:
        return self._char is not None

    def see(self, line: str) -> None:
        match = FENCE.match(line)
        if match is None:
            return
        run = match.group(1)
        char, length = run[0], len(run)
        if self._char is None:
            self._char, self._length = char, length
        elif char == self._char and length >= self._length:
            self._char, self._length = None, 0


def parse_file_blocks(text: str) -> ParseFileBlocksResult:
    """Extract every closed, safe ``---FILE:`` block from *text*.

    Line endings are normalised first. The walk is linear: an opener starts a
    block, lines are collected until a closer outside a code sample, and the
    collected text becomes the page. See the module docstring for what is
    dropped and why.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: list[FileBlock] = []
    warnings: list[str] = []

    position = 0
    while position < len(lines):
        opener = OPENER.match(lines[position])
        if opener is None:
            position += 1
            continue

        path = opener.group(1).strip()
        position += 1
        page: list[str] = []
        sample = _SampleTracker()
        closed = False

        while position < len(lines):
            line = lines[position]
            inside = sample.open
            sample.see(line)
            if not inside and not sample.open:
                if CLOSER.match(line):
                    closed = True
                    position += 1
                    break
                if OPENER.match(line):
                    # The next page starts here, so this block was cut off. It is
                    # dropped below; the line is read again as a new block.
                    break
            page.append(line)
            position += 1

        if not closed:
            warnings.append(
                f'block for "{path or "an unnamed page"}" never closed, so it was '
                "dropped (a block ends at ---END FILE---; without one the page "
                "was cut off)"
            )
            continue
        if not path:
            warnings.append(
                "a block named no path and was dropped "
                '(the opener said only "---FILE:")'
            )
            continue
        if not is_safe_ingest_path(path):
            warnings.append(
                f'block "{path}" was refused: a page is a Windows-safe path '
                "under wiki/, with no .. and no drive or leading slash"
            )
            continue

        blocks.append(FileBlock(path=path, content="\n".join(page)))

    return ParseFileBlocksResult(blocks=tuple(blocks), warnings=tuple(warnings))


def format_parse_result(result: ParseFileBlocksResult) -> str:
    """Human-readable summary for CLI output."""
    parts: list[str] = [
        f"blocks: {len(result.blocks)}",
        f"warnings: {len(result.warnings)}",
    ]
    for index, block in enumerate(result.blocks, start=1):
        parts.append("")
        parts.append(f"--- block {index} ---")
        parts.append(f"path: {block.path}")
        parts.append(f"chars: {len(block.content)}")
        line_count = 0 if not block.content else len(block.content.splitlines())
        parts.append(f"lines: {line_count}")
        preview = block.content
        if len(preview) > 400:
            preview = preview[:400] + "\n…(truncated)…"
        parts.append("content:")
        parts.append(preview)
    if result.warnings:
        parts.append("")
        parts.append("--- warnings ---")
        for warning in result.warnings:
            parts.append(f"- {warning}")
    return "\n".join(parts)
