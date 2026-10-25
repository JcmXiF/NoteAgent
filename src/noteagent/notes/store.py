"""A note: what a person wrote about one wiki page, kept out of the wiki.

The wiki is written by the agent and read by a person; a note runs the other way.
It is a person's own writing *about* a page, so it is kept apart from the page --
top level ``notes/``, never inside ``wiki/`` -- and which page it is about is a
field of the note rather than a fact about where the file happens to sit. The
folder mirrors the wiki so someone browsing the project sees the same shape, but
nothing reads the location while the field is there: move a file by hand and the
note still knows its page.

A note whose body is empty is not written. That rule belongs to whoever watches
the writing, not here -- this module only refuses an empty body, so one note
cannot be half-written by two callers -- and a note that already sits on disk
with no body still reads, the way an editor opens an empty file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from secrets import token_hex

import yaml

from noteagent.ingest.frontmatter import format_scalar
from noteagent.io.paths import prune_empty_parents
from noteagent.io.text_file import write_text
from noteagent.project.layout import WIKI_DIR, ProjectLayout
from noteagent.util.refusal import Refusal

__all__ = [
    "Note",
    "create_note",
    "delete_note",
    "find_note_path",
    "list_notes",
    "mirror_dir",
    "note_id_new",
    "note_path",
    "note_to_dict",
    "normalize_page",
    "parse_note_text",
    "read_note",
    "render_note",
    "require_body",
    "update_note",
    "validate_note_id",
    "wiki_page_path",
]

WIKI_PREFIX = f"{WIKI_DIR}/"
_SUFFIX = ".md"
_ID = re.compile(r"\A\d{8}-\d{6}-[0-9a-z]+\Z")
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", re.DOTALL)

#: The order keys are written in, so two notes of the same age read alike.
_FIELDS = ("page", "anchor", "quote", "created", "updated")
_TIMESTAMP_FIELDS = frozenset({"created", "updated"})


@dataclass(frozen=True, slots=True)
class Note:
    """One note: what page it is about, where it goes, and what it says."""

    id: str
    page: str
    body: str
    anchor: str = ""
    quote: str = ""
    created: str = ""
    updated: str = ""


def note_to_dict(note: Note) -> dict[str, str]:
    """A note as the interface carries it."""
    return {
        "id": note.id,
        "page": note.page,
        "anchor": note.anchor,
        "quote": note.quote,
        "created": note.created,
        "updated": note.updated,
        "body": note.body,
    }


def note_id_new(now: datetime | None = None) -> str:
    """A note's name: the moment it was written, then a few random characters."""
    stamp = (now or _now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{token_hex(2)}"


def validate_note_id(note_id: str) -> str:
    """A note's name, checked because it becomes part of a file's path."""
    text = (note_id or "").strip()
    if not _ID.match(text):
        raise ValueError(f"not a note id: {note_id}")
    return text


def normalize_page(page: str) -> str:
    """The page a note is about, as a project-relative wiki path."""
    text = (page or "").replace("\\", "/").strip()
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"unsafe page: {page}")
    if parts[0].lower() != WIKI_DIR or not parts[-1].lower().endswith(_SUFFIX):
        raise Refusal("note.notOnPage", f"a note is about a wiki page: {page}")
    return "/".join([WIKI_DIR, *parts[1:]])


def wiki_page_path(layout: ProjectLayout, page: str) -> Path:
    """The wiki file a note is about, once the page name has been checked."""
    return layout.root.joinpath(*normalize_page(page).split("/"))


def mirror_dir(layout: ProjectLayout, page: str) -> Path:
    """Where a page's notes live: the wiki's own shape, without the wiki root."""
    relative = normalize_page(page)[len(WIKI_PREFIX) :]
    return layout.notes.joinpath(*Path(relative).with_suffix("").parts)


def note_path(layout: ProjectLayout, page: str, note_id: str) -> Path:
    """The file one note lives in, given the page it is about."""
    return mirror_dir(layout, page) / f"{validate_note_id(note_id)}{_SUFFIX}"


def require_body(body: str) -> str:
    """A note with nothing in it is not written.

    Whether a body that was cleared means the note is gone is decided by whoever
    watches the writing -- that rule is about the person's intent, and this
    module only knows text.
    """
    if not (body or "").strip():
        raise Refusal("note.empty", "a note has no content")
    return body


def render_note(note: Note) -> str:
    """A note as it is written to disk."""
    values = {
        "page": note.page,
        "anchor": note.anchor,
        "quote": note.quote,
        "created": note.created,
        "updated": note.updated,
    }
    lines = ["---"]
    lines.extend(f"{key}: {_render_value(key, values[key])}" for key in _FIELDS)
    lines.append("---")
    text = "\n".join(lines) + "\n"
    if note.body:
        text += "\n" + (note.body if note.body.endswith("\n") else note.body + "\n")
    return text


def parse_note_text(text: str) -> tuple[dict[str, str], str]:
    """A note's frontmatter fields and its body, as the file gives them."""
    folded = text.replace("\r\n", "\n").replace("\r", "\n")
    match = _FRONTMATTER.match(folded)
    if match is None:
        return {}, folded
    body = match.group(2)
    if body.startswith("\n"):
        body = body[1:]
    return _load_fields(match.group(1)), body


def read_note(layout: ProjectLayout, note_id: str) -> Note:
    """The note with *note_id*, wherever in the project it sits."""
    path = find_note_path(layout, note_id)
    if path is None:
        raise Refusal("note.missing", f"note not found: {note_id}", status=404)
    return _note_at(layout, path)


def list_notes(layout: ProjectLayout, page: str | None = None) -> list[Note]:
    """A page's notes, or every note in the project, in the order they were made."""
    if page is None:
        root = layout.notes
        paths = sorted(root.rglob(f"*{_SUFFIX}")) if root.is_dir() else []
    else:
        # Not a walk: a page's folder also holds the folders of pages under it.
        paths = _notes_directly_in(mirror_dir(layout, page))
    notes: list[Note] = []
    for path in paths:
        try:
            notes.append(_note_at(layout, path))
        except OSError:
            continue
    # Two notes can land in the same second, so the name breaks those ties.
    notes.sort(key=lambda note: (note.created, note.id))
    return notes


def find_note_path(layout: ProjectLayout, note_id: str) -> Path | None:
    """The file a note lives in, found by its name, or None when there is none."""
    # Checked before anything else: a name that could never be a note is the
    # caller's mistake, and saying "not found" would hide it behind a 404.
    wanted = f"{validate_note_id(note_id)}{_SUFFIX}"
    root = layout.notes
    if not root.is_dir():
        return None
    for path in sorted(root.rglob(wanted)):
        if path.is_file():
            return path
    return None


def create_note(
    layout: ProjectLayout,
    *,
    page: str,
    body: str,
    anchor: str = "",
    quote: str = "",
    note_id: str | None = None,
    now: datetime | None = None,
) -> Note:
    """Write a new note about *page*.

    *note_id* is for callers that must know the name beforehand, such as tests;
    the service never lets a caller choose one.
    """
    moment = now or _now()
    stamp = _stamp(moment)
    note = Note(
        id=validate_note_id(note_id) if note_id else note_id_new(moment),
        page=normalize_page(page),
        body=require_body(body),
        anchor=anchor or "",
        quote=quote or "",
        created=stamp,
        updated=stamp,
    )
    path = note_path(layout, note.page, note.id)
    if path.exists():
        raise Refusal("note.duplicateName", f"a note is already named {note.id}")
    _write(path, render_note(note))
    return note


def update_note(
    layout: ProjectLayout,
    note_id: str,
    *,
    body: str | None = None,
    anchor: str | None = None,
    quote: str | None = None,
    now: datetime | None = None,
) -> Note:
    """Change what a note says or where it goes; its page is not its to change."""
    path = find_note_path(layout, note_id)
    if path is None:
        raise Refusal("note.missing", f"note not found: {note_id}", status=404)
    current = _note_at(layout, path)
    note = replace(
        current,
        body=current.body if body is None else require_body(body),
        anchor=current.anchor if anchor is None else anchor,
        quote=current.quote if quote is None else quote,
        updated=_stamp(now or _now()),
    )
    # Written back where it was read, not to the folder its page names: a file
    # someone moved by hand would otherwise be left behind and written twice.
    _write(path, render_note(note))
    return note


def delete_note(layout: ProjectLayout, note_id: str) -> str:
    """Remove a note, and the folder that only existed to hold it."""
    path = find_note_path(layout, note_id)
    if path is None:
        raise Refusal("note.missing", f"note not found: {note_id}", status=404)
    path.unlink()
    prune_empty_parents(path.parent, layout.notes)
    return path.stem


def _notes_directly_in(folder: Path) -> list[Path]:
    """The note files in *folder* itself, not in the folders under it."""
    if not folder.is_dir():
        return []
    return sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() == _SUFFIX
        ),
        key=lambda path: path.name,
    )


def _note_at(layout: ProjectLayout, path: Path) -> Note:
    """The note a file holds, taking its page from the file or from its folder."""
    fields, body = parse_note_text(path.read_text(encoding="utf-8"))
    return Note(
        id=path.stem,
        page=fields.get("page") or _page_from_location(layout, path),
        body=body,
        anchor=fields.get("anchor", ""),
        quote=fields.get("quote", ""),
        created=fields.get("created", ""),
        updated=fields.get("updated", ""),
    )


def _page_from_location(layout: ProjectLayout, path: Path) -> str:
    """The page a note claims by sitting where it sits -- a fallback, not proof."""
    try:
        relative = path.relative_to(layout.notes)
    except ValueError:
        return ""
    folders = relative.parent.parts
    if not folders:
        return ""
    return WIKI_PREFIX + "/".join(folders) + _SUFFIX


def _render_value(key: str, value: str) -> str:
    # A timestamp is always quoted: written bare, YAML reads it back as a date
    # and the time of day is lost on the next read.
    if key in _TIMESTAMP_FIELDS:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return format_scalar(value)


def _load_fields(raw: str) -> dict[str, str]:
    """Frontmatter fields as text; unreadable YAML reads as no fields."""
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): _as_text(value) for key, value in loaded.items()}


def _as_text(value: object) -> str:
    """A YAML value as the text a note carries."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        # Keep the time of day: a written note has a moment, not just a date.
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write(path: Path, text: str) -> None:
    """Write a note, creating the folders its page names on the way."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)


def _now() -> datetime:
    """The moment a note is written, with the machine's offset."""
    return datetime.now().astimezone()


def _stamp(moment: datetime) -> str:
    """A timestamp written to the microsecond.

    Two notes written back to back share a second, and the order they were made
    in is the order a reader expects to find them.
    """
    return moment.isoformat(timespec="microseconds")
