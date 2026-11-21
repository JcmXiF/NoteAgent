"""The writing state as agent tools: search, page selection, perception."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import NoReturn

from noteagent.acquire.derive import blocks_for_capture
from noteagent.acquire.quality import FIDELITY_FAILED
from noteagent.acquire.tools.web_fetch import LOGIN_WALL_STATUSES
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.acquire.write_raw import (
    capture_kind,
    capture_material_bytes,
    raw_relative_for_capture,
)
from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds, iso_timestamp
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import (
    Diagnostic,
    OutputLimits,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from noteagent.ingest.clips import MAX_BLOCKS_PER_COLLECTION
from noteagent.pipeline.captures import CaptureInbox
from noteagent.project.layout import ProjectLayout
from noteagent.writing.publish import publish_page
from noteagent.writing.state import (
    WRITING_ID,
    WritingState,
    WritingStateStore,
    draft_page_for,
    draft_pages,
    draft_section_for,
    draft_sections,
    row_version,
    state_summary,
)

GET_STATE = "get_state"
WEB_SEARCH = "web_search"
SELECT_PAGE = "select_page"
SELECT_FRAME = "select_frame"
COMPOSE_WIKI = "compose_wiki"
WRITE_WIKI = "write_wiki"

WebSearchFn = Callable[[str], Sequence[CandidateCard]]
WebFetchFn = Callable[[str], Capture]

#: The handler's own budget for one detail read; the registry would otherwise
#: slice the JSON at its hard cap and leave the model with a broken object.
_DETAIL_LIMIT = 20_000

_GET_STATE_DESCRIPTION = (
    "Read this project's writing state. With no arguments it returns the summary: "
    "stateVersion, the current writer, the counts of searches, selected pages, "
    "frames and the draft, the latest search, and attention. With searchId it "
    "returns that search's hits; with pageId it returns that page's blocks; with "
    "framesFor it returns that page's frames; with section:'frames' it returns "
    "every frame; with section:'draft' it returns the draft page and section "
    "list; with section:'section' plus wikiFile/sectionId it returns that "
    "section's body and citations. Each detail is truncated with a diagnostic "
    "when it is too large. The state is the only source of truth for those "
    "areas; read it before acting on them."
)

_WEB_SEARCH_DESCRIPTION = (
    "Search the web for a query. Returns a status, a searchId and the hit count; "
    "the hits themselves are stored in the writing state. Read them with "
    "get_state({searchId}). A failed search is recorded in the state and returned "
    "as an error observation."
)

_SELECT_PAGE_DESCRIPTION = (
    "Fetch the page behind one search hit and put its blocks in the writing state. "
    "Returns a status, a pageId and the block count; read the blocks with "
    "get_state({pageId}). A failed fetch is recorded in the state and returned as "
    "an error observation. The same URL is not fetched twice unless `refresh` is "
    "true: a ready page is fetched again, its version moves, and sections citing "
    "it become stale."
)

_SELECT_FRAME_DESCRIPTION = (
    "Select a frame as a list of block ids on one selected page. Returns a frame "
    "id, version and block count; the frames are in the writing state. Read them "
    "with get_state({framesFor: pageId}). Empty, unknown or too many blocks are "
    "errors."
)

_COMPOSE_WIKI_DESCRIPTION = (
    "Compose structured sections into the wiki draft. Each section has a stable "
    "id, heading, Markdown body and frame ids as citations; an existing id is "
    "replaced in place, a new id is inserted after `after` or appended. A "
    "section a person edited is refused unless `overwrite` is true after you "
    "read it again. Pass the draft page's current version as baseDraftVersion "
    "(0 for a new page). The draft is state only; publishing is a later step. "
    "Read the page list with get_state({section: 'draft'}) and one section "
    "with section: 'section'."
)

_WRITE_WIKI_DESCRIPTION = (
    "Publish one draft page to the wiki. The page is assembled from the state "
    "draft; no content is accepted. It writes through the page-write seam and "
    "returns the committed effect ids. The same draftVersion is published once; "
    "a newer draft has a higher version."
)


def _take_lease(
    layout: ProjectLayout,
    context: ToolContext,
    moment: Clock,
    kind: str = "agent",
) -> None:
    """A run's first state write takes the lease; its runtime releases it."""
    WritingStateStore(layout, clock=moment).ensure_writer(
        kind, context.session_id
    )


def get_state_tool(layout: ProjectLayout) -> ToolSpec:
    """The one perception tool the writing workflow starts from."""

    def get_state(arguments, _context) -> ToolResult:
        search_id = str(arguments.get("searchId") or "").strip()
        page_id = str(arguments.get("pageId") or "").strip()
        frames_for = str(arguments.get("framesFor") or "").strip()
        section = str(arguments.get("section") or "").strip()
        store = WritingStateStore(layout)
        state = store.load()
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
            content, diagnostics = _bounded(hits)
            return ToolResult(
                content=content,
                meta={"title": f"search {search_id}", "target": search_id},
                diagnostics=diagnostics,
            )
        if page_id:
            row = next(
                (
                    item
                    for item in state.selected_pages
                    if str(item.get("id")) == page_id
                ),
                None,
            )
            if row is None:
                raise ValueError(f"no such page: {page_id}")
            content, diagnostics = _bounded(
                store.load_page_blocks(page_id)
            )
            return ToolResult(
                content=content,
                meta={"title": f"page {page_id}", "target": page_id},
                diagnostics=diagnostics,
            )
        if frames_for:
            rows = [
                dict(item)
                for item in state.frames
                if str(item.get("pageId")) == frames_for
            ]
            content, diagnostics = _bounded(rows)
            return ToolResult(
                content=content,
                meta={
                    "title": f"frames for {frames_for}",
                    "target": frames_for,
                },
                diagnostics=diagnostics,
            )
        if section == "frames":
            rows = [dict(item) for item in state.frames]
            content, diagnostics = _bounded(rows)
            return ToolResult(
                content=content,
                meta={"title": "frames", "target": "frames"},
                diagnostics=diagnostics,
            )
        if section == "draft":
            pages = [
                {
                    "wikiFile": str(page.get("wikiFile") or ""),
                    "title": str(page.get("title") or ""),
                    "version": row_version(page),
                    "status": str(page.get("status") or "draft"),
                    "writtenAt": str(page.get("writtenAt") or ""),
                    "effectIds": [
                        str(item)
                        for item in page.get("effectIds") or []
                    ],
                    "sections": [
                        {
                            "id": str(item.get("id") or ""),
                            "heading": str(item.get("heading") or ""),
                            "version": row_version(item),
                            "stale": bool(item.get("stale")),
                            "staleReason": str(
                                item.get("staleReason") or ""
                            ),
                            "humanEdited": bool(
                                item.get("humanEdited")
                            ),
                        }
                        for item in draft_sections(page)
                    ],
                }
                for page in draft_pages(state.draft)
            ]
            content, diagnostics = _bounded(pages)
            return ToolResult(
                content=content,
                meta={"title": "draft", "target": "draft"},
                diagnostics=diagnostics,
            )
        if section == "section":
            wiki_file = str(arguments.get("wikiFile") or "").strip()
            section_id = str(arguments.get("sectionId") or "").strip()
            page = draft_page_for(state.draft, wiki_file)
            if page is None:
                raise ValueError(f"no such draft page: {wiki_file}")
            item = draft_section_for(page, section_id)
            if item is None:
                raise ValueError(f"no such section: {section_id}")
            body = str(item.get("body") or "")
            truncated = False
            payload = {
                "wikiFile": wiki_file,
                "id": str(item.get("id") or ""),
                "heading": str(item.get("heading") or ""),
                "body": body,
                "citations": [
                    dict(citation)
                    for citation in item.get("citations") or []
                ],
                "version": row_version(item),
                "stale": bool(item.get("stale")),
                "staleReason": str(
                    item.get("staleReason") or ""
                ),
                "humanEdited": bool(item.get("humanEdited")),
            }
            content = json.dumps(payload, ensure_ascii=False)
            while body and len(content) > _DETAIL_LIMIT:
                truncated = True
                body = body[: len(body) // 2]
                payload["body"] = body
                content = json.dumps(payload, ensure_ascii=False)
            diagnostics = (
                (
                    Diagnostic(
                        "warning",
                        "truncated",
                        "the section body was truncated",
                    ),
                )
                if truncated
                else ()
            )
            return ToolResult(
                content=content,
                meta={
                    "title": f"{wiki_file}#{section_id}",
                    "target": section_id,
                },
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
                },
                "pageId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Read one selected page's blocks by id.",
                },
                "framesFor": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Read the frames of one selected page by id.",
                },
                "section": {
                    "type": "string",
                    "enum": ["frames", "draft", "section"],
                    "description": (
                        "Read every frame, the draft page list, or one "
                        "draft section."
                    ),
                },
                "wikiFile": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The draft page, for section: 'section'.",
                },
                "sectionId": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The section id, for section: 'section'.",
                },
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


def project_fetch(layout: ProjectLayout) -> WebFetchFn:
    """The product's web fetch: the project's acquire fetch, logged there."""

    def fetch(url: str) -> Capture:
        from noteagent.acquire.run_fetch import run_web_fetch
        from noteagent.util.run_log import RunLogger

        return run_web_fetch(
            url,
            run_logger=RunLogger.for_layout(layout),
            caller="agent",
        )

    return fetch


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

    def web_search(arguments, context) -> ToolResult:
        _take_lease(layout, context, moment)
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


def select_page_tool(
    layout: ProjectLayout,
    fetch: WebFetchFn,
    *,
    clock: Clock | None = None,
    ids: Ids | None = None,
) -> ToolSpec:
    """The agent's page selection: a status here, the blocks in the state."""
    moment = clock or SystemClock()
    minted = ids or UuidIds()
    store = WritingStateStore(layout, clock=moment)

    def select_page(arguments, context) -> ToolResult:
        _take_lease(layout, context, moment)
        hit_id = str(arguments["hitId"]).strip()
        refresh = bool(arguments.get("refresh", False))
        state = store.load()
        hit = _find_hit(state, hit_id)
        if hit is None:
            raise ValueError(f"no such hit: {hit_id}")
        url = str(hit.get("url") or "").strip()
        if not url:
            raise ValueError(f"the hit has no URL: {hit_id}")
        title = str(hit.get("title") or url)

        existing = next(
            (
                page
                for page in state.selected_pages
                if str(page.get("url")) == url
                and str(page.get("status") or "")
                in {"fetching", "ready"}
            ),
            None,
        )
        if existing is not None and (
            not refresh
            or str(existing.get("status") or "") == "fetching"
        ):
            return ToolResult(
                content=json.dumps(
                    {
                        "status": "already_selected",
                        "pageId": str(existing.get("id") or ""),
                        "blockCount": int(
                            existing.get("blockCount") or 0
                        ),
                    },
                    ensure_ascii=False,
                ),
                meta={"title": title, "url": url},
            )

        failed = next(
            (
                page
                for page in state.selected_pages
                if str(page.get("url")) == url
                and str(page.get("status") or "") == "failed"
            ),
            None,
        )
        previous = failed if failed is not None else existing
        page_id = (
            str(previous.get("id") or "")
            if previous is not None
            else minted.new_id("page")
        )
        base_version = (
            row_version(previous) if previous is not None else 0
        )
        fetching = _page_row(
            page_id=page_id,
            hit_id=hit_id,
            url=url,
            title=title,
            status="fetching",
            block_count=0,
            version=base_version + 1,
        )
        if previous is not None:
            store.update_selected_page(page_id, fetching)
        else:
            store.append_selected_page(fetching)

        def fail(
            message: str,
            kind: str,
            error: Exception,
            *,
            page_title: str | None = None,
        ) -> NoReturn:
            store.update_selected_page(
                page_id,
                _failed_page(
                    fetching,
                    message,
                    kind,
                    iso_timestamp(moment),
                    title=page_title,
                ),
            )
            raise error

        try:
            capture = fetch(url)
        except RunCancelled as err:
            fail("cancelled", "fetch_failed", err)
        except Exception as err:  # noqa: BLE001 - record it, then let the loop see it
            fail(str(err)[:300], "fetch_failed", err)

        reasons = " ".join(capture.reasons)
        if capture.http_status in LOGIN_WALL_STATUSES:
            message = reasons or f"HTTP {capture.http_status}"
            try:
                CaptureInbox(layout.capture_inbox).add(
                    url=url,
                    title=capture.title or title,
                    reason=message,
                    http_status=capture.http_status,
                    at=iso_timestamp(moment),
                )
            except (OSError, ValueError):
                # The state row still says what happened; the inbox is best effort.
                pass
            fail(
                message,
                "login_wall",
                ValueError(f"login wall: {message}"),
                page_title=capture.title or title,
            )
        if capture.fidelity == FIDELITY_FAILED:
            message = reasons or "the capture failed its quality gate"
            fail(
                message,
                "fetch_failed",
                ValueError(message),
                page_title=capture.title or title,
            )
        blocks = blocks_for_capture(capture)
        if not blocks:
            message = reasons or "the page has no blocks"
            fail(
                message,
                "fetch_failed",
                ValueError(message),
                page_title=capture.title or title,
            )

        page_title = capture.title or title
        material = raw_relative_for_capture(capture)
        try:
            store.save_page_material(
                page_id,
                capture_material_bytes(
                    capture,
                    capture_kind(capture),
                    iso_timestamp(moment),
                ),
                material,
            )
            store.save_page_blocks(page_id, blocks)
        except (OSError, ValueError) as err:
            fail(str(err)[:300], "fetch_failed", err, page_title=page_title)
        ready = _page_row(
            page_id=page_id,
            hit_id=hit_id,
            url=url,
            title=page_title,
            status="ready",
            block_count=len(blocks),
            version=base_version + 2,
            material=material,
            fetched_at=iso_timestamp(moment),
        )
        store.update_selected_page(page_id, ready)
        return ToolResult(
            content=json.dumps(
                {
                    "status": "ok",
                    "pageId": page_id,
                    "blockCount": len(blocks),
                },
                ensure_ascii=False,
            ),
            meta={"title": page_title, "url": url, "hits": len(blocks)},
        )

    return ToolSpec(
        name=SELECT_PAGE,
        description=_SELECT_PAGE_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "hitId": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "The hit id from a web_search",
                },
                "refresh": {
                    "type": "boolean",
                    "description": (
                        "Fetch a ready page again; its version moves and "
                        "sections citing it go stale"
                    ),
                },
            },
            "required": ["hitId"],
            "additionalProperties": False,
        },
        side_effect="network",
        timeout_s=60.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=select_page,
        argument_hint="hitId",
    )


def select_frame_tool(
    layout: ProjectLayout,
    *,
    clock: Clock | None = None,
    ids: Ids | None = None,
    writer_kind: str = "agent",
) -> ToolSpec:
    """The agent's frame selection: a frame id and its blocks in the state."""
    moment = clock or SystemClock()
    minted = ids or UuidIds()
    store = WritingStateStore(layout, clock=moment)

    def select_frame(arguments, context) -> ToolResult:
        _take_lease(layout, context, moment, writer_kind)
        page_id = str(arguments["pageId"]).strip()
        block_ids = [
            str(item).strip()
            for item in arguments["blockIds"]
            if str(item).strip()
        ]
        if not block_ids:
            raise ValueError("a frame needs at least one block")
        if len(block_ids) > MAX_BLOCKS_PER_COLLECTION:
            raise ValueError(
                f"at most {MAX_BLOCKS_PER_COLLECTION} blocks per frame"
            )
        page, blocks = store.page_for_frame(page_id)
        known = {str(block.get("id")) for block in blocks}
        unknown = [
            block_id for block_id in block_ids if block_id not in known
        ]
        if unknown:
            raise ValueError(f"no such block: {unknown[0]}")
        ordered = list(dict.fromkeys(block_ids))
        frame_id = minted.new_id("frame")
        store.append_frame(
            _frame_row(
                frame_id=frame_id,
                page_id=page_id,
                page_version=row_version(page),
                block_ids=ordered,
                version=1,
                source="agent",
                updated_at=iso_timestamp(moment),
            )
        )
        return ToolResult(
            content=json.dumps(
                {
                    "status": "ok",
                    "frameId": frame_id,
                    "version": 1,
                    "blockCount": len(ordered),
                },
                ensure_ascii=False,
            ),
            meta={
                "title": f"frame {frame_id}",
                "target": page_id,
                "hits": len(ordered),
            },
        )

    return ToolSpec(
        name=SELECT_FRAME,
        description=_SELECT_FRAME_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "pageId": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "A selected page id from get_state",
                },
                "blockIds": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                    "description": "The blocks that make the frame",
                },
            },
            "required": ["pageId", "blockIds"],
            "additionalProperties": False,
        },
        side_effect="state",
        timeout_s=5.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=select_frame,
        argument_hint="pageId",
    )


def compose_wiki_tool(
    layout: ProjectLayout,
    *,
    clock: Clock | None = None,
) -> ToolSpec:
    """The agent's draft composer: structured sections, state only."""
    moment = clock or SystemClock()
    store = WritingStateStore(layout, clock=moment)

    def compose_wiki(arguments, context) -> ToolResult:
        _take_lease(layout, context, moment)
        wiki_file = str(arguments["wikiFile"]).strip()
        base_version = arguments.get("baseDraftVersion", 0)
        if (
            isinstance(base_version, bool)
            or not isinstance(base_version, int)
            or base_version < 0
        ):
            raise ValueError(
                "baseDraftVersion must be a non-negative integer"
            )
        sections: list[dict[str, object]] = []
        seen: set[str] = set()
        for raw in arguments["sections"]:
            section_id = str(raw["id"]).strip()
            if WRITING_ID.fullmatch(section_id) is None:
                raise ValueError(f"not a section id: {section_id!r}")
            if section_id in seen:
                raise ValueError(f"duplicate section id: {section_id}")
            seen.add(section_id)
            heading = str(raw["heading"]).strip()
            if not heading:
                raise ValueError("a section needs a heading")
            sections.append(
                {
                    "id": section_id,
                    "heading": heading,
                    "body": str(raw.get("body") or ""),
                    "citations": [
                        str(frame_id).strip()
                        for frame_id in raw.get("citations", [])
                    ],
                    "after": str(raw.get("after") or "").strip(),
                    "baseVersion": raw.get("baseVersion"),
                }
            )
        _saved, page, changed = store.compose_draft(
            wiki_file,
            sections,
            base_version,
            overwrite=bool(arguments.get("overwrite", False)),
        )
        return ToolResult(
            content=json.dumps(
                {
                    "status": "ok",
                    "wikiFile": wiki_file,
                    "draftVersion": row_version(page),
                    "sections": [
                        {
                            "id": str(item.get("id") or ""),
                            "version": row_version(item),
                            "stale": bool(item.get("stale")),
                        }
                        for item in changed
                    ],
                },
                ensure_ascii=False,
            ),
            meta={"title": wiki_file, "path": wiki_file},
        )

    return ToolSpec(
        name=COMPOSE_WIKI,
        description=_COMPOSE_WIKI_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "wikiFile": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "The wiki page the draft becomes",
                },
                "baseDraftVersion": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "The draft version the caller read; 0 for a new page",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "Replace a section a person edited; read it again "
                        "before setting this"
                    ),
                },
                "sections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "minLength": 1,
                                "pattern": r"\S",
                                "description": "A stable section id",
                            },
                            "heading": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "body": {"type": "string"},
                            "citations": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                },
                            },
                            "after": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "baseVersion": {
                                "type": "integer",
                                "minimum": 0,
                                "description": (
                                    "The section version the caller read; "
                                    "omit for a new section"
                                ),
                            },
                        },
                        "required": ["id", "heading"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["wikiFile", "sections"],
            "additionalProperties": False,
        },
        side_effect="state",
        timeout_s=5.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=compose_wiki,
        argument_hint="wikiFile",
    )


def write_wiki_tool(
    layout: ProjectLayout,
    *,
    clock: Clock | None = None,
) -> ToolSpec:
    """The agent's publisher: state draft in, a wiki page on disk."""
    moment = clock or SystemClock()

    def write_wiki(arguments, context) -> ToolResult:
        _take_lease(layout, context, moment)
        wiki_file = str(arguments["wikiFile"]).strip()
        draft_version = arguments["draftVersion"]
        if (
            isinstance(draft_version, bool)
            or not isinstance(draft_version, int)
            or draft_version < 0
        ):
            raise ValueError(
                "draftVersion must be a non-negative integer"
            )
        result = publish_page(
            layout,
            wiki_file,
            draft_version,
            run_id=context.run_id,
            session_id=context.session_id,
            clock=moment,
        )
        return ToolResult(
            content=json.dumps(
                {
                    "status": "ok",
                    "wikiFile": wiki_file,
                    "effectIds": list(result.effect_ids),
                    "writtenAt": result.written_at,
                    "warnings": [
                        dict(item) for item in result.warnings
                    ],
                },
                ensure_ascii=False,
            ),
            meta={
                "title": wiki_file,
                "path": wiki_file,
                "wrote_wiki": True,
            },
        )

    return ToolSpec(
        name=WRITE_WIKI,
        description=_WRITE_WIKI_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "wikiFile": {
                    "type": "string",
                    "minLength": 1,
                    "pattern": r"\S",
                    "description": "The draft page to publish",
                },
                "draftVersion": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "The draft version to publish",
                },
            },
            "required": ["wikiFile", "draftVersion"],
            "additionalProperties": False,
        },
        side_effect="disk",
        timeout_s=30.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=write_wiki,
        argument_hint="wikiFile",
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


def _find_hit(
    state: WritingState, hit_id: str
) -> Mapping[str, object] | None:
    for search in state.searches:
        hits = search.get("hits")
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if isinstance(hit, Mapping) and str(hit.get("id")) == hit_id:
                return hit
    return None


def _page_row(
    *,
    page_id: str,
    hit_id: str,
    url: str,
    title: str,
    status: str,
    block_count: int,
    version: int,
    material: str = "",
    error: str = "",
    error_kind: str = "",
    fetched_at: str = "",
) -> dict[str, object]:
    return {
        "id": page_id,
        "hitId": hit_id,
        "url": url,
        "title": title,
        "status": status,
        "blockCount": block_count,
        "version": version,
        "material": material,
        "error": error,
        "errorKind": error_kind,
        "fetchedAt": fetched_at,
    }


def _frame_row(
    *,
    frame_id: str,
    page_id: str,
    page_version: int,
    block_ids: list[str],
    version: int,
    source: str,
    updated_at: str,
) -> dict[str, object]:
    return {
        "id": frame_id,
        "pageId": page_id,
        "pageVersion": page_version,
        "blockIds": block_ids,
        "version": version,
        "source": source,
        "updatedAt": updated_at,
    }


def _failed_page(
    row: Mapping[str, object],
    message: str,
    kind: str,
    fetched_at: str,
    *,
    title: str | None = None,
) -> dict[str, object]:
    return {
        **row,
        **({"title": title} if title else {}),
        "status": "failed",
        "blockCount": 0,
        "version": row_version(row) + 1,
        "error": message,
        "errorKind": kind,
        "fetchedAt": fetched_at,
    }




def _bounded(items: list[object]) -> tuple[str, tuple[Diagnostic, ...]]:
    content = json.dumps(items, ensure_ascii=False)
    omitted = 0
    while len(content) > _DETAIL_LIMIT and items:
        items.pop()
        omitted += 1
        content = json.dumps(items, ensure_ascii=False)
    diagnostics = (
        (
            Diagnostic(
                "warning",
                "truncated",
                f"{omitted} items omitted from this read",
            ),
        )
        if omitted
        else ()
    )
    return content, diagnostics


__all__ = [
    "COMPOSE_WIKI",
    "GET_STATE",
    "SELECT_FRAME",
    "SELECT_PAGE",
    "WEB_SEARCH",
    "WRITE_WIKI",
    "WebFetchFn",
    "WebSearchFn",
    "compose_wiki_tool",
    "get_state_tool",
    "project_fetch",
    "project_web_search",
    "select_frame_tool",
    "select_page_tool",
    "web_search_tool",
    "write_wiki_tool",
]
