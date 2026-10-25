"""Writing project text as bytes, on one line ending.

A page is read and written by more than one writer, and a writer in text mode
translates ``\\n`` to the platform's ending, which is how one project ended up
carrying both kinds at once. The repository asks for LF (``.gitattributes`` and
``.editorconfig`` both say ``eol=lf``), so text is folded to LF here and written
as bytes: whatever the platform, the same page lands on disk.

The count returned by a write is the file's own, taken from the bytes that went
out rather than from a string, so nothing has to guess what translation happened.
"""

from __future__ import annotations

from pathlib import Path

from noteagent.io.atomic import write_text_atomic

__all__ = ["to_lf", "write_text"]


def to_lf(text: str) -> str:
    """The text as it will land on disk: one line ending and no byte-order mark."""
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def write_text(path: Path, text: str) -> int:
    """Write *text* as LF bytes, and say how many bytes reached disk.

    Replaced in one step rather than written in place: a page that is read by the
    interface while a compile writes it must be one version or the other, never a
    half-written third.
    """
    return write_text_atomic(path, to_lf(text))
