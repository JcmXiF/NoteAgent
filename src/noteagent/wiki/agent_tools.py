"""The wiki as the agent may touch it: pages by slug, and search over them.

Two read-only tools, and deliberately only two. The agent reaches a page by the
name a reader would use, or searches the wiki's own text; it never gets a file
system. ``raw/`` holds the material and ``notes/`` the reader's own layer, so
neither is reachable from here: a target only resolves to a page the wiki
already lists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import (
    Diagnostic,
    OutputLimits,
    ToolContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.project.layout import ProjectLayout
from noteagent.rag.result import RagSearchResult
from noteagent.wiki.links import (
    is_wiki_content_page,
    resolve_wikilink,
    wiki_slug_for_path,
    wiki_slug_maps,
)
from noteagent.wiki.local_search import query_needles, rank_text

if TYPE_CHECKING:
    from noteagent.rag.search import RagSearchBackend

WIKI_READ = "wiki_read"
SEARCH = "search"

_READ_DESCRIPTION = (
    "Open one wiki page by slug (a [[wikilink]] or a wiki/...md path). "
    "Read a page before quoting or summarizing it. "
    "Call it once per page; several calls in one round run in parallel. "
    "Wiki pages only: never raw/ or notes/."
)
SEARCH_DESCRIPTION = (
    "Search this project's wiki pages by slug, title, or body text. "
    "Returns page slugs and short excerpts. Excerpts are locators, not quotes: "
    "wiki_read a page before relying on it. "
    "Wiki pages only: never raw/ or notes/."
)

_READ_PARAMETERS = {
    "type": "object",
    "properties": {
        "slug": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": "Wiki page as [[slug]] or wiki/...md",
        }
    },
    "required": ["slug"],
    "additionalProperties": False,
}

SEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": "Words to find in a page's slug, title, or body",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}

#: Marked on text the tool had to cut, so the model knows the page goes on.
_TRUNCATED = "\n\n…(truncated)…"


@dataclass(frozen=True, slots=True)
class WikiToolLimits:
    """How much of the wiki one call may carry.

    ``wiki_read`` takes one page per call, so ``max_page_chars`` is what one
    result may hold; reading several pages is several parallel calls, and the
    round's total is bounded by the calls the loop admits.
    """

    max_page_chars: int = 8_000
    max_hits: int = 20
    excerpt_chars: int = 400
    max_result_chars: int = 24_000


def wiki_tools(
    layout: ProjectLayout,
    *,
    limits: WikiToolLimits | None = None,
    rag: "RagSearchBackend | None" = None,
) -> ToolRegistry:
    """The registry the chat loop offers for one project.

    ``rag`` swaps the ``search`` backend only; the tool's name, schema and
    description do not change, so an old session keeps the same contract.
    """
    bounds = limits or WikiToolLimits()
    return ToolRegistry(
        [
            ToolSpec(
                name=WIKI_READ,
                description=_READ_DESCRIPTION,
                parameters=_READ_PARAMETERS,
                side_effect="read",
                timeout_s=10.0,
                output_limits=OutputLimits(
                    max_items=1,
                    max_chars_per_item=bounds.max_page_chars,
                    max_chars_total=bounds.max_result_chars,
                ),
                handler=_read_handler(layout, bounds),
                argument_hint="slug",
            ),
            ToolSpec(
                name=SEARCH,
                description=SEARCH_DESCRIPTION,
                parameters=SEARCH_PARAMETERS,
                side_effect="read",
                timeout_s=15.0,
                output_limits=OutputLimits(
                    max_items=bounds.max_hits,
                    max_chars_per_item=bounds.excerpt_chars,
                    max_chars_total=bounds.max_result_chars,
                ),
                handler=_search_handler(layout, bounds, rag),
                argument_hint="query",
            ),
        ]
    )


def _read_handler(layout: ProjectLayout, limits: WikiToolLimits):
    def read(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        target = str(arguments["slug"]).strip()
        _check_cancel(context)
        slug_to_rel, bare_to_rel = wiki_slug_maps(layout)
        relative = resolve_wikilink(target, slug_to_rel, bare_to_rel)
        if relative is None or not is_wiki_content_page(relative):
            return ToolResult(
                content=f"no wiki page for {target}",
                meta={"title": target, "target": target, "missing": True},
            )
        path = (layout.root / relative).resolve()
        try:
            path.relative_to(layout.root.resolve())
        except ValueError:
            # A page that resolves outside the project is not a page.
            return ToolResult(
                content=f"no wiki page for {target}",
                meta={"title": target, "target": target, "missing": True},
            )
        text = path.read_text(encoding="utf-8")

        page = parse_page(text)
        title = _title(page.fields, relative)
        slug = _slug(relative, target)
        header = f"[[{slug}]] — {title}\n\n"
        budget = max(0, limits.max_page_chars - len(header))
        body, clipped = _clip(page.body.strip(), budget)
        meta: dict[str, object] = {
            "title": title,
            "target": f"[[{slug}]]",
            "path": relative,
            "slug": slug,
        }
        diagnostics: list[Diagnostic] = []
        if clipped:
            meta["truncated"] = True
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "truncated",
                    f"{slug} is longer than {limits.max_page_chars} characters",
                )
            )
        return ToolResult(header + body, meta, tuple(diagnostics))

    return read


@dataclass(frozen=True, slots=True)
class _Hit:
    score: int
    slug: str
    match: str
    excerpt: str


def _search_handler(
    layout: ProjectLayout,
    limits: WikiToolLimits,
    rag: "RagSearchBackend | None",
):
    def search(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        query = str(arguments["query"]).strip()
        if not query:
            raise ValueError("search query must not be empty")
        hybrid = None
        if rag is not None:
            try:
                hybrid = rag.search(query)
            except Exception as err:  # noqa: BLE001 - fallback is the contract
                hybrid = RagSearchResult(
                    (),
                    "lexical",
                    True,
                    (f"retrieval failed: {err}",),
                )
        if hybrid is not None and (
            not hybrid.degraded or hybrid.hits
        ):
            return _rag_result(query, hybrid, limits)
        result = _lexical_search(layout, limits, query, context)
        if hybrid is None:
            return result
        meta = dict(result.meta)
        meta["mode"] = hybrid.mode
        meta["degraded"] = True
        diagnostics = list(result.diagnostics)
        for warning in hybrid.warnings:
            diagnostics.append(
                Diagnostic("warning", "rag_fallback", warning)
            )
        return ToolResult(result.content, meta, tuple(diagnostics))

    return search


def _lexical_search(
    layout: ProjectLayout,
    limits: WikiToolLimits,
    query: str,
    context: ToolContext,
) -> ToolResult:
    slug_to_rel, _bare = wiki_slug_maps(layout)
    needles = query_needles(query)
    ranked: list[_Hit] = []
    skipped = 0
    for slug, relative in slug_to_rel.items():
        _check_cancel(context)
        page = _title_and_body(layout, relative)
        if page is None:
            # One unreadable page must not fail the whole search, but the
            # model should know the result is not the whole wiki.
            skipped += 1
            continue
        title, body = page
        score, match, excerpt = rank_text(needles, slug, title, body)
        if score <= 0:
            continue
        ranked.append(_Hit(score, slug, match, excerpt))
    ranked.sort(key=lambda hit: (-hit.score, hit.slug.casefold()))
    return _render_search(
        query,
        ranked,
        limits,
        extra=_skipped_diagnostic(skipped),
    )


def _rag_result(
    query: str, result: RagSearchResult, limits: WikiToolLimits
) -> ToolResult:
    hits = [
        _Hit(hit.score, hit.slug, hit.match, hit.excerpt)
        for hit in result.hits
    ]
    return _render_search(
        query,
        hits,
        limits,
        mode=result.mode,
        degraded=result.degraded,
        warnings=result.warnings,
        default_match="semantic",
    )


def _render_search(
    query: str,
    hits: list[_Hit],
    limits: WikiToolLimits,
    *,
    mode: str | None = None,
    degraded: bool | None = None,
    warnings: tuple[str, ...] = (),
    extra: tuple[Diagnostic, ...] = (),
    default_match: str = "body",
) -> ToolResult:
    diagnostics: list[Diagnostic] = list(extra)
    diagnostics.extend(
        Diagnostic("warning", "rag_degraded", warning)
        for warning in warnings
    )
    if not hits:
        meta: dict[str, object] = {
            "title": query,
            "target": query,
            "query": query,
            "missing": True,
        }
        if mode is not None:
            meta["scope"] = "wiki"
            meta["hits"] = 0
            meta["mode"] = mode
            meta["degraded"] = bool(degraded)
        return ToolResult(
            f"no wiki pages matched {query}",
            meta,
            tuple(diagnostics),
        )

    selected = hits[: max(0, limits.max_hits)]
    omitted = len(hits) - len(selected)
    lines: list[str] = []
    clipped = False
    for hit in selected:
        shown, cut = _clip(hit.excerpt, limits.excerpt_chars)
        clipped = clipped or cut
        lines.append(
            f"[[{hit.slug}]] — matched "
            f"{hit.match or default_match}"
        )
        if shown:
            lines.append(f"  excerpt: {shown}")
    if omitted:
        lines.append(f"{omitted} more matches omitted")
        diagnostics.append(
            Diagnostic(
                "warning",
                "truncated",
                f"{omitted} search hits were omitted",
            )
        )
    if clipped:
        diagnostics.append(
            Diagnostic(
                "warning",
                "truncated",
                f"an excerpt was cut to {limits.excerpt_chars} characters",
            )
        )
    meta = {
        "title": query,
        "target": query,
        "query": query,
        "scope": "wiki",
        "hits": len(selected),
    }
    if mode is not None:
        meta["mode"] = mode
        meta["degraded"] = bool(degraded)
    if omitted:
        meta["omitted"] = "budget"
        meta["truncated"] = True
    if clipped:
        meta["truncated"] = True
    return ToolResult("\n".join(lines), meta, tuple(diagnostics))


def _skipped_diagnostic(skipped: int) -> tuple[Diagnostic, ...]:
    if not skipped:
        return ()
    return (
        Diagnostic(
            "warning",
            "unreadable",
            f"{skipped} wiki pages could not be read",
        ),
    )


def _title(fields: Mapping[str, object], relative: str) -> str:
    raw = fields.get("title")
    if isinstance(raw, str):
        got = unquote_scalar(raw).strip()
        if got:
            return got
    return relative.rsplit("/", 1)[-1].removesuffix(".md")


def _slug(relative: str, fallback: str) -> str:
    try:
        return wiki_slug_for_path(relative)
    except ValueError:
        return fallback


def _title_and_body(
    layout: ProjectLayout, relative: str
) -> tuple[str, str] | None:
    """The title a page shows and the prose below its frontmatter.

    ``None`` means the file was listed but could not be read; the caller counts
    it instead of treating it as an empty page.
    """
    try:
        text = (layout.root / relative).read_text(encoding="utf-8")
    except OSError:
        return None
    page = parse_page(text)
    return _title(page.fields, relative), page.body


def _clip(text: str, limit: int) -> tuple[str, bool]:
    """Cut *text* to *limit*, with the mark taking room inside the limit."""
    if len(text) <= limit:
        return text, False
    if limit <= len(_TRUNCATED):
        return text[:limit], True
    return text[: limit - len(_TRUNCATED)] + _TRUNCATED, True


def _check_cancel(context: ToolContext) -> None:
    if context.cancel is not None and context.cancel.is_set():
        raise RunCancelled("the run was cancelled")


__all__ = [
    "SEARCH",
    "SEARCH_DESCRIPTION",
    "SEARCH_PARAMETERS",
    "WIKI_READ",
    "WikiToolLimits",
    "wiki_tools",
]
