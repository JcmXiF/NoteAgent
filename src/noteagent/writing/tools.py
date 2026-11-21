"""The writing state as agent tools: search, and the one perception call."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from noteagent.acquire.types import CandidateCard
from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds, iso_timestamp
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import Diagnostic, OutputLimits, ToolResult, ToolSpec
from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingStateStore, state_summary

GET_STATE = "get_state"
WEB_SEARCH = "web_search"

WebSearchFn = Callable[[str], Sequence[CandidateCard]]

#: The handler's own budget for one search read; the registry would otherwise
#: slice the JSON at its hard cap and leave the model with a broken object.
_DETAIL_LIMIT = 20_000

_GET_STATE_DESCRIPTION = (
    "Read this project's writing state. With no arguments it returns the summary: "
    "stateVersion, the current writer, the counts of searches, selected pages, "
    "frames and the draft, the latest search, and attention. With searchId it "
    "returns that search's hits (truncated with a diagnostic when the list is "
    "too large). The state is the only source of truth for those areas; read it "
    "before acting on them."
)

_WEB_SEARCH_DESCRIPTION = (
    "Search the web for a query. Returns a status, a searchId and the hit count; "
    "the hits themselves are stored in the writing state. Read them with "
    "get_state({searchId}). A failed search is recorded in the state and returned "
    "as an error observation."
)


def get_state_tool(layout: ProjectLayout) -> ToolSpec:
    """The one perception tool the writing workflow starts from."""

    def get_state(arguments, _context) -> ToolResult:
        search_id = str(arguments.get("searchId") or "").strip()
        state = WritingStateStore(layout).load()
        if search_id:
            row = next(
                (
                    item
                    for item in state.searches
                    if str(item.get("id")) == search_id
                ),
                None,
            )
            if row is None:
                raise ValueError(f"no such search: {search_id}")
            raw_hits = row.get("hits")
            hits = list(raw_hits) if isinstance(raw_hits, list) else []
            content = json.dumps(hits, ensure_ascii=False)
            omitted = 0
            while len(content) > _DETAIL_LIMIT and hits:
                hits.pop()
                omitted += 1
                content = json.dumps(hits, ensure_ascii=False)
            diagnostics = (
                (
                    Diagnostic(
                        "warning",
                        "truncated",
                        f"{omitted} hits omitted from this read",
                    ),
                )
                if omitted
                else ()
            )
            return ToolResult(
                content=content,
                meta={"title": f"search {search_id}", "target": search_id},
                diagnostics=diagnostics,
            )
        summary = state_summary(state)
        return ToolResult(
            content=json.dumps(summary, ensure_ascii=False),
            meta={"title": "writing state", "target": "state"},
        )

    return ToolSpec(
        name=GET_STATE,
        description=_GET_STATE_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "searchId": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Read one search's hits by id; omit for the summary."
                    ),
                }
            },
            "additionalProperties": False,
        },
        side_effect="read",
        timeout_s=5.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=24_000,
            max_chars_total=24_000,
        ),
        handler=get_state,
        argument_hint="",
    )


def project_web_search(layout: ProjectLayout) -> WebSearchFn:
    """The product's web search: the project's own acquire search, logged there."""

    def search(query: str) -> Sequence[CandidateCard]:
        from noteagent.acquire.run_search import run_web_search
        from noteagent.util.run_log import RunLogger

        return run_web_search(
            query,
            run_logger=RunLogger.for_layout(layout),
            caller="agent",
        ).cards

    return search


def web_search_tool(
    layout: ProjectLayout,
    search: WebSearchFn,
    *,
    clock: Clock | None = None,
    ids: Ids | None = None,
) -> ToolSpec:
    """The agent's web search: a status here, the hits in the state."""
    moment = clock or SystemClock()
    minted = ids or UuidIds()
    store = WritingStateStore(layout, clock=moment)

    def web_search(arguments, _context) -> ToolResult:
        query = " ".join(str(arguments["query"]).split())
        search_id = minted.new_id("search")
        created_at = iso_timestamp(moment)
        try:
            cards = list(search(query))[:20]
        except RunCancelled:
            raise
        except Exception as err:  # noqa: BLE001 - record it, then let the loop see it
            store.append_search(
                _search_row(
                    search_id=search_id,
                    query=query,
                    status="failed",
                    hits=[],
                    error=str(err)[:300],
                    created_at=created_at,
                )
            )
            raise

        hits = [
            {
                "id": minted.new_id("hit"),
                "title": card.title,
                "url": card.url,
                "snippet": card.snippet[:400],
                "rank": index + 1,
            }
            for index, card in enumerate(cards)
        ]
        store.append_search(
            _search_row(
                search_id=search_id,
                query=query,
                status="ok",
                hits=hits,
                error="",
                created_at=created_at,
            )
        )
        return ToolResult(
            content=json.dumps(
                {"status": "ok", "searchId": search_id, "count": len(hits)},
                ensure_ascii=False,
            ),
            meta={"title": query, "query": query, "hits": len(hits)},
        )

    return ToolSpec(
        name=WEB_SEARCH,
        description=_WEB_SEARCH_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "What to search the web for",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        side_effect="network",
        timeout_s=30.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=web_search,
        argument_hint="query",
    )


def _search_row(
    *,
    search_id: str,
    query: str,
    status: str,
    hits: list[dict[str, object]],
    error: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "id": search_id,
        "query": query,
        "status": status,
        "hits": hits,
        "error": error,
        "createdAt": created_at,
    }


__all__ = [
    "GET_STATE",
    "WEB_SEARCH",
    "WebSearchFn",
    "get_state_tool",
    "project_web_search",
    "web_search_tool",
]
