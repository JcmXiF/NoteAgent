"""HTTP helpers for the notes a person writes about wiki pages.

Nothing here resolves a path: which file a note lives in follows from the page it
is about and its own name, both of which the core checks. A request says what to
write and about which page, never where it goes.
"""

from __future__ import annotations

from typing import Any

from noteagent.notes.store import (
    create_note,
    delete_note,
    list_notes,
    note_to_dict,
    read_note,
    update_note,
    wiki_page_path,
)
from noteagent.project.layout import ProjectLayout
from noteagent.util.refusal import Refusal


def list_notes_payload(
    layout: ProjectLayout,
    page: str | None = None,
) -> dict[str, Any]:
    """One page's notes, or every note when no page is named."""
    return {
        "page": page or "",
        "notes": [note_to_dict(note) for note in list_notes(layout, page)],
    }


def get_note_payload(layout: ProjectLayout, note_id: str) -> dict[str, Any]:
    return note_to_dict(read_note(layout, note_id))


def create_note_payload(layout: ProjectLayout, body: dict[str, Any]) -> dict[str, Any]:
    page = _require_page(layout, str(body.get("page") or ""))
    note = create_note(
        layout,
        page=page,
        body=str(body.get("body") or ""),
        anchor=str(body.get("anchor") or ""),
        quote=str(body.get("quote") or ""),
    )
    return note_to_dict(note)


def update_note_payload(
    layout: ProjectLayout,
    note_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Change a note. A field that is not given keeps what it had."""
    note = update_note(
        layout,
        note_id,
        body=body.get("body"),
        anchor=body.get("anchor"),
        quote=body.get("quote"),
    )
    return note_to_dict(note)


def delete_note_payload(layout: ProjectLayout, note_id: str) -> dict[str, Any]:
    return {"deleted": delete_note(layout, note_id)}


def _require_page(layout: ProjectLayout, page: str) -> str:
    """A note is written about a page that is there.

    Only when writing a new one: an existing note whose page has gone is left
    alone and stays readable, since finding out where it belongs is the reader's
    business rather than an error.
    """
    path = wiki_page_path(layout, page)
    if not path.is_file():
        raise Refusal("note.pageMissing", f"page not found: {page}", status=404)
    return page


__all__ = [
    "create_note_payload",
    "delete_note_payload",
    "get_note_payload",
    "list_notes_payload",
    "update_note_payload",
]
