"""Resolve [[wikilink]] targets to wiki Markdown paths.

Chat, doctor, and the later graph all need the same mapping. Content pages
only — ``index`` / ``overview`` / ``log`` stay catalog files, not wiki_read hits.

What counts as a link is ``link_syntax``'s business; this module turns the target
it reports into a file, by the three-step fallback that makes a wikilink worth
writing: a full ``folder/slug``, then a bare name, then the last segment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noteagent.wiki.link_syntax import find_wikilinks

if TYPE_CHECKING:
    from noteagent.project.layout import ProjectLayout

_NAV_PAGES = frozenset({"wiki/index.md", "wiki/overview.md", "wiki/log.md"})


def normalize_wiki_rel(path: str) -> str:
    """A project-relative path with forward slashes and no leading ``./``."""
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def is_wiki_content_page(relative_path: str) -> bool:
    normalized = normalize_wiki_rel(relative_path).lower()
    if normalized in _NAV_PAGES:
        return False
    return normalized.startswith("wiki/") and normalized.endswith(".md")


def wiki_slug_for_path(relative_path: str) -> str:
    """``wiki/foo.md`` → ``foo``; a legacy folder stays in the slug."""
    normalized = normalize_wiki_rel(relative_path)
    if not normalized.startswith("wiki/"):
        raise ValueError(f"not under wiki/: {relative_path}")
    rest = normalized[len("wiki/") :]
    if rest.lower().endswith(".md"):
        rest = rest[:-3]
    return rest


def normalize_wikilink_target(target: str) -> str:
    """A link target as ``folder/slug``, however it was written."""
    text = target.strip().replace("\\", "/")
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2].strip()
    text = text.split("|")[0].strip()
    if text.startswith("wiki/"):
        text = text[5:]
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text.strip().strip("/")


def parse_wikilink_targets(text: str) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for link in find_wikilinks(text or ""):
        target = normalize_wikilink_target(link.target)
        if not target or target in seen:
            continue
        seen.add(target)
        out.append(target)
    return tuple(out)


def wiki_slug_maps(layout: ProjectLayout) -> tuple[dict[str, str], dict[str, str]]:
    """Two lookup tables from the pages on disk.

    Returns ``(folder/slug -> wiki/...md, bare-name -> wiki/...md)``. The first
    disk path wins for a bare name, so two folders holding the same file name
    still resolve to one page rather than to whichever was read last.
    """
    slug_to_rel: dict[str, str] = {}
    bare_to_rel: dict[str, str] = {}
    if not layout.wiki.is_dir():
        return slug_to_rel, bare_to_rel
    root = layout.root.resolve()
    for path in sorted(
        layout.wiki.rglob("*.md"), key=lambda item: item.as_posix().casefold()
    ):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if not is_wiki_content_page(rel):
            continue
        try:
            slug = wiki_slug_for_path(rel)
        except ValueError:
            continue
        slug_to_rel[slug] = rel
        bare_to_rel.setdefault(slug.split("/")[-1], rel)
    return slug_to_rel, bare_to_rel


def resolve_wikilink(
    target: str,
    slug_to_rel: dict[str, str],
    bare_to_rel: dict[str, str],
) -> str | None:
    """The page a link target names: full slug, then bare name, then last segment."""
    text = normalize_wikilink_target(target)
    if not text:
        return None
    if text in slug_to_rel:
        return slug_to_rel[text]
    if text in bare_to_rel:
        return bare_to_rel[text]
    return bare_to_rel.get(text.split("/")[-1])
