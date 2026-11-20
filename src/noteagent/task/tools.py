"""The writing-task tools: a search, a pick, a frame, the gate, and a read.

Their schemas are deliberately small. The model names a query, a card, a frame or
a view; the session's observed task revision travels on the op the wrapper
writes, never as an argument the model has to guess. A tool result carries the
task data and the revision for the model, and the same card data on the
observation channel for the reader; the task itself and its op log stay behind.
``compose_page`` is the gate: it can record a write request but never approves
one, and no tool can write an approval.
"""

from __future__ import annotations

from collections.abc import Mapping

from noteagent.agent.tools import (
    OutputLimits,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from noteagent.task.model import (
    MAX_CARD_SNIPPET,
    MAX_SPEC_SECTIONS,
    MAX_VIEW_BLOCKS,
    MAX_VIEW_CARDS,
    MAX_VIEW_MATERIALS,
    Card,
    Frame,
    material_summary,
    request_to_dict,
)
from noteagent.task.service import (
    ComposeOutcome,
    FrameOutcome,
    PickOutcome,
    SearchOutcome,
    TaskService,
)

WEB_SEARCH = "web_search"
PICK_CARD = "pick_card"
FRAME_BLOCKS = "frame_blocks"
COMPOSE_PAGE = "compose_page"
INSPECT = "inspect"

_WEB_SEARCH_DESCRIPTION = (
    "Search the public web for sources on a topic. The results become cards in "
    "the current writing task. Returns the cards and the task revision. "
    "One query per call. If the task changed since your last look, call inspect "
    "before searching again."
)
_PICK_CARD_DESCRIPTION = (
    "Turn one card of the current writing task into a material: fetch the card's "
    "public page and stage it. Returns the material and the task revision, or "
    "that the pick is still requested/running. Use inspect(\"materials\") for the "
    "block index afterwards. If the task changed since your last look, call "
    "inspect before picking."
)
_FRAME_BLOCKS_DESCRIPTION = (
    "Mark blocks of one material as the basis for writing: draw a frame. The "
    "blocks must be ids of that material, as inspect(\"materials\") shows them. "
    "Drawing a frame again replaces its blocks. Returns the frame and the task "
    "revision. A frame change invalidates any approval the person had given, so "
    "ask for approval again afterwards. If the task changed since your last "
    "look, call inspect before drawing."
)
_COMPOSE_PAGE_DESCRIPTION = (
    "Propose the page to write from one frame: a writing spec with a slug, a "
    "title, an action (create or update) and 1-12 sections, each with a title and "
    "1-20 block ids that frame covers. Without a person's matching approval it "
    "records the write request and returns needs_approval, with no page and no "
    "write op. With a matching approval it writes exactly one page through the "
    "core and returns written, skipped or failed with the page and the new "
    "revision; a page that takes longer than three minutes answers "
    "requested/running and the executor finishes it. A create never overwrites "
    "an existing page and an update never invents a missing one. The person "
    "approves or rejects the request in the confirmation panel; inspect(\"approval\") "
    "says what stands. A changed spec, frame or task revision needs a new "
    "approval, so call compose_page again with the same frame and spec only after "
    "the approval is visible. If the task changed since your last look, call "
    "inspect first."
)
_INSPECT_DESCRIPTION = (
    "Read a bounded view of the current writing task. `summary` says what the "
    "task is and how much it holds; `cards` lists the resource cards collected "
    "so far; `materials` lists the staged materials with an id/preview index of "
    "their blocks; `frames` lists the frames drawn so far, with their material, "
    "source and block ids/previews. Returns the view and the task revision. Call "
    "it after a conflict, before deciding what to do next."
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

PICK_CARD_PARAMETERS = {
    "type": "object",
    "properties": {
        "cardId": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": (
                "The id of a card the task holds, as inspect(cards) shows it"
            ),
        }
    },
    "required": ["cardId"],
    "additionalProperties": False,
}

FRAME_BLOCKS_PARAMETERS = {
    "type": "object",
    "properties": {
        "docId": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": (
                "The id of a material the task holds, as inspect(materials) "
                "shows it"
            ),
        },
        "blocks": {
            "type": "array",
            "items": {"type": "string", "pattern": r"\S"},
            "description": (
                "Block ids of that material, as inspect(materials) shows them. "
                "At least one is required."
            ),
        },
    },
    "required": ["docId", "blocks"],
    "additionalProperties": False,
}


COMPOSE_PAGE_PARAMETERS = {
    "type": "object",
    "properties": {
        "frameId": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": (
                "The id of a frame the task holds, as inspect(frames) shows it"
            ),
        },
        "spec": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The page's wiki name, as page links use it",
                },
                "title": {
                    "type": "string",
                    "description": "The page's title, at most 120 characters",
                },
                "action": {
                    "type": "string",
                    "enum": ["create", "update"],
                    "description": "Make a new page, or rewrite an existing one",
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "minLength": 1},
                            "blocks": {
                                "type": "array",
                                "items": {"type": "string", "pattern": r"\S"},
                            },
                        },
                        "required": ["title", "blocks"],
                    },
                    "description": (
                        "At most 12 sections; each names at least one block of "
                        "the frame and at most 20"
                    ),
                },
            },
            "required": ["slug", "title", "action", "sections"],
            # The schema lists the shape the model should send. Limits and
            # unknown fields are checked by the handler, so a spec that breaks
            # them is answered with the model-facing `bad_spec`, not the
            # registry's argument error.
        },
    },
    "required": ["frameId", "spec"],
    "additionalProperties": False,
}


INSPECT_PARAMETERS = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": ["summary", "cards", "materials", "frames", "approval"],
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
            name=PICK_CARD,
            description=_PICK_CARD_DESCRIPTION,
            parameters=PICK_CARD_PARAMETERS,
            side_effect="network",
            # A pick fetches a public page and stages it under its own bounded
            # wait (90s by default); the registry's wall clock must sit above it.
            timeout_s=max(120.0, service.pick_wait_s + 10.0),
            output_limits=OutputLimits(
                max_items=MAX_VIEW_MATERIALS,
                max_chars_per_item=MAX_CARD_SNIPPET + 400,
                max_chars_total=24_000,
            ),
            handler=_pick_handler(service),
            argument_hint="cardId",
        ),
        ToolSpec(
            name=FRAME_BLOCKS,
            description=_FRAME_BLOCKS_DESCRIPTION,
            parameters=FRAME_BLOCKS_PARAMETERS,
            # It writes the task file, not the web and not a wiki: the tool
            # contract has its own word for a local state write.
            side_effect="write",
            timeout_s=10.0,
            output_limits=OutputLimits(
                max_items=1,
                max_chars_per_item=MAX_CARD_SNIPPET + 2_000,
                max_chars_total=8_000,
            ),
            handler=_frame_handler(service),
            argument_hint="docId",
        ),
        ToolSpec(
            name=COMPOSE_PAGE,
            description=_COMPOSE_PAGE_DESCRIPTION,
            parameters=COMPOSE_PAGE_PARAMETERS,
            # It writes local state and, once a person approves, one wiki page
            # through the core. The contract's `write` means a local write the
            # core performs, not a fetch and not a page the model writes itself;
            # the LLM call inside compose is the core's, not this tool's.
            side_effect="write",
            # The handler has its own bounded wait (180s by default); the
            # registry's wall clock must sit above it, or a slow compose would
            # be reported as a registry timeout instead of requested/running.
            timeout_s=max(240.0, service.compose_wait_s + 30.0),
            output_limits=OutputLimits(
                max_items=MAX_SPEC_SECTIONS,
                max_chars_per_item=MAX_CARD_SNIPPET + 400,
                max_chars_total=8_000,
            ),
            handler=_compose_handler(service),
            argument_hint="frameId",
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


def _pick_handler(service: TaskService):
    def pick(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        card_id = str(arguments["cardId"]).strip()
        outcome = service.pick(
            card_id,
            session_id=context.session_id,
            cancel=context.cancel,
        )
        if outcome.status == "done" and outcome.material:
            summary = material_summary(outcome.material)
            return ToolResult(
                content=_render_picked(outcome, summary),
                meta={
                    "title": summary["title"] or card_id,
                    "url": summary["url"],
                    "fidelity": summary["fidelity"],
                    # The chat card reads the same shape as inspect("materials"),
                    # minus the block index: the card is a summary, not a reader.
                    "materials": [summary],
                    "rev": outcome.rev,
                },
            )
        return ToolResult(
            content=_render_pick_running(outcome),
            meta={
                "title": card_id,
                "status": outcome.status,
                "rev": outcome.rev,
            },
        )

    return pick


def _frame_handler(service: TaskService):
    def frame(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        doc_id = str(arguments["docId"]).strip()
        raw = arguments.get("blocks")
        blocks = list(raw) if isinstance(raw, list) else []
        outcome = service.frame(
            doc_id,
            blocks,
            session_id=context.session_id,
        )
        return ToolResult(
            content=_render_frame(outcome),
            meta={
                "title": outcome.doc_id,
                "rev": outcome.rev,
                "frames": [_frame_summary(outcome.frame)],
            },
        )

    return frame


def _compose_handler(service: TaskService):
    def compose(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        frame_id = str(arguments["frameId"]).strip()
        raw = arguments.get("spec")
        spec = raw if isinstance(raw, Mapping) else {}
        outcome = service.compose(
            frame_id,
            spec,
            session_id=context.session_id,
            cancel=context.cancel,
        )
        meta: dict[str, object] = {
            "title": _spec_title(outcome.spec, fallback=frame_id),
            "rev": outcome.rev,
            "status": outcome.status,
        }
        if outcome.request is not None:
            # The confirmation card and the task panel read the same request,
            # so the card carries the canonical spec the task stored.
            meta["request"] = request_to_dict(outcome.request)
        if outcome.result is not None:
            result = outcome.result
            # The card gets the page's identity and fate; the diff and the
            # effect ids stay in the task, which is where the panel reads them.
            meta["path"] = _text(result.get("page"))
            meta["slug"] = _text(result.get("slug"))
            meta["action"] = _text(result.get("action"))
            meta["wrote_wiki"] = outcome.status == "written"
        return ToolResult(content=_render_compose(outcome), meta=meta)

    return compose


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
        if view == "materials":
            materials = payload.get("materials")
            rows = (
                [item for item in materials if isinstance(item, Mapping)]
                if isinstance(materials, list)
                else []
            )
            meta["materials"] = [material_summary(item) for item in rows]
            meta["hits"] = len(rows)
        if view == "frames":
            frames = payload.get("frames")
            rows = (
                [dict(item) for item in frames if isinstance(item, Mapping)]
                if isinstance(frames, list)
                else []
            )
            # The view is already bounded; the card reads the same shape.
            meta["frames"] = rows
            meta["hits"] = len(rows)
        if view == "approval":
            request = payload.get("request")
            if isinstance(request, Mapping):
                # The same request the task panel shows; a chat card can render
                # the proposal from the observation alone.
                meta["request"] = dict(request)
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


def _render_frame(outcome: FrameOutcome) -> str:
    frame = outcome.frame
    shown = frame.blocks[:MAX_VIEW_BLOCKS]
    lines = [
        f"Task rev {outcome.rev}. Frame [{frame.id}] on material "
        f'"{frame.doc_id}" now covers {len(frame.blocks)} block(s), '
        f"source={frame.source}:",
    ]
    lines.extend(f"   - [{block}]" for block in shown)
    if len(frame.blocks) > len(shown):
        lines.append(
            f"{len(frame.blocks) - len(shown)} more block(s) omitted from this "
            "result; call inspect(\"frames\") to read the frame."
        )
    if outcome.approval_cleared:
        lines.append(
            "The previous approval was cleared: this frame changed the writing "
            "basis, so the person must approve again before compose_page."
        )
    return "\n".join(lines)


def _render_compose(outcome: ComposeOutcome) -> str:
    spec = outcome.spec
    slug = spec.get("slug", "")
    title = spec.get("title", "")
    action = spec.get("action", "")
    raw_sections = spec.get("sections")
    sections = (
        [item for item in raw_sections if isinstance(item, Mapping)]
        if isinstance(raw_sections, list)
        else []
    )
    blocks = 0
    for section in sections:
        raw = section.get("blocks")
        blocks += len(raw) if isinstance(raw, list) else 0
    if outcome.status in ("written", "skipped", "failed"):
        result = outcome.result or {}
        page = str(result.get("page") or "")
        summary = str(result.get("summary") or "")
        reason = str(result.get("reason") or "")
        if outcome.status == "written":
            return (
                f"Task rev {outcome.rev}. compose_page written: {page} "
                f'("{title}", {slug}, {action}) from frame {outcome.frame_id} '
                f"with {len(sections)} section(s) and {blocks} block(s). "
                f"{summary} The page is source-grounded: every section carries "
                "the marker of the material it was composed from. The write "
                "result and its diff are recorded in the writing task."
            )
        if outcome.status == "skipped":
            return (
                f"Task rev {outcome.rev}. compose_page skipped writing {page} "
                f'("{title}", {slug}, {action}): {reason or summary}. '
                "Nothing was overwritten. Read the page and the task before "
                "deciding whether a different spec is right."
            )
        return (
            f"Task rev {outcome.rev}. compose_page failed to write {page} "
            f'("{title}", {slug}, {action}): {reason or summary}. The task '
            "records the failure; do not retry blindly, call inspect first."
        )
    if outcome.status in ("requested", "running"):
        return (
            f'Task rev {outcome.rev}. compose_page is still {outcome.status} '
            f'for page "{title}" ({slug}, {action}) from frame '
            f"{outcome.frame_id}. The executor is still writing it; call "
            'inspect("summary") later; do not compose it again.'
        )
    lines = [
        f'Task rev {outcome.rev}. compose_page needs_approval: writing page '
        f'"{title}" ({slug}, {action}) from frame {outcome.frame_id} with '
        f"{len(sections)} section(s) and {blocks} block(s) has no person's "
        "approval yet.",
    ]
    if outcome.approval_cleared:
        lines.append(
            "The previous approval did not match this request and was cleared; "
            "it must be approved again."
        )
    lines.extend(
        "   - {title} ({count} block(s))".format(
            title=section.get("title", ""),
            count=len(section.get("blocks"))
            if isinstance(section.get("blocks"), list)
            else 0,
        )
        for section in sections[:MAX_SPEC_SECTIONS]
    )
    lines.append(
        "The request is recorded in the writing task; no wiki page was written "
        "and no write op was started. Ask the person to approve or reject it in "
        'the confirmation panel, then call inspect("approval") to see the '
        "decision. Do not call compose_page with a different spec to get around "
        "the gate."
    )
    return "\n".join(lines)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _spec_title(spec: Mapping[str, object], *, fallback: str = "") -> str:
    title = spec.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    slug = spec.get("slug")
    if isinstance(slug, str) and slug.strip():
        return slug.strip()
    return fallback


def _frame_summary(frame: Frame) -> dict[str, object]:
    """A frame as an event card reads it: identity, source and size."""
    return {
        "id": frame.id,
        "docId": frame.doc_id,
        "source": frame.source,
        "blocks": [_block_ref(block) for block in frame.blocks[:MAX_VIEW_BLOCKS]],
        "blockCount": len(frame.blocks),
    }


def _block_ref(block_id: str) -> dict[str, object]:
    return {"id": block_id}


def _render_picked(
    outcome: PickOutcome, material: Mapping[str, object]
) -> str:
    return (
        f"Task rev {outcome.rev}. Card {outcome.card_id} is now the material "
        f'[{material.get("id", "")}] "{material.get("title", "")}" '
        f'(fidelity={material.get("fidelity", "")}, '
        f'state={material.get("state", "")}, '
        f'{material.get("blockCount", 0)} blocks). '
        'Call inspect("materials") for the block index before drawing frames.'
    )


def _render_pick_running(outcome: PickOutcome) -> str:
    return (
        f'Picking card "{outcome.card_id}" is still {outcome.status} '
        f"(task rev {outcome.rev}). Call inspect(\"materials\") later; do not "
        "pick it again."
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
        approval = payload.get("approval")
        if isinstance(approval, Mapping):
            lines.append(
                "approval: frame {frame} by {by} at rev {rev}.".format(
                    frame=approval.get("frameId", ""),
                    by=approval.get("by", ""),
                    rev=approval.get("rev", ""),
                )
            )
        else:
            lines.append("approval: none.")
        return "\n".join(lines)
    if view == "materials":
        return _render_materials(rev, payload)
    if view == "frames":
        return _render_frames(rev, payload)
    if view == "approval":
        return _render_approval(rev, payload)
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


#: How much of a materials view the model's content may carry. The registry
#: caps the total as well; this keeps the block index from crowding out the
#: material headers when there are many blocks.
_MATERIALS_RENDER_BUDGET = 20_000


def _render_materials(rev: object, payload: Mapping[str, object]) -> str:
    raw = payload.get("materials")
    rows = (
        [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )
    if not rows:
        return f"Task rev {rev}. No materials yet."
    omitted = payload.get("omitted")
    header = f"Task rev {rev}. {len(rows)} material(s)"
    if isinstance(omitted, int) and omitted > 0:
        header += f" ({omitted} more omitted)"
    lines = [header + ":"]
    for index, material in enumerate(rows, 1):
        blocks = material.get("blocks")
        shown = (
            [item for item in blocks if isinstance(item, Mapping)]
            if isinstance(blocks, list)
            else []
        )
        lines.append(
            '{index}. [{id}] "{title}" — fidelity={fidelity} state={state} '
            "{count} block(s), {shown} in this index:".format(
                index=index,
                id=material.get("id", ""),
                title=material.get("title", ""),
                fidelity=material.get("fidelity", ""),
                state=material.get("state", ""),
                count=material.get("blockCount", 0),
                shown=len(shown),
            )
        )
        for block in shown[:MAX_VIEW_BLOCKS]:
            lines.append(
                "   - [{id}] {preview}".format(
                    id=block.get("id", ""),
                    preview=block.get("preview", ""),
                )
            )
        more = material.get("blocksOmitted")
        if isinstance(more, int) and more > 0:
            lines.append(f"   ({more} more block(s) beyond this index)")
        if sum(len(line) + 1 for line in lines) >= _MATERIALS_RENDER_BUDGET:
            lines.append("The rest of the materials view is truncated.")
            break
    return "\n".join(lines)


def _render_approval(rev: object, payload: Mapping[str, object]) -> str:
    lines = [
        f"Task rev {rev}. status={payload.get('status', '')}."
    ]
    approval = payload.get("approval")
    request = payload.get("request")
    if isinstance(approval, Mapping):
        lines.append(
            "approval: "
            + _spec_sentence(approval)
            + " by {by} at rev {rev}.".format(
                by=approval.get("by", ""), rev=approval.get("rev", "")
            )
        )
    elif payload.get("approvalStale"):
        lines.append(
            "approval: none. The stored approval is stale: it was bound to "
            "another revision or its frame is gone, so it no longer applies; a "
            "new approval is needed."
        )
    else:
        lines.append("approval: none.")
    if isinstance(request, Mapping):
        lines.append(
            "request: "
            + _spec_sentence(request)
            + " recorded at rev {rev}.".format(rev=request.get("rev", ""))
        )
        raw_sections = _request_sections(request)
        lines.extend(
            "   - {title} ({count} block(s))".format(
                title=section.get("title", ""),
                count=len(section.get("blocks"))
                if isinstance(section.get("blocks"), list)
                else 0,
            )
            for section in raw_sections[:MAX_SPEC_SECTIONS]
        )
        lines.append(
            "No person has approved this request yet; only a person can, in "
            "the confirmation panel."
        )
    elif payload.get("requestStale"):
        lines.append(
            "No usable write request is pending; the previous one was bound to "
            "another revision. compose_page again to propose it anew."
        )
    elif isinstance(approval, Mapping):
        lines.append(
            "No write request is pending; the approval above still covers a "
            "compose_page with the same frame, spec and rev."
        )
    else:
        lines.append("No write request is pending.")
    return "\n".join(lines)


def _spec_sentence(row: Mapping[str, object]) -> str:
    spec = row.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    sections = _request_sections(row)
    blocks = 0
    for section in sections:
        raw = section.get("blocks")
        blocks += len(raw) if isinstance(raw, list) else 0
    return (
        f'frame {row.get("frameId", "")}: page "{spec.get("title", "")}" '
        f'({spec.get("slug", "")}, {spec.get("action", "")}), '
        f"{len(sections)} section(s), {blocks} block(s)"
    )


def _request_sections(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    spec = row.get("spec")
    spec = spec if isinstance(spec, Mapping) else {}
    raw = spec.get("sections")
    return (
        [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )


def _render_frames(rev: object, payload: Mapping[str, object]) -> str:
    raw = payload.get("frames")
    rows = (
        [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )
    if not rows:
        return f"Task rev {rev}. No frames yet."
    omitted = payload.get("omitted")
    header = f"Task rev {rev}. {len(rows)} frame(s)"
    if isinstance(omitted, int) and omitted > 0:
        header += f" ({omitted} more omitted)"
    lines = [header + ":"]
    for index, frame in enumerate(rows, 1):
        blocks = frame.get("blocks")
        shown = (
            [item for item in blocks if isinstance(item, Mapping)]
            if isinstance(blocks, list)
            else []
        )
        lines.append(
            '{index}. [{id}] on "{doc}" source={source} '
            "{count} block(s):".format(
                index=index,
                id=frame.get("id", ""),
                doc=frame.get("docId", ""),
                source=frame.get("source", ""),
                count=frame.get("blockCount", 0),
            )
        )
        for block in shown:
            lines.append(
                "   - [{id}] {preview}".format(
                    id=block.get("id", ""),
                    preview=block.get("preview", ""),
                )
            )
        more = frame.get("blocksOmitted")
        if isinstance(more, int) and more > 0:
            lines.append(f"   ({more} more block(s) beyond this view)")
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
    "COMPOSE_PAGE",
    "COMPOSE_PAGE_PARAMETERS",
    "FRAME_BLOCKS",
    "FRAME_BLOCKS_PARAMETERS",
    "INSPECT",
    "INSPECT_PARAMETERS",
    "PICK_CARD",
    "PICK_CARD_PARAMETERS",
    "WEB_SEARCH",
    "WEB_SEARCH_PARAMETERS",
    "task_tools",
]
