"""Build the global wiki graph from body [[wikilink]]s.

Does not persist graph.json. Navigation only — no scores, no Louvain, no
source-file edges. Chat keeps its own unique-target helper; this module
counts repeats on the same directed pair.
"""

from __future__ import annotations

from typing import Any

from noteagent.ingest.frontmatter import ParsedPage, parse_page, unquote_scalar
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.link_syntax import find_wikilinks
from noteagent.wiki.links import (
    is_wiki_content_page,
    normalize_wiki_rel,
    normalize_wikilink_target,
    resolve_wikilink,
    wiki_slug_for_path,
    wiki_slug_maps,
)
from noteagent.wiki.page_kinds import NOTE_KIND, SOURCE_DIR, is_source_page

_NAV_TARGETS = frozenset({"index", "overview", "log"})


def wiki_graph_payload(layout: ProjectLayout) -> dict[str, Any]:
    """Nodes + directed edges for GET /graph. Isolated pages stay visible."""
    slug_to_rel, bare_to_rel = wiki_slug_maps(layout)
    nodes: dict[str, dict[str, object]] = {}
    edge_counts: dict[tuple[str, str], int] = {}

    pages: list[tuple[str, str, ParsedPage]] = []
    for rel, content in _iter_graph_pages(layout):
        pages.append((rel, wiki_slug_for_path(rel), parse_page(content)))

    for rel, slug, page in pages:
        nodes[slug] = _real_node(slug, rel, page.fields)

    for _rel, source_id, page in pages:
        for link in find_wikilinks(page.body or ""):
            target = normalize_wikilink_target(link.target)
            if not target or _is_nav_target(target):
                continue
            dest_id = _resolve_graph_target(
                target,
                slug_to_rel,
                bare_to_rel,
                nodes,
            )
            if dest_id is None or dest_id == source_id:
                continue
            edge_counts[(source_id, dest_id)] = (
                edge_counts.get((source_id, dest_id), 0) + 1
            )

    neighbors: dict[str, set[str]] = {key: set() for key in nodes}
    for source_id, dest_id in edge_counts:
        neighbors.setdefault(source_id, set()).add(dest_id)
        neighbors.setdefault(dest_id, set()).add(source_id)

    node_list = []
    for item in nodes.values():
        node_id = str(item["id"])
        node_list.append({**item, "degree": len(neighbors.get(node_id, set()))})
    node_list.sort(key=lambda item: str(item["id"]).casefold())

    edges = [
        {"source": source_id, "target": dest_id, "count": count}
        for (source_id, dest_id), count in sorted(
            edge_counts.items(),
            key=lambda pair: (pair[0][0].casefold(), pair[0][1].casefold()),
        )
    ]
    return {"nodes": node_list, "edges": edges}


def is_graph_content_page(relative_path: str) -> bool:
    """True for every page the graph draws: notes, never source summaries."""
    normalized = normalize_wiki_rel(relative_path)
    if not is_wiki_content_page(normalized):
        return False
    return not is_source_page(normalized)


def _iter_graph_pages(layout: ProjectLayout) -> list[tuple[str, str]]:
    """``(relative path, text)`` for every page the graph draws.

    Walking the whole wiki rather than a list of known folders means a page in a
    folder this version has never heard of is still drawn. Navigation files and
    source summaries are excluded by ``is_graph_content_page``.
    """
    pages: list[tuple[str, str]] = []
    if not layout.wiki.is_dir():
        return pages
    root = layout.root.resolve()
    for path in sorted(
        layout.wiki.rglob("*.md"),
        key=lambda item: item.as_posix().casefold(),
    ):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if not is_graph_content_page(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        pages.append((rel, text))
    return pages


def _real_node(slug: str, rel: str, fields: dict[str, str]) -> dict[str, object]:
    """A page the project holds, with the title it declares."""
    title = unquote_scalar(str(fields.get("title") or "")).strip()
    return {
        "id": slug,
        "path": rel,
        "title": title or slug.split("/")[-1],
        "type": NOTE_KIND,
        "ghost": False,
    }


def _ghost_node(slug: str) -> dict[str, object]:
    return {
        "id": slug,
        "path": None,
        "title": slug.split("/")[-1],
        "type": "ghost",
        "ghost": True,
    }


def _resolve_graph_target(
    target: str,
    slug_to_rel: dict[str, str],
    bare_to_rel: dict[str, str],
    nodes: dict[str, dict[str, object]],
) -> str | None:
    if target.split("/")[0].lower() == SOURCE_DIR or _is_nav_target(target):
        return None
    resolved = resolve_wikilink(target, slug_to_rel, bare_to_rel)
    if resolved:
        if not is_graph_content_page(resolved):
            return None
        return wiki_slug_for_path(resolved)
    if target not in nodes:
        nodes[target] = _ghost_node(target)
    return target


def _is_nav_target(target: str) -> bool:
    return target.split("/")[-1].lower() in _NAV_TARGETS
