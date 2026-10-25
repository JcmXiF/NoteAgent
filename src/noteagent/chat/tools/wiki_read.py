"""wiki_read: open current-project wiki Markdown. Never reads raw/."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from noteagent.chat.limits import ChatLimits
from noteagent.chat.tools.types import ToolObservation
from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.ingest.page_merge import page_sources
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import truncate
from noteagent.wiki.links import (
    is_wiki_content_page,
    resolve_wikilink,
    wiki_slug_for_path,
    wiki_slug_maps,
)

TOOL_NAME = "wiki_read"
TOOL_DESCRIPTION = (
    "Open wiki Markdown page bodies by [[slug]]. "
    "Does not read raw/ or modify the wiki."
)
TOOL_ARGUMENT_HINT = "targets: list of [[slug]] or wiki/...md paths"
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "targets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Wiki pages as [[slug]] or wiki/...md paths",
        }
    },
    "required": ["targets"],
}


def wiki_read(
    layout: ProjectLayout,
    targets: Sequence[str],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    slug_to_rel, bare_to_rel = wiki_slug_maps(layout)
    root = layout.root.resolve()
    remaining = limits.max_pages_chars
    out: list[ToolObservation] = []

    for raw in targets:
        if len(out) >= limits.max_open_pages:
            break
        rel = resolve_wikilink(raw, slug_to_rel, bare_to_rel)
        if rel is not None and not is_wiki_content_page(rel):
            rel = None
        if rel is None:
            out.append(
                ToolObservation(
                    tool=TOOL_NAME,
                    title=raw,
                    target=raw,
                    content="",
                    meta={"missing": True},
                )
            )
            continue
        path = (layout.root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            out.append(
                ToolObservation(
                    tool=TOOL_NAME,
                    title=raw,
                    target=raw,
                    content="",
                    meta={"missing": True, "unsafe": True},
                )
            )
            continue
        if not path.is_file():
            out.append(
                ToolObservation(
                    tool=TOOL_NAME,
                    title=raw,
                    target=rel,
                    content="",
                    meta={"missing": True},
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            out.append(
                ToolObservation(
                    tool=TOOL_NAME,
                    title=raw,
                    target=rel,
                    content="",
                    meta={"missing": True, "error": str(err)},
                )
            )
            continue

        page = parse_page(text)
        title = unquote_scalar(page.fields["title"]) if "title" in page.fields else ""
        try:
            slug = wiki_slug_for_path(rel)
        except ValueError:
            slug = raw
        if not title:
            title = slug.rsplit("/", 1)[-1]
        sources = page_sources(text)
        body = page.body.strip()
        meta: dict[str, Any] = {"path": rel, "slug": slug, "sources": sources}

        if remaining <= 0:
            meta["omitted"] = "budget"
            out.append(
                ToolObservation(
                    tool=TOOL_NAME,
                    title=title,
                    target=f"[[{slug}]]",
                    content="",
                    meta=meta,
                )
            )
            continue

        budget = min(limits.max_page_chars, remaining)
        clipped = truncate(body, budget)
        meta["truncated"] = clipped != body
        remaining -= min(len(body), budget)
        out.append(
            ToolObservation(
                tool=TOOL_NAME,
                title=title,
                target=f"[[{slug}]]",
                content=clipped,
                meta=meta,
            )
        )
    return tuple(out)
