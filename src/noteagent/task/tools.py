"""The writing-task tools: one search, and one bounded read.

Their schemas are deliberately small. The model names a query or a view; the
session's observed task revision travels on the op the wrapper writes, never as
an argument the model has to guess. A tool result carries cards and the revision
for the model, and the same card data on the observation channel for the reader;
the task itself, its ops and its other fields stay behind.
"""

from __future__ import annotations

from collections.abc import Mapping

from noteagent.agent.tools import (
    OutputLimits,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from noteagent.task.model import MAX_CARD_SNIPPET, MAX_VIEW_CARDS, Card
from noteagent.task.service import SearchOutcome, TaskService

WEB_SEARCH = "web_search"
INSPECT = "inspect"

_WEB_SEARCH_DESCRIPTION = (
    "Search the public web for sources on a topic. The results become cards in "
    "the current writing task. Returns the cards and the task revision. "
    "One query per call. If the task changed since your last look, call inspect "
    "before searching again."
)
_INSPECT_DESCRIPTION = (
    "Read a bounded view of the current writing task. `summary` says what the "
    "task is and how much it holds; `cards` lists the resource cards collected "
    "so far. Returns the view and the task revision. Call it after a conflict, "
    "before deciding what to do next."
)

WEB_SEARCH_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": "What to search the public web for",
        }
    },
    "required": ["query"],
    "additionalProperties": False,
}

INSPECT_PARAMETERS = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": ["summary", "cards"],
            "description": "Which bounded view of the writing task to read",
        }
    },
    "required": ["view"],
    "additionalProperties": False,
}


def task_tools(service: TaskService) -> tuple[ToolSpec, ...]:
    """The writing task's tools, for one project's run."""
    return (
        ToolSpec(
            name=WEB_SEARCH,
            description=_WEB_SEARCH_DESCRIPTION,
            parameters=WEB_SEARCH_PARAMETERS,
            side_effect="network",
            # The handler has its own bounded wait (15s by default); the
            # registry's wall clock must sit above it, or a slow search would be
            # reported as a registry timeout instead of requested/running.
            timeout_s=max(60.0, service.wait_s + 10.0),
            output_limits=OutputLimits(
                max_items=MAX_VIEW_CARDS,
                max_chars_per_item=MAX_CARD_SNIPPET + 400,
                max_chars_total=24_000,
            ),
            handler=_search_handler(service),
            argument_hint="query",
        ),
        ToolSpec(
            name=INSPECT,
            description=_INSPECT_DESCRIPTION,
            parameters=INSPECT_PARAMETERS,
            side_effect="read",
            timeout_s=10.0,
            output_limits=OutputLimits(
                max_items=MAX_VIEW_CARDS,
                max_chars_per_item=MAX_CARD_SNIPPET + 400,
                max_chars_total=24_000,
            ),
            handler=_inspect_handler(service),
            argument_hint="view",
        ),
    )


def _search_handler(service: TaskService):
    def search(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        query = str(arguments["query"]).strip()
        outcome = service.search(
            query,
            session_id=context.session_id,
            cancel=context.cancel,
        )
        if outcome.status == "done":
            shown = outcome.cards[:MAX_VIEW_CARDS]
            return ToolResult(
                content=_render_cards(outcome, shown),
                meta={
                    "title": outcome.query,
                    "query": outcome.query,
                    "hits": len(outcome.cards),
                    "rev": outcome.rev,
                    "cards": [_card_payload(card) for card in shown],
                },
            )
        return ToolResult(
            content=_render_running(outcome),
            meta={
                "title": outcome.query,
                "query": outcome.query,
                "status": outcome.status,
                "rev": outcome.rev,
            },
        )

    return search


def _inspect_handler(service: TaskService):
    def inspect(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        view = str(arguments["view"])
        payload, rev = service.inspect(view, session_id=context.session_id)
        meta: dict[str, object] = {
            "title": f"writing task {view}",
            "rev": rev,
        }
        if view == "cards":
            cards = payload.get("cards")
            meta["hits"] = len(cards) if isinstance(cards, list) else 0
        return ToolResult(content=_render_view(view, payload), meta=meta)

    return inspect


def _render_cards(outcome: SearchOutcome, shown: tuple[Card, ...]) -> str:
    count = len(outcome.cards)
    if count == 0:
        return (
            f'Task rev {outcome.rev}. The search "{outcome.query}" '
            "returned no cards."
        )
    lines = [
        f'Task rev {outcome.rev}. The search "{outcome.query}" '
        f"returned {count} card(s):"
    ]
    lines.extend(_render_card(index, card) for index, card in enumerate(shown, 1))
    omitted = count - len(shown)
    if omitted > 0:
        lines.append(f"{omitted} more card(s) omitted from this result.")
    return "\n".join(lines)


def _render_running(outcome: SearchOutcome) -> str:
    return (
        f'The search "{outcome.query}" is still {outcome.status} '
        f"(task rev {outcome.rev}). Call inspect(\"cards\") later; do not start "
        "it again."
    )


def _render_card(index: int, card: Card) -> str:
    label = card.site or _host(card.url) or "web"
    lines = [f"{index}. [{card.id}] [{label}] {card.title}", f"   {card.url}"]
    snippet = card.snippet.strip()
    if snippet:
        lines.append("   " + snippet[:MAX_CARD_SNIPPET])
    return "\n".join(lines)


def _render_view(view: str, payload: Mapping[str, object]) -> str:
    rev = payload.get("rev")
    if view == "summary":
        counts = payload.get("counts")
        counts = counts if isinstance(counts, Mapping) else {}
        intent = str(payload.get("intent") or "").strip()
        lines = [
            f"Task rev {rev}. id={payload.get('id')} kind={payload.get('kind')} "
            f"status={payload.get('status')}.",
            "cards={cards} materials={materials} frames={frames}.".format(
                cards=counts.get("cards", 0),
                materials=counts.get("materials", 0),
                frames=counts.get("frames", 0),
            ),
        ]
        if intent:
            lines.append(f"intent: {intent}")
        return "\n".join(lines)
    cards = payload.get("cards")
    shown = [item for item in cards if isinstance(item, Mapping)] if isinstance(cards, list) else []
    if not shown:
        return f"Task rev {rev}. No cards yet."
    omitted = payload.get("omitted")
    header = f"Task rev {rev}. {len(shown)} card(s)"
    if isinstance(omitted, int) and omitted > 0:
        header += f" ({omitted} more omitted)"
    lines = [header + ":"]
    for index, item in enumerate(shown, 1):
        lines.append(
            "{index}. [{id}] [{site}] {title}\n   {url}".format(
                index=index,
                id=item.get("id", ""),
                site=item.get("site", ""),
                title=item.get("title", ""),
                url=item.get("url", ""),
            )
        )
    return "\n".join(lines)


def _card_payload(card: Card) -> dict[str, object]:
    return {
        "id": card.id,
        "title": card.title,
        "url": card.url,
        "snippet": card.snippet[:MAX_CARD_SNIPPET],
        "site": card.site,
    }


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


__all__ = [
    "INSPECT",
    "INSPECT_PARAMETERS",
    "WEB_SEARCH",
    "WEB_SEARCH_PARAMETERS",
    "task_tools",
]
