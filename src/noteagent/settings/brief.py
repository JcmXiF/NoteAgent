"""The project brief: what this wiki is for, in the author's words.

The model already reads ``config/brief.md``, falling back to a legacy file and
then to a built-in paragraph. The screen edits that same file. Empty text
removes it, so the fallback applies again; a legacy file is not deleted,
because throwing away an unmigrated copy is a migration decision.
"""

from __future__ import annotations

from typing import Any

from noteagent.io.atomic import write_text_atomic
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import DEFAULT_BRIEF, MAX_BRIEF_CHARS, read_optional
from noteagent.util.refusal import Refusal


def _own(layout: ProjectLayout) -> str:
    return read_optional(layout.brief, MAX_BRIEF_CHARS + 1).strip()


def _legacy(layout: ProjectLayout) -> str:
    return read_optional(layout.legacy_purpose, MAX_BRIEF_CHARS + 1).strip()


def brief_view(layout: ProjectLayout) -> dict[str, Any]:
    """The brief the screen edits, and where the words are coming from."""
    own = _own(layout)
    legacy = "" if own else _legacy(layout)
    if own:
        source = "project"
        text = own
    elif legacy:
        source = "legacy"
        text = legacy
    else:
        source = "default"
        text = ""
    return {
        "text": text,
        "source": source,
        "defaultText": DEFAULT_BRIEF.strip(),
        "maxChars": MAX_BRIEF_CHARS,
    }


def save_brief(layout: ProjectLayout, text: str) -> dict[str, Any]:
    """Write the project's own brief, or remove it when *text* is empty."""
    if "\x00" in text:
        raise Refusal("settings.invalid", "brief contains a null", params={"field": "brief"}, status=400)
    cleaned = text.replace("\r\n", "\n").strip()
    if len(cleaned) > MAX_BRIEF_CHARS:
        raise Refusal(
            "settings.briefTooLong",
            f"brief is longer than {MAX_BRIEF_CHARS} characters",
            params={"limit": str(MAX_BRIEF_CHARS)},
            status=400,
        )
    if not cleaned:
        if layout.brief.is_file():
            layout.brief.unlink()
        return brief_view(layout)
    layout.config.mkdir(parents=True, exist_ok=True)
    write_text_atomic(layout.brief, cleaned + "\n")
    return brief_view(layout)
