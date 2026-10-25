"""How much of the project a prompt may carry, and what a missing file reads as."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from noteagent.project.layout import RAW_DIR
from noteagent.project.raw_kinds import raw_kind

if TYPE_CHECKING:
    from noteagent.project.layout import ProjectLayout

#: Character caps on injected context, so one long source cannot fill the window.
MAX_BRIEF_CHARS = 6_000
MAX_INDEX_CHARS = 12_000
MAX_SOURCE_CHARS = 80_000
#: Cap on the analysis text handed to the generation pass.
MAX_ANALYSIS_CHARS = 40_000

_TRUNCATED = "\n\n…(truncated)…"

#: What a project without a brief is told about itself. Deliberately short: it
#: describes the writing, not the program's rules, which the code enforces.
DEFAULT_BRIEF = """\
# Project brief

This wiki is a working set of notes built from sources the author chose to keep.
Aim for notes that answer a question, not summaries of a document.

- One subject per page, under the name a reader would search for.
- Say what a claim rests on; a claim the sources do not support stays out.
- Link with [[slug]] where the reader would want to follow it, not to fill a list.
- Keep the source's own language and notation.
"""


def read_optional(path: Path, limit: int) -> str:
    """Read a file if it exists, truncating to *limit* characters.

    Missing files become empty strings so prompt assembly can skip them.
    """
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATED


def truncate(text: str, limit: int) -> str:
    """Truncate an in-memory string to *limit* characters."""
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATED


def read_brief(layout: ProjectLayout, limit: int = MAX_BRIEF_CHARS) -> str:
    """The project's own description, or the built-in default.

    `config/brief.md` wins; a project that predates it still gets its `purpose.md`
    read, so an upgrade does not change what the model is told. `schema.md` is
    ignored: it described folders the code enforces, and drifted from them.
    """
    for path in (layout.brief, layout.legacy_purpose):
        text = read_optional(path, limit)
        if text:
            return text
    return truncate(DEFAULT_BRIEF.strip(), limit)


def folder_context_from_identity(identity: str) -> str:
    """The folders a source was filed under, beyond its kind.

    A new source sits in ``raw/<kind>/``, and the kind is already part of the
    identity, so it adds nothing. A project that filed sources deeper — a legacy
    host folder under ``raw/sources/web/``, say — still says where it came from,
    and that is worth passing on.
    """
    text = identity.replace("\\", "/").strip()
    if not text.startswith(f"{RAW_DIR}/"):
        return ""
    below = text[len(RAW_DIR) + 1 :].rsplit("/", 1)[0]
    segments = below.split("/")
    kind = raw_kind(text)
    if kind:
        lowered = [segment.lower() for segment in segments]
        if kind not in lowered:
            return ""
        segments = segments[lowered.index(kind) + 1 :]
    return " > ".join(segment for segment in segments if segment)
