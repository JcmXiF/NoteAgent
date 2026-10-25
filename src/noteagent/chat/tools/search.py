"""search: wiki slug/title/body substring, or web SERP cards. Never fetches web bodies."""

from __future__ import annotations

from typing import Any

from noteagent.acquire.budget import MAX_CANDIDATES
from noteagent.acquire.run_search import run_web_search
from noteagent.acquire.search_config import SearchConfigError, SearchError
from noteagent.acquire.types import ProviderNotConfiguredError
from noteagent.chat.limits import ChatLimits
from noteagent.chat.tools.types import ToolObservation
from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.project.layout import ProjectLayout
from noteagent.util.run_log import RunLogger
from noteagent.wiki.links import wiki_slug_maps
from noteagent.wiki.local_search import query_needles, rank_text

TOOL_NAME = "search"
TOOL_DESCRIPTION = (
    "Search wiki pages (scope=wiki: slug/title first, then body substring; "
    "not vectors) or the open web (scope=web: SERP cards only). "
    "Web cards are not source material and do not fetch pages. "
    "Wiki excerpts are locators — wiki_read the page before quoting."
)
TOOL_ARGUMENT_HINT = "query: string; scope: wiki | web (default wiki)"
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Search string"},
        "scope": {
            "type": "string",
            "enum": ["wiki", "web"],
            "description": "wiki = current project pages; web = SERP cards",
        },
    },
    "required": ["query"],
}

_WIKI_HIT_CAP = 12


def search(
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("search query must not be empty")
    raw_scope = arguments.get("scope", "wiki")
    scope = str(raw_scope or "wiki").strip().lower()
    if scope == "wiki":
        return _search_wiki(layout, query, limits)
    if scope == "web":
        return _search_web(layout, query)
    raise ValueError("search scope must be wiki or web")


def _search_wiki(
    layout: ProjectLayout, query: str, limits: ChatLimits
) -> tuple[ToolObservation, ...]:
    del limits
    slug_to_rel, _bare = wiki_slug_maps(layout)
    needles = query_needles(query)
    ranked: list[tuple[int, str, str, str, str, str]] = []
    for slug, rel in slug_to_rel.items():
        title, body = _page_title_and_body(layout, rel, slug)
        score, match, excerpt = rank_text(needles, slug, title, body)
        if score <= 0:
            continue
        ranked.append((score, slug, rel, title, match, excerpt))
    ranked.sort(key=lambda item: (-item[0], item[1].casefold()))
    hits = ranked[:_WIKI_HIT_CAP]
    if not hits:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title=query,
                target=query,
                content="no wiki pages matched slug, title, or body",
                meta={"scope": "wiki", "missing": True, "query": query},
            ),
        )
    out: list[ToolObservation] = []
    for _score, slug, rel, title, match, excerpt in hits:
        lines = [f"[[{slug}]] — {title}"]
        if excerpt:
            lines.append(f"excerpt: {excerpt}")
        out.append(
            ToolObservation(
                tool=TOOL_NAME,
                title=title,
                target=f"[[{slug}]]",
                content="\n".join(lines),
                meta={
                    "scope": "wiki",
                    "path": rel,
                    "slug": slug,
                    "query": query,
                    "match": match,
                },
            )
        )
    if len(ranked) > len(hits):
        out.append(
            ToolObservation(
                tool=TOOL_NAME,
                title=query,
                target=query,
                content=f"{len(ranked) - len(hits)} more matches omitted",
                meta={"scope": "wiki", "omitted": "budget", "query": query},
            )
        )
    return tuple(out)


def _search_web(layout: ProjectLayout, query: str) -> tuple[ToolObservation, ...]:
    try:
        run = run_web_search(
            query,
            top_k=MAX_CANDIDATES,
            run_logger=RunLogger.for_layout(layout),
            caller="chat",
        )
    except (ProviderNotConfiguredError, SearchConfigError, SearchError, ValueError) as err:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title=query,
                target=query,
                content=str(err),
                meta={"scope": "web", "error": True, "query": query},
            ),
        )
    if not run.cards:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title=query,
                target=query,
                content="no web cards",
                meta={"scope": "web", "missing": True, "query": query},
            ),
        )
    out: list[ToolObservation] = []
    for card in run.cards:
        site = card.site_name or card.site
        lines = [
            card.title,
            f"url: {card.url}",
            f"site: {site}" if site else "",
            f"snippet: {card.snippet}" if card.snippet else "",
            "SERP card only — not wiki, not raw/. Do not treat the snippet as a note.",
        ]
        out.append(
            ToolObservation(
                tool=TOOL_NAME,
                title=card.title or card.url,
                target=card.url,
                content="\n".join(line for line in lines if line),
                meta={
                    "scope": "web",
                    "url": card.url,
                    "not_source": True,
                    "query": query,
                },
            )
        )
    return tuple(out)


def _page_title_and_body(
    layout: ProjectLayout, rel: str, slug: str
) -> tuple[str, str]:
    fallback = slug.rsplit("/", 1)[-1]
    path = layout.root / rel
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return fallback, ""
    page = parse_page(text)
    title = fallback
    if "title" in page.fields:
        got = unquote_scalar(page.fields["title"]).strip()
        if got:
            title = got
    return title, page.body
