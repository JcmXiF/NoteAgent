"""Keep ``wiki/index.md`` in sync with the pages on disk.

The index has two sections: source pages under ``Sources`` and every other page
under ``Notes``. The folder decides, not a frontmatter field. Adding is
deterministic: a page already linked in the index is not linked twice. A full
rebuild rewrites the whole file from the pages that currently exist, so deleted
pages leave no ghost bullets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.io.text_file import write_text
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.link_syntax import find_wikilinks, format_wikilink
from noteagent.wiki.links import is_wiki_content_page, wiki_slug_for_path
from noteagent.wiki.page_kinds import SOURCE_KIND, page_kind

SOURCE_SECTION = "Sources"
NOTE_SECTION = "Notes"
INDEX_SECTIONS: tuple[str, ...] = (SOURCE_SECTION, NOTE_SECTION)

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """
    One index line to append under a section.
    """

    section: str
    slug: str
    title: str
    line: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class IndexUpdateResult:
    """
    Outcome of a deterministic index update.
    """

    added: tuple[IndexEntry, ...]
    skipped: tuple[str, ...]  # reasons
    dry_run: bool
    index_path: str = "wiki/index.md"


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _is_indexable(relative_path: str) -> bool:
    return is_wiki_content_page(relative_path)


def section_for_page(relative_path: str) -> str:
    """
    The section a page belongs in: source pages under Sources, the rest under Notes.

    A page still in a retired type folder is a note, with its folder kept inside
    the slug, so nothing disappears from the catalog before migration.
    """
    return SOURCE_SECTION if page_kind(relative_path) == SOURCE_KIND else NOTE_SECTION


def _existing_slugs(index_text: str) -> set[str]:
    return {link.target for link in find_wikilinks(index_text)}


def _append_under_section(index_text: str, section: str, line: str) -> str:
    """
    Insert ``line`` at the end of ``## section`` (create heading if missing).
    """
    text = index_text.replace("\r\n", "\n")
    if not text.endswith("\n") and text:
        text += "\n"

    lines = text.split("\n")
    heading = f"## {section}"
    start: int | None = None
    for i, raw in enumerate(lines):
        match = _HEADING_RE.match(raw)
        if match and match.group(1).strip() == section:
            start = i
            break

    if start is None:
        # append new section at end
        body = text.rstrip() + "\n\n" + heading + "\n\n" + line + "\n"
        return body

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _HEADING_RE.match(lines[j]):
            end = j
            break

    # trim trailing blanks inside section before insert
    insert_at = end
    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    new_lines = lines[:insert_at] + [line] + lines[insert_at:]
    result = "\n".join(new_lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def build_index_entries(
    pages: list[tuple[str, str]],
) -> tuple[list[IndexEntry], list[str]]:
    """
    Build candidate index entries from (relative_path, content) pairs.
    """
    entries: list[IndexEntry] = []
    skipped: list[str] = []
    seen_slug: set[str] = set()

    for relative_path, content in pages:
        path = _normalize_rel(relative_path)
        if not _is_indexable(path):
            skipped.append(f"skipped non-indexable path: {path}")
            continue
        try:
            slug = wiki_slug_for_path(path)
        except ValueError as err:
            skipped.append(f"skipped {path}: {err}")
            continue
        if slug in seen_slug:
            skipped.append(f"skipped duplicate in batch: {path}")
            continue
        seen_slug.add(slug)

        parsed = parse_page(content)
        title = (
            unquote_scalar(parsed.fields["title"])
            if "title" in parsed.fields
            else slug.rsplit("/", 1)[-1]
        )
        section = section_for_page(path)
        line = f"- {format_wikilink(slug)} — {title}"
        entries.append(
            IndexEntry(
                section=section,
                slug=slug,
                title=title,
                line=line,
                relative_path=path,
            )
        )
    return entries, skipped


def update_wiki_index(
    layout: ProjectLayout,
    pages: list[tuple[str, str]],
    *,
    dry_run: bool = False,
) -> IndexUpdateResult:
    """
    Deterministically append new wiki pages into ``wiki/index.md`` sections.
    """
    candidates, skipped = build_index_entries(pages)
    index_path = layout.wiki_index
    if index_path.is_file():
        current = index_path.read_text(encoding="utf-8")
    else:
        current = (
            "# Wiki Index\n\n"
            "按来源与笔记列出所有页面。\n\n"
            "## Sources\n\n"
            "## Notes\n"
        )

    existing = _existing_slugs(current)
    added: list[IndexEntry] = []
    next_text = current
    for entry in candidates:
        if entry.slug in existing:
            skipped.append(f"already indexed: {entry.slug}")
            continue
        next_text = _append_under_section(next_text, entry.section, entry.line)
        existing.add(entry.slug)
        added.append(entry)

    if added and not dry_run:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(index_path, next_text)

    return IndexUpdateResult(
        added=tuple(added),
        skipped=tuple(skipped),
        dry_run=dry_run,
    )


def iter_wiki_content_pages(layout: ProjectLayout) -> list[tuple[str, str]]:
    """
    List ``(relative_path, content)`` for indexable pages on disk.
    """
    pages: list[tuple[str, str]] = []
    if not layout.wiki.is_dir():
        return pages
    for path in sorted(layout.wiki.rglob("*.md")):
        try:
            rel = path.relative_to(layout.root).as_posix()
        except ValueError:
            continue
        if not _is_indexable(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        pages.append((rel, text))
    return pages


def rebuild_wiki_index(
    layout: ProjectLayout,
    *,
    dry_run: bool = False,
) -> IndexUpdateResult:
    """
    Rebuild ``wiki/index.md`` from pages currently on disk (no ghosts).
    """
    pages = iter_wiki_content_pages(layout)
    candidates, skipped = build_index_entries(pages)

    by_section: dict[str, list[IndexEntry]] = {name: [] for name in INDEX_SECTIONS}
    for entry in candidates:
        by_section.setdefault(entry.section, []).append(entry)

    lines = [
        "# Wiki Index",
        "",
        "按来源与笔记列出所有页面。",
        "",
    ]
    for section in INDEX_SECTIONS:
        entries = by_section.get(section) or []
        lines.append(f"## {section}")
        lines.append("")
        for entry in sorted(entries, key=lambda e: e.slug.lower()):
            lines.append(entry.line)
        lines.append("")

    next_text = "\n".join(lines).rstrip() + "\n"
    if not dry_run:
        layout.wiki_index.parent.mkdir(parents=True, exist_ok=True)
        write_text(layout.wiki_index, next_text)

    return IndexUpdateResult(
        added=tuple(candidates),
        skipped=tuple(skipped),
        dry_run=dry_run,
    )


def format_index_update_result(result: IndexUpdateResult) -> str:
    """
    Human-readable summary for CLI.
    """
    parts = [
        f"index: {result.index_path}",
        f"added: {len(result.added)}",
        f"skipped: {len(result.skipped)}",
        f"dry_run: {result.dry_run}",
    ]
    for entry in result.added:
        prefix = "would add" if result.dry_run else "added"
        parts.append(f"- [{prefix}] {entry.section}: {entry.line}")
    return "\n".join(parts)
