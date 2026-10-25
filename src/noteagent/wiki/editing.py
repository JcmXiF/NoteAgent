"""Changing a page: its prose, its name, and the versions it has been.

The wiki is written by the pipeline, but it is not only the pipeline's: a reader
who spots a wrong sentence should be able to fix it without opening a terminal,
and a page under a name that no longer fits should be able to move. What keeps
that safe is the part a person is not offered:

* the frontmatter is the program's. It carries the page's sources and the markers
  a citation resolves through, and a person editing prose should not have to keep
  YAML valid. Only the body is theirs, and only the body is replaced.
* a rename moves the page and rewrites every link that named it, because a link
  is how the rest of the wiki refers to the page. A rename that left the links
  behind would look successful and break the graph.
* a delete is a move into the trash. What a page was is often the only record of
  what a source said.

Every write through here is a version: the text it replaced is kept first, so
:mod:`noteagent.ingest.page_history` can offer it back.
"""

from __future__ import annotations

import difflib
import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from noteagent.ingest.frontmatter import (
    parse_page,
    replace_page_body,
    set_frontmatter_scalar,
    unquote_scalar,
)
from noteagent.ingest.page_history import (
    blob_digest,
    blob_path,
    keep_before,
    page_snapshots,
    record_write,
    rename_entries,
)
from noteagent.ingest.update_index import iter_wiki_content_pages, rebuild_wiki_index
from noteagent.io.paths import prune_empty_parents
from noteagent.io.text_file import to_lf, write_text
from noteagent.notes.store import (
    find_note_path,
    list_notes,
    note_path,
    render_note,
)
from noteagent.project.layout import (
    WIKI_DIR,
    WIKI_INDEX,
    WIKI_LOG,
    WIKI_OVERVIEW,
    ProjectLayout,
)
from noteagent.util.refusal import Refusal
from noteagent.wiki.link_syntax import find_wikilinks, format_wikilink
from noteagent.wiki.links import (
    is_wiki_content_page,
    normalize_wiki_rel,
    parse_wikilink_targets,
    resolve_wikilink,
    wiki_slug_for_path,
    wiki_slug_maps,
)

__all__ = [
    "WRITTEN_BY_PERSON",
    "page_backlinks",
    "page_edit_view",
    "page_versions",
    "read_version",
    "rename_page",
    "restore_version",
    "save_page",
]

#: Who a write through this module came from. The pipeline writes as ``agent``;
#: a version's ``by`` is what tells the two apart when a page is read back.
WRITTEN_BY_PERSON = "person"

#: Files the program maintains. Their bodies are generated from the pages on
#: disk, so editing one by hand would be undone by the next write.
_PROTECTED_PAGES = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})

#: How much of a diff is handed to the interface. A page can be rewritten whole,
#: and a dialog is not a place to scroll through a thousand lines.
MAX_DIFF_LINES = 400
_DIFF_CONTEXT = 3


def page_edit_view(layout: ProjectLayout, relative: str) -> dict[str, object]:
    """A page as something to edit: its prose, the part that is not, and a stamp."""
    path, rel = _page_path(layout, relative)
    text = _read(path)
    page = parse_page(text)
    title = _title_of(page.fields, rel)
    backlinks = page_backlinks(layout, rel)
    return {
        "path": rel,
        "title": title,
        "body": page.body,
        # The frontmatter is shown as it stands, so the reader can see what the
        # page is made of, but it is sent back untouched by every save.
        "frontmatter": page.prefix,
        "revision": blob_digest(text),
        "backlinkCount": backlinks["count"],
        "noteCount": backlinks["notes"],
    }


def save_page(
    layout: ProjectLayout,
    relative: str,
    body: str,
    *,
    base_revision: str = "",
    preview: bool = False,
    now: datetime | None = None,
) -> dict[str, object]:
    """Replace a page's prose, or say what replacing it would change.

    *base_revision* is the revision the editor opened. When the file has changed
    since, the save is refused rather than applied: the compile that wrote it may
    have had a reason, and silently overwriting it would lose that reason.
    """
    path, rel = _page_path(layout, relative)
    text = _read(path)
    page = parse_page(text)
    wanted = to_lf(body)
    if not page.has_frontmatter and wanted.lstrip().startswith("---"):
        # Without a frontmatter block of its own, a body that opens with a fence
        # would become the page's metadata the next time it is read.
        raise Refusal(
            "page.bodyStartsFence",
            f"the body would read as frontmatter: {rel}",
            params={"path": rel},
            status=400,
        )

    current = blob_digest(text)
    if not preview and base_revision and base_revision != current:
        raise Refusal(
            "page.changed",
            f"the page changed since it was opened: {rel}",
            params={"path": rel},
            status=409,
        )

    next_text = replace_page_body(text, wanted)
    # A save that changes nothing is not a version, and its stamp must not move:
    # the date says when the page last said something different.
    if next_text != text and "updated" in page.fields:
        stamp = (now or datetime.now()).date().isoformat()
        next_text = set_frontmatter_scalar(next_text, "updated", stamp)

    changed = next_text != text
    added, removed = _diff_counts(page.body, parse_page(next_text).body)
    payload: dict[str, object] = {
        "path": rel,
        "changed": changed,
        "diff": _diff(page.body, parse_page(next_text).body),
        "added": added,
        "removed": removed,
        "revision": blob_digest(next_text),
    }
    if preview or not changed:
        return payload

    keep_before(layout, text)
    write_text(path, next_text)
    record_write(
        layout,
        rel,
        before=current,
        after_text=next_text,
        mode="edit",
        by=WRITTEN_BY_PERSON,
    )
    return payload


def rename_page(layout: ProjectLayout, relative: str, to: str) -> dict[str, object]:
    """Move a page to a new name, and take every reference to it along.

    The page's own versions follow it, the notes written about it follow it, every
    ``[[link]]`` that named it is rewritten to the new name, and the index is
    rebuilt. A link that was written as a bare name and still resolves to the page
    where it now sits is left exactly as the author wrote it.
    """
    source, rel = _page_path(layout, relative)
    target_rel = _target_rel(to)
    if target_rel == rel:
        return {
            "from": rel,
            "path": rel,
            "rewritten": [],
            "notes": 0,
            "moved": False,
        }
    if target_rel in _PROTECTED_PAGES:
        raise Refusal(
            "file.protected",
            f"the program maintains this file: {target_rel}",
        )
    target = layout.root.joinpath(*target_rel.split("/"))
    if target.exists():
        raise Refusal(
            "page.exists",
            f"a page is already there: {target_rel}",
            params={"path": target_rel},
            status=409,
        )

    before_maps = wiki_slug_maps(layout)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    prune_empty_parents(source.parent, layout.wiki)

    # The log names the page, not the file it was in: refiling its versions means
    # the page keeps the history it earned under its old name.
    rename_entries(layout, rel, target_rel)
    rewritten = _rewrite_links(layout, rel, target_rel, before_maps)
    moved_notes = _repoint_notes(layout, rel, target_rel)
    rebuild_wiki_index(layout)
    return {
        "from": rel,
        "path": target_rel,
        "rewritten": rewritten,
        "notes": moved_notes,
        "moved": True,
    }


def page_backlinks(layout: ProjectLayout, relative: str) -> dict[str, object]:
    """The pages that link here, and how many notes are about this page."""
    _path, rel = _page_path(layout, relative)
    slug_to_rel, bare_to_rel = wiki_slug_maps(layout)
    pages: list[dict[str, str]] = []
    for other_rel, text in iter_wiki_content_pages(layout):
        if other_rel == rel:
            continue
        body = parse_page(text).body
        for target in parse_wikilink_targets(body):
            if resolve_wikilink(target, slug_to_rel, bare_to_rel) != rel:
                continue
            pages.append({"path": other_rel, "title": _title_of(parse_page(text).fields, other_rel)})
            break
    notes = sum(1 for note in list_notes(layout) if note.page == rel)
    return {"pages": pages, "count": len(pages), "notes": notes}


def page_versions(
    layout: ProjectLayout,
    relative: str,
    *,
    limit: int = 50,
) -> dict[str, object]:
    """What this page has been, newest first, with what it says now in front."""
    path, rel = _page_path(layout, relative)
    text = _read(path)
    entries = page_snapshots(layout, rel)
    versions: list[dict[str, object]] = []
    seen: set[str] = set()

    current = blob_digest(text)
    latest = entries[0] if entries else None
    versions.append(
        {
            "id": current,
            "at": latest.at if latest else "",
            "by": latest.by if latest else "",
            "mode": "current",
            "bytes": len(text.encode("utf-8")),
            "current": True,
        }
    )
    seen.add(current)
    for entry in entries:
        if len(versions) >= max(1, limit):
            break
        digest = entry.before
        if not digest or digest in seen:
            continue
        blob = blob_path(layout, digest)
        if not blob.is_file():
            continue
        seen.add(digest)
        versions.append(
            {
                "id": digest,
                "at": entry.at,
                "by": entry.by,
                "mode": entry.mode,
                "bytes": blob.stat().st_size,
                "current": False,
            }
        )
    return {"path": rel, "versions": versions}


def read_version(layout: ProjectLayout, relative: str, version_id: str) -> dict[str, object]:
    """One version of a page, as text."""
    _path, rel = _page_path(layout, relative)
    known = page_versions(layout, rel, limit=200)
    match = next(
        (item for item in known["versions"] if item["id"] == version_id),
        None,
    )
    if match is None:
        raise Refusal(
            "page.versionMissing",
            f"no such version of {rel}: {version_id}",
            params={"path": rel},
            status=404,
        )
    if match["current"]:
        content = _read(layout.root.joinpath(*rel.split("/")))
    else:
        content = blob_path(layout, version_id).read_text(encoding="utf-8")
    return {
        "path": rel,
        "id": version_id,
        "at": match["at"],
        "by": match["by"],
        "content": content,
    }


def restore_version(
    layout: ProjectLayout,
    relative: str,
    version_id: str,
    *,
    base_revision: str = "",
    now: datetime | None = None,
) -> dict[str, object]:
    """Put an earlier version back, keeping what it replaces as a version too."""
    path, rel = _page_path(layout, relative)
    text = _read(path)
    current = blob_digest(text)
    if base_revision and base_revision != current:
        raise Refusal(
            "page.changed",
            f"the page changed since it was opened: {rel}",
            params={"path": rel},
            status=409,
        )
    known = page_versions(layout, rel, limit=200)
    if not any(item["id"] == version_id for item in known["versions"]):
        raise Refusal(
            "page.versionMissing",
            f"no such version of {rel}: {version_id}",
            params={"path": rel},
            status=404,
        )
    if version_id == current:
        return {"path": rel, "changed": False, "revision": current}
    restored = blob_path(layout, version_id).read_text(encoding="utf-8")
    restored = to_lf(restored)
    keep_before(layout, text)
    write_text(path, restored)
    record_write(
        layout,
        rel,
        before=current,
        after_text=restored,
        mode="restore",
        by=WRITTEN_BY_PERSON,
        at=(now or datetime.now()).isoformat(timespec="seconds"),
    )
    return {"path": rel, "changed": True, "revision": blob_digest(restored)}


def _page_path(layout: ProjectLayout, relative: str) -> tuple[Path, str]:
    """A wiki page that exists, checked because it becomes a write target."""
    rel = normalize_wiki_rel(relative)
    if rel in _PROTECTED_PAGES:
        raise Refusal(
            "file.protected",
            f"the program maintains this file: {rel}",
            params={"path": rel},
        )
    if not is_wiki_content_page(rel):
        raise Refusal(
            "page.notAPage",
            f"not a wiki page: {relative}",
            params={"path": relative},
            status=400,
        )
    path = layout.root.joinpath(*rel.split("/"))
    try:
        path.resolve().relative_to(layout.root.resolve())
    except ValueError as err:
        raise Refusal(
            "page.notAPage",
            f"path escapes the project: {relative}",
            params={"path": relative},
            status=400,
        ) from err
    if not path.is_file():
        raise Refusal(
            "page.missing",
            f"no such page: {relative}",
            params={"path": relative},
            status=404,
        )
    return path, rel


def _target_rel(to: str) -> str:
    """Where a rename is asking the page to go, as a project-relative path."""
    text = normalize_wiki_rel(to)
    if text.startswith(f"{WIKI_DIR}/"):
        text = text[len(WIKI_DIR) + 1 :]
    if not text:
        raise Refusal("page.nameEmpty", "a page needs a name")
    if not text.lower().endswith(".md"):
        text += ".md"
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts[:-1]) or parts[-1] in (".", ".."):
        raise Refusal(
            "page.nameNotAllowed",
            f"not a usable page name: {to}",
            params={"name": to},
            status=400,
        )
    if not is_wiki_content_page(f"{WIKI_DIR}/{text}"):
        raise Refusal(
            "page.nameNotAllowed",
            f"not a usable page name: {to}",
            params={"name": to},
            status=400,
        )
    return f"{WIKI_DIR}/{text}"


def _rewrite_links(
    layout: ProjectLayout,
    old_rel: str,
    new_rel: str,
    before_maps: tuple[dict[str, str], dict[str, str]],
) -> list[str]:
    """Point every link that named the old page at the one that now holds it."""
    after_maps = wiki_slug_maps(layout)
    new_slug = wiki_slug_for_path(new_rel)
    rewritten: list[str] = []
    for rel, text in iter_wiki_content_pages(layout):
        links = find_wikilinks(text)
        if not links:
            continue
        edits: list[tuple[int, int, str]] = []
        for link in links:
            if resolve_wikilink(link.target, *after_maps) == new_rel:
                # Still means the same page where it was written; rewriting it
                # would be a change nobody asked for.
                continue
            if resolve_wikilink(link.target, *before_maps) != old_rel:
                continue
            edits.append((link.start, link.end, format_wikilink(new_slug, link.label)))
        if not edits:
            continue
        pieces: list[str] = []
        cursor = 0
        for start, end, rendered in edits:
            pieces.append(text[cursor:start])
            pieces.append(rendered)
            cursor = end
        pieces.append(text[cursor:])
        next_text = "".join(pieces)
        path = layout.root.joinpath(*rel.split("/"))
        before = keep_before(layout, text)
        write_text(path, next_text)
        record_write(
            layout,
            rel,
            before=before,
            after_text=next_text,
            mode="relink",
            by=WRITTEN_BY_PERSON,
        )
        rewritten.append(rel)
    return rewritten


def _repoint_notes(layout: ProjectLayout, old_rel: str, new_rel: str) -> int:
    """Move the notes about a page to its new name, taking them off the old one."""
    moved = 0
    for note in list_notes(layout):
        if note.page != old_rel:
            continue
        source = find_note_path(layout, note.id)
        target = note_path(layout, new_rel, note.id)
        if source is None or source == target:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, render_note(replace(note, page=new_rel)))
        source.unlink()
        prune_empty_parents(source.parent, layout.notes)
        moved += 1
    return moved


def _title_of(fields: dict[str, str], rel: str) -> str:
    if "title" in fields:
        title = unquote_scalar(fields["title"]).strip()
        if title:
            return title
    return Path(rel).stem


def _read(path: Path) -> str:
    return to_lf(path.read_text(encoding="utf-8"))


def _diff(before: str, after: str) -> str:
    """A unified diff of two bodies, cut off when it would be a wall of text."""
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            lineterm="",
            n=_DIFF_CONTEXT,
        )
    )
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ "):
        lines = lines[2:]
    if not lines:
        return ""
    if len(lines) > MAX_DIFF_LINES:
        rest = len(lines) - MAX_DIFF_LINES
        lines = lines[:MAX_DIFF_LINES] + [f"… {rest} more lines"]
    return "\n".join(lines)


def _diff_counts(before: str, after: str) -> tuple[int, int]:
    added = 0
    removed = 0
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0))
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ "):
        lines = lines[2:]
    for line in lines:
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed
