"""Deterministic writing evals: the agent's tools over the HTTP seam.

Each case is one user story from the writing spec: the full search → select →
frame → compose → publish path, a partial path continued from the state, the
auto-frame node, a person's edit beside a save lock, the failure matrix, a
crashed node resumed from its marker, and an old run/archived state still
readable. The model is scripted and the project is temporary, so nothing here
reaches a provider or the checkout.
"""

from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.clock import iso_timestamp
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelEvent,
    ModelStartEvent,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
)
from noteagent.agent.projection import project
from noteagent.agent.run_marker import (
    FINISHED,
    RUNNING,
    RunFingerprint,
    RunMarker,
    config_digest,
    last_marker,
    marker_entry,
)
from noteagent.agent.tools import toolset_name
from noteagent.api.deps import AppDeps
from noteagent.api.nodes import auto_frame_instruction, node_tools
from noteagent.effects.journal import (
    COMMITTED,
    WIKI_PAGE_WRITE,
    EffectJournal,
)
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.ingest.page_history import page_snapshots
from noteagent.ingest.source_citations import list_source_citations
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.run import run_to_dict
from noteagent.project.run_fingerprint import project_fingerprint
from noteagent.sessions.recovery import recover_store
from noteagent.writing.state import WritingState, WritingStateStore

from evals.harness import Case, CaseRun, Exchange

WIKI = "wiki/concepts/entropy.md"
MATERIAL = "raw/sources/web/example.com/entropy.html"
PAGE_HTML = (
    "<html><body><article>"
    "<p data-na-block='b1'>熵衡量不确定性。</p>"
    "<p data-na-block='b2'>信息是不确定性的减少。</p>"
    "<p data-na-block='b3'>熵是信息的度量。</p>"
    "</article></body></html>"
)


def search_cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="Entropy",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵衡量不确定性。",
            why="",
            via="eval",
        )
    ]


def capture_page(_url: str) -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="Entropy",
        markdown="熵衡量不确定性。信息是不确定性的减少。",
        html=PAGE_HTML,
        capture_method="eval",
        fidelity="ok",
        reasons=(),
        http_status=200,
        char_count=100,
    )


def failing_capture(_url: str) -> Capture:
    raise RuntimeError("the page refused the capture")


def _call(
    call_id: str, name: str, arguments: dict[str, object]
) -> list[ModelEvent]:
    return FauxModelClient.tool_call(
        call_id, name, json.dumps(arguments, ensure_ascii=False)
    ).script


def _final(text: str) -> list[ModelEvent]:
    return FauxModelClient.text(text).script


def _record_writing_metrics(
    run: CaseRun, page: dict[str, object]
) -> None:
    """The writing metrics the spec asks the report to carry."""
    sections = page.get("sections") or []
    cited = sum(
        1 for section in sections if section.get("citations")
    )
    stale = sum(1 for section in sections if section.get("stale"))
    run.metric("sections", len(sections))
    run.metric(
        "citation_coverage",
        round(cited / len(sections), 2) if sections else 0.0,
    )
    run.metric("stale_sections", stale)
    run.metric(
        "write_success", 1 if page.get("status") == "written" else 0
    )


def _first_page(state: dict[str, object]) -> dict[str, object]:
    draft = state["draft"]
    pages = draft["pages"] if isinstance(draft, dict) else []
    if not pages:
        raise AssertionError("no draft page in the state")
    return pages[0]


def _first_section(page: dict[str, object]) -> dict[str, object]:
    sections = page["sections"]
    if not sections:
        raise AssertionError("no section in the draft page")
    return sections[0]


def _tokens(reply: Exchange) -> str:
    return "".join(
        str(data.get("text") or "") for data in reply.events_of("token")
    )


def _tool_names(entries: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        if entry.get("type") != "message":
            continue
        for call in entry.get("toolCalls") or []:
            names.append(str(call.get("name") or ""))
    return names


def _state(api) -> dict[str, object]:
    reply = api.request(
        "GET", "/api/projects/demo/writing/state"
    )
    if reply.status_code != 200:
        raise AssertionError(
            f"the writing state could not be read: {reply.text[:300]}"
        )
    return reply.json()


def _wait_for_lease(api) -> None:
    """The run's done event can arrive just before its writer lease clears."""
    api.wait_for(
        lambda: _state(api)["writer"] is None,
        "the run released the writing state",
        timeout_s=5.0,
    )


def _save(api, base_version: int, changes: dict[str, object]) -> Exchange:
    return api.request(
        "POST",
        "/api/projects/demo/writing/state",
        json={"baseVersion": base_version, "changes": changes},
    )


def _frame(
    *,
    block_ids: tuple[str, ...] = ("b1",),
    version: int = 1,
    page_version: int = 1,
) -> dict[str, object]:
    return {
        "id": "frame-1",
        "pageId": "page-1",
        "pageVersion": page_version,
        "blockIds": list(block_ids),
        "version": version,
        "source": "agent",
    }


def _section(
    *,
    body: str = "熵衡量不确定性。",
    frame: str = "frame-1",
    frame_version: int = 1,
    block_ids: tuple[str, ...] = ("b1",),
    version: int = 1,
    human: bool = False,
) -> dict[str, object]:
    return {
        "id": "definition",
        "heading": "定义",
        "body": body,
        "citations": [
            {
                "frameId": frame,
                "frameVersion": frame_version,
                "blockIds": list(block_ids),
            }
        ],
        "version": version,
        "stale": False,
        "staleReason": "",
        "humanEdited": human,
    }


def _page(
    *,
    sections: list[dict[str, object]] | None = None,
    version: int = 1,
    status: str = "draft",
    wiki: str = WIKI,
) -> dict[str, object]:
    return {
        "wikiFile": wiki,
        "title": wiki.rsplit("/", 1)[-1].removesuffix(".md"),
        "version": version,
        "status": status,
        "sections": sections if sections is not None else [_section()],
    }


def _seed(
    run: CaseRun,
    *,
    page: dict[str, object] | None = None,
    pages: list[dict[str, object]] | None = None,
    frame: bool = True,
    blocks: tuple[str, ...] = ("b1", "b2", "b3"),
) -> WritingStateStore:
    store = WritingStateStore(run.layout, clock=run.clock)
    store.save_page_blocks(
        "page-1", [{"id": block} for block in blocks]
    )
    store.save_page_material(
        "page-1", PAGE_HTML.encode("utf-8"), MATERIAL
    )
    state = WritingState(
        selected_pages=(
            {
                "id": "page-1",
                "hitId": "hit-1",
                "url": "https://example.com/seed",
                "title": "Entropy",
                "status": "ready",
                "blockCount": len(blocks),
                "version": 1,
                "material": MATERIAL,
            },
        ),
        frames=(_frame(),) if frame else (),
        draft={
            "pages": pages
            if pages is not None
            else [page or _page()],
        },
    )
    store.save(store.refresh_provenance(state))
    # Keep the app's id counters past the ids this seed already used.
    run.ids.new_id("page")
    run.ids.new_id("frame")
    return store


def writing_full_flow(run: CaseRun) -> None:
    """Search, select, frame, compose and publish, with every artifact checked."""
    model = FauxModelClient(
        _call("c1", "web_search", {"query": "熵"}),
        _call("c2", "get_state", {"searchId": "search-1"}),
        _call("c3", "select_page", {"hitId": "hit-1"}),
        _call("c4", "get_state", {"pageId": "page-1"}),
        _call(
            "c5",
            "select_frame",
            {"pageId": "page-1", "blockIds": ["b1", "b2"]},
        ),
        _call(
            "c6",
            "compose_wiki",
            {
                "wikiFile": WIKI,
                "baseDraftVersion": 0,
                "sections": [
                    {
                        "id": "definition",
                        "heading": "定义",
                        "body": "熵衡量不确定性。",
                        "citations": ["frame-1"],
                    }
                ],
            },
        ),
        _call("c7", "get_state", {"section": "draft"}),
        _call(
            "c8",
            "compose_wiki",
            {
                "wikiFile": WIKI,
                "baseDraftVersion": 1,
                "sections": [
                    {
                        "id": "properties",
                        "heading": "性质",
                        "body": "信息是不确定性的减少。",
                        "citations": ["frame-1"],
                        "after": "definition",
                    }
                ],
            },
        ),
        _call(
            "c9",
            "write_wiki",
            {"wikiFile": WIKI, "draftVersion": 2},
        ),
        _final("已发布。"),
    )
    api = run.start(
        model, web_search=search_cards, web_fetch=capture_page
    )
    session = api.create_session()
    reply = api.query(session, "写一篇关于熵的 wiki")

    run.equal("the query is answered", reply.status_code, 200)
    run.equal("the final answer streams", _tokens(reply).strip(), "已发布。")
    run.equal(
        "the tool trajectory follows the spec's recipe",
        _tool_names(api.entries(session)),
        [
            "web_search",
            "get_state",
            "select_page",
            "get_state",
            "select_frame",
            "compose_wiki",
            "get_state",
            "compose_wiki",
            "write_wiki",
        ],
    )
    state = _state(api)
    run.equal("one search round is kept", len(state["searches"]), 1)
    run.equal(
        "the selected page is ready",
        state["selectedPages"][0]["status"],
        "ready",
    )
    run.equal("one frame was drawn", len(state["frames"]), 1)
    page = _first_page(state)
    run.equal("both sections reached the draft", len(page["sections"]), 2)
    run.equal("the second compose moved the page version", page["version"], 2)
    _record_writing_metrics(run, page)
    run.metric(
        "tool_calls", len(_tool_names(api.entries(session)))
    )
    run.equal("the draft page is published", page["status"], "written")
    run.equal("the publish recorded one effect", len(page["effectIds"]), 1)
    _wait_for_lease(api)
    run.equal("the writer lease is released", _state(api)["writer"], None)

    wiki = (run.layout.root / WIKI).read_text(encoding="utf-8")
    parsed = parse_page(wiki)
    sources = parse_inline_list(parsed.fields.get("sources", ""))
    run.equal("both cited blocks are in sources", len(sources), 2)
    run.check(
        "both section headings reached the page",
        "## 定义" in wiki and "## 性质" in wiki,
    )
    run.check("the section body reached the page", "熵衡量不确定性。" in wiki)
    effects = EffectJournal(run.layout).query(kind=WIKI_PAGE_WRITE)
    run.equal("one committed page write", len(effects), 1)
    run.equal("the effect is committed", effects[0].status, COMMITTED)
    run.equal("the write is in page history", len(page_snapshots(run.layout, WIKI)), 1)

    material = str(state["selectedPages"][0]["material"])
    marks = list_source_citations(run.layout).marks.get(material, [])
    run.equal("both markers resolve to the raw", len(marks), 2)
    run.equal(
        "the marks carry the pinned blocks",
        [mark.block_ids for mark in marks],
        [("b1", "b2"), ("b1", "b2")],
    )
    run.equal(
        "the marks own the two headings",
        [mark.heading for mark in marks],
        ["定义", "性质"],
    )
    run.check("the raw original is kept", (run.layout.root / material).is_file())

    WritingStateStore(run.layout, clock=run.clock).archive()
    after = list_source_citations(run.layout).marks.get(material, [])
    run.equal(
        "the archive keeps the markers resolvable",
        [(item.id, item.block_ids) for item in after],
        [(item.id, item.block_ids) for item in marks],
    )
    run.equal(
        "the archived state is no longer current",
        _state(api)["searches"],
        [],
    )


def writing_partial_then_second_run(run: CaseRun) -> None:
    """A first run stops at frames; a second reads the state and publishes."""
    model = FauxModelClient(
        _call("c1", "web_search", {"query": "熵"}),
        _call("c2", "get_state", {"searchId": "search-1"}),
        _call("c3", "select_page", {"hitId": "hit-1"}),
        _call("c4", "get_state", {"pageId": "page-1"}),
        _call(
            "c5",
            "select_frame",
            {"pageId": "page-1", "blockIds": ["b1", "b2"]},
        ),
        _final("先停在框。"),
        _call(
            "c6",
            "compose_wiki",
            {
                "wikiFile": WIKI,
                "baseDraftVersion": 0,
                "sections": [
                    {
                        "id": "definition",
                        "heading": "定义",
                        "body": "熵衡量不确定性。",
                        "citations": ["frame-1"],
                    }
                ],
            },
        ),
        _call(
            "c7",
            "write_wiki",
            {"wikiFile": WIKI, "draftVersion": 1},
        ),
        _final("继续发布了。"),
    )
    api = run.start(
        model, web_search=search_cards, web_fetch=capture_page
    )
    first = api.create_session()
    api.query(first, "先帮我选材料、画框")
    _wait_for_lease(api)

    state = _state(api)
    run.equal("the first run kept one frame", len(state["frames"]), 1)
    run.equal("the first run wrote no draft", state["draft"]["pages"], [])

    second = api.create_session()
    reply = api.query(second, "从 state 继续发布")
    run.equal("the second run answers", reply.status_code, 200)
    run.equal("the second run publishes", _tokens(reply).strip(), "继续发布了。")
    state = _state(api)
    page = _first_page(state)
    run.equal("the state's frame is published", page["status"], "written")
    run.check("the wiki page exists", (run.layout.root / WIKI).is_file())
    _record_writing_metrics(run, page)
    run.metric(
        "tool_calls",
        len(_tool_names(api.entries(first)))
        + len(_tool_names(api.entries(second))),
    )


def writing_node_run(run: CaseRun) -> None:
    """The auto-frame node writes frames with only its two tools."""
    _seed(run, frame=False, page=_page(sections=[]))
    model = FauxModelClient(
        _call("c1", "get_state", {"pageId": "page-1"}),
        _call(
            "c2",
            "select_frame",
            {"pageId": "page-1", "blockIds": ["b1", "b2"]},
        ),
        _final("DONE"),
    )
    api = run.start(model)

    reply = api.request(
        "POST",
        "/api/projects/demo/nodes/auto-frame",
        json={"pageId": "page-1"},
    )

    run.equal("the node endpoint answers", reply.status_code, 200)
    payload = reply.json()
    run.equal("the node completed", payload["status"], "completed")
    run.equal("one frame is the node's result", len(payload["frames"]), 1)
    offered = sorted(
        tool["function"]["name"] for tool in model.requests[0].tools
    )
    run.equal("the node sees only two tools", offered, ["get_state", "select_frame"])
    sessions = api.request("GET", "/api/v1/sessions").json()["sessions"]
    run.equal("the node session stays out of the chat list", sessions, [])
    state = _state(api)
    run.equal("the node wrote the state", state["frames"][0]["blockIds"], ["b1", "b2"])
    run.equal("the node lease is released", state["writer"], None)
    run.metric("frames", len(state["frames"]))
    run.metric("tool_calls", len(model.requests))


def writing_save_lock(run: CaseRun) -> None:
    """A person's save is refused while a node holds the state lease."""
    _seed(run, frame=False, page=_page(sections=[]))

    class BlockingModel:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                self.release.wait(5)
                yield ModelStartEvent("faux")
                yield ModelToolCallEnd(
                    ToolCall(
                        "c1",
                        "select_frame",
                        json.dumps(
                            {
                                "pageId": "page-1",
                                "blockIds": ["b1"],
                            }
                        ),
                    )
                )
                yield ModelDoneEvent(
                    AssistantMessage(stop_reason="tool_calls")
                )
                return
            yield ModelStartEvent("faux")
            yield ModelDoneEvent(
                AssistantMessage(
                    blocks=(TextBlock("DONE"),), stop_reason="stop"
                )
            )

    model = BlockingModel()
    api = run.start(model)
    app = run.app
    assert app is not None
    responses: list = []

    def start() -> None:
        responses.append(
            TestClient(app).post(
                "/api/projects/demo/nodes/auto-frame",
                json={"pageId": "page-1"},
            )
        )

    thread = threading.Thread(target=start)
    thread.start()
    try:
        run.check("the node started", model.started.wait(5))
        api.wait_for(
            lambda: _state(api)["writer"] is not None,
            "the node holds the state lease",
        )
        state = _state(api)
        refused = _save(api, int(state["stateVersion"]), {})
        run.equal(
            "a save during the node is refused",
            refused.status_code,
            409,
        )
        run.equal(
            "the refusal names the lease",
            refused.json()["code"],
            "state.busy",
        )
    finally:
        model.release.set()
        thread.join(5)

    run.equal("the node still completes", responses[0].status_code, 200)
    _wait_for_lease(api)
    state = _state(api)
    run.equal(
        "the node wrote its frame",
        state["frames"][0]["blockIds"],
        ["b1"],
    )
    run.equal("the lease is released", state["writer"], None)


def writing_human_edit_and_conflict(run: CaseRun) -> None:
    """A person edits, loses a stale save, stales a frame, publishes and is refused."""
    _seed(run)
    api = run.start(
        FauxModelClient(
            _call(
                "c1",
                "write_wiki",
                {"wikiFile": WIKI, "draftVersion": 2},
            ),
            _final("已发布。"),
            _call(
                "c2",
                "write_wiki",
                {"wikiFile": WIKI, "draftVersion": 3},
            ),
            _final("缺目标，拒绝。"),
        )
    )
    state = _state(api)
    base = int(state["stateVersion"])
    page = _first_page(state)
    edited = {
        **page,
        "sections": [
            {**_first_section(page), "body": "人工改过。"}
        ],
    }

    saved = _save(api, base, {"draft": {"pages": [edited]}})

    run.equal("the human save lands", saved.status_code, 200)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the touched section is marked", section["humanEdited"], True)
    run.equal("the human save bumps the state", state["stateVersion"], base + 1)
    conflict = _save(api, base, {})
    run.equal("the stale base is a conflict", conflict.status_code, 409)
    run.equal("the conflict names its code", conflict.json()["code"], "state.conflict")
    run.equal(
        "the conflict returns the current version",
        conflict.json()["params"]["stateVersion"],
        str(state["stateVersion"]),
    )

    state = _state(api)
    base = int(state["stateVersion"])
    frames = [{**state["frames"][0], "blockIds": ["b1", "b2"]}]
    staled = _save(api, base, {"frames": frames})
    run.equal("the frame edit lands", staled.status_code, 200)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the citing section is stale", section["staleReason"], "frame_changed")

    session = api.create_session()
    first = api.query(session, "发布")
    run.equal("the publish answers", first.status_code, 200)
    _wait_for_lease(api)
    state = _state(api)
    page = _first_page(state)
    run.equal("the stale page still publishes", page["status"], "written")
    entries = api.entries(session)
    run.check(
        "the write reports the drift as a warning",
        "frame_changed" in json.dumps(entries, ensure_ascii=False),
    )

    state = _state(api)
    base = int(state["stateVersion"])
    page = _first_page(state)
    edited = {
        **page,
        "sections": [
            {**_first_section(page), "body": "再改。"}
        ],
    }
    moved = _save(api, base, {"frames": [], "draft": {"pages": [edited]}})
    run.equal("the frame delete lands", moved.status_code, 200)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the target is now missing", section["staleReason"], "frame_missing")

    second = api.create_session()
    refused = api.query(second, "再发布")
    run.equal("the refused publish answers", refused.status_code, 200)
    run.check(
        "the missing frame refuses the publish",
        "no such frame" in json.dumps(api.entries(second), ensure_ascii=False),
    )


def writing_failure_matrix(run: CaseRun) -> None:
    """Every structural failure is a tool error, not a crash or a silent write."""
    _seed(
        run,
        pages=[
            _page(sections=[]),
            _page(
                wiki="wiki/concepts/missing.md",
                sections=[_section(frame="frame-404")],
            ),
        ],
    )
    model = FauxModelClient(
        _call("c1", "web_search", {"query": "熵"}),
        _call("c2", "select_page", {"hitId": "hit-1"}),
        _call("c2b", "get_state", {}),
        _call("c3", "select_frame", {"pageId": "page-1", "blockIds": []}),
        _call(
            "c4",
            "compose_wiki",
            {
                "wikiFile": WIKI,
                "baseDraftVersion": 0,
                "sections": [
                    {
                        "id": "definition",
                        "heading": "定义",
                        "body": "正文",
                        "citations": ["frame-404"],
                    }
                ],
            },
        ),
        _call(
            "c5",
            "compose_wiki",
            {
                "wikiFile": WIKI,
                "baseDraftVersion": 0,
                "sections": [
                    {"id": "definition", "heading": "A", "body": "一"},
                    {"id": "definition", "heading": "B", "body": "二"},
                ],
            },
        ),
        _call(
            "c6",
            "write_wiki",
            {"wikiFile": WIKI, "draftVersion": 1},
        ),
        _call(
            "c7",
            "write_wiki",
            {"wikiFile": "wiki/concepts/missing.md", "draftVersion": 1},
        ),
        _final("失败都能看到。"),
    )
    api = run.start(
        model, web_search=search_cards, web_fetch=failing_capture
    )
    session = api.create_session()
    reply = api.query(session, "把所有错误都试一遍")

    run.equal("the run still ends cleanly", reply.status_code, 200)
    failed = [
        card
        for card in reply.events_of("observation")
        if card.get("error")
    ]
    run.check("the failures are visible cards", len(failed) >= 5)
    state = _state(api)
    run.check(
        "the failed page is recorded",
        any(
            page.get("status") == "failed"
            for page in state["selectedPages"]
        ),
    )
    entries = json.dumps(api.entries(session), ensure_ascii=False)
    run.check(
        "the get_state summary names the fetch failure",
        "fetch_failed" in entries,
    )
    missing_page = state["draft"]["pages"][1]
    missing_section = _first_section(missing_page)
    run.equal(
        "the unknown frame leaves a missing target",
        missing_section["staleReason"],
        "frame_missing",
    )
    run.equal(
        "the missing-frame page stays draft",
        missing_page["status"],
        "draft",
    )
    run.check(
        "no page was written for the missing frame",
        not (
            run.layout.root / "wiki/concepts/missing.md"
        ).exists(),
    )
    run.equal(
        "the empty draft page stays draft",
        state["draft"]["pages"][0]["status"],
        "draft",
    )
    run.check(
        "the empty frame is refused",
        "invalid arguments for select_frame" in entries,
    )
    run.check("the unknown citation is refused", "no such frame" in entries)
    run.check("the duplicate id is refused", "duplicate section id" in entries)
    run.check("the empty draft is refused", "has no sections" in entries)


def writing_node_recovery(run: CaseRun) -> None:
    """A crashed node continues from its own marker and writes its frames."""
    _seed(run, frame=False, page=_page(sections=[]))
    model = FauxModelClient(
        _call(
            "c1",
            "select_frame",
            {"pageId": "page-1", "blockIds": ["b1"]},
        ),
        _final("DONE"),
    )
    api = run.start(model)
    store = run.store
    store.create("node-1", "Auto-frame")
    store.append(
        "node-1",
        {"type": "node", "node": "auto-frame", "pageId": "page-1"},
    )
    store.append(
        "node-1",
        {
            "type": "message",
            "role": "user",
            "content": auto_frame_instruction("page-1"),
        },
    )
    deps = AppDeps(
        clock=run.clock,
        ids=run.ids,
        settings={"model": "faux"},
    )
    fingerprint = project_fingerprint(
        run.layout, config={"model": "faux"}
    )
    store.append(
        "node-1",
        marker_entry(
            RunMarker(
                "run-1",
                RUNNING,
                fingerprint=RunFingerprint.from_mapping(
                    fingerprint,
                    model="faux",
                    toolset=toolset_name(node_tools(deps, run.layout)),
                    config_hash=config_digest(
                        fingerprint.get("configHash", ""),
                        project(store.load("node-1").entries).sections,
                    ),
                ),
            )
        ),
    )
    store.append(
        "node-1",
        {
            "type": "message",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": "c0",
                    "name": "get_state",
                    "arguments": '{"pageId": "page-1"}',
                }
            ],
        },
    )
    recover_store(store, clock=run.clock)

    reply = api.request(
        "POST",
        "/api/projects/demo/nodes/node-1/resume",
        json={"runId": "run-1"},
    )

    run.equal("the node resumes", reply.status_code, 200)
    payload = reply.json()
    run.equal("the resumed node completed", payload["status"], "completed")
    run.equal("the resumed node wrote its frame", payload["frames"][0]["blockIds"], ["b1"])
    state = _state(api)
    run.equal("the state kept the frame", state["frames"][0]["blockIds"], ["b1"])
    run.equal("the resumed lease is released", state["writer"], None)
    marker = last_marker(store.load("node-1").entries)
    run.check(
        "the resumed run's marker is finished",
        marker is not None and marker.state == FINISHED,
    )


def writing_old_run_and_archive_readable(run: CaseRun) -> None:
    """Retiring the pipeline leaves its runs and an archived state readable."""
    stamp = iso_timestamp(run.clock)
    old_run_path = run.layout.pipeline_runs / "pr-old.json"
    old_run_path.parent.mkdir(parents=True, exist_ok=True)
    old_run_path.write_text(
        json.dumps(
            run_to_dict(
                PipelineRun(
                    id="pr-old",
                    intent="旧主题",
                    started_by="human",
                    started_at=stamp,
                    updated_at=stamp,
                    status="succeeded",
                    stage="done",
                )
            ),
            ensure_ascii=False,
            indent=2,
        )
        + chr(10),
        encoding="utf-8",
    )
    store = _seed(run)
    api = run.start(FauxModelClient())

    old = api.request("GET", "/api/v1/pipeline/runs/pr-old")
    run.equal("the old run is readable", old.status_code, 200)
    run.equal("the old run keeps its id", old.json().get("id"), "pr-old")

    run.store.create("s-old", "Old wait")
    run.store.append(
        "s-old",
        {
            "type": "wait_proposed",
            "waitId": "w-old",
            "job": "plan",
            "interface": "plan",
            "completion": "approve",
        },
    )
    session = api.request("GET", "/api/v1/sessions/s-old")
    run.equal("the old session is readable", session.status_code, 200)
    run.check(
        "the old wait entry is readable",
        any(
            entry.get("waitId") == "w-old"
            for entry in run.store.load("s-old").entries
        ),
    )

    archives = store.archive()
    run.equal("the state archives", len(archives), 1)
    listing = api.request(
        "GET", "/api/projects/demo/writing/archives"
    )
    run.equal("the archive is listed", listing.status_code, 200)
    items = listing.json()["items"]
    run.equal("one archived state is readable", len(items), 1)
    restored = api.request(
        "POST",
        f"/api/projects/demo/writing/archives/{items[0]['id']}/activate",
    )
    run.equal("the archived state can be restored", restored.status_code, 200)
    run.equal(
        "the restored state has its page",
        len(restored.json()["selectedPages"]),
        1,
    )


WRITING_CASES = (
    Case(
        "writing_full_flow",
        "The agent writes and publishes a two-section wiki page",
        writing_full_flow,
    ),
    Case(
        "writing_partial_then_second_run",
        "A partial run is continued from the state",
        writing_partial_then_second_run,
    ),
    Case(
        "writing_node_auto_frame",
        "The auto-frame node writes frames with its two tools",
        writing_node_run,
    ),
    Case(
        "writing_save_lock",
        "A save is refused while a node holds the state lease",
        writing_save_lock,
    ),
    Case(
        "writing_human_edit_and_conflict",
        "A person's edits, conflicts, stale frames and refusals",
        writing_human_edit_and_conflict,
    ),
    Case(
        "writing_failure_matrix",
        "Every structural failure is a visible tool error",
        writing_failure_matrix,
    ),
    Case(
        "writing_node_recovery",
        "A crashed node resumes from its marker",
        writing_node_recovery,
    ),
    Case(
        "writing_old_run_and_archive",
        "An old run and an archived state stay readable",
        writing_old_run_and_archive_readable,
    ),
)

__all__ = ["WRITING_CASES"]
