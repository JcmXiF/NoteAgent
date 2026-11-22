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
from dataclasses import replace

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
    ABANDONED,
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
from noteagent.pipeline.materials import Materials
from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.run import run_to_dict
from noteagent.project.run_fingerprint import project_fingerprint
from noteagent.sessions.recovery import recover_store
from noteagent.writing.state import WritingState, WritingStateStore

from evals.harness import Case, CaseRun, Exchange
from evals.report import (
    LOCK,
    NODE_RUN,
    PROVENANCE,
    PUBLISH,
    RECOVERY,
    STATE,
    TOOL_CONTRACT,
)
from evals.trace import TraceSpec

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


def _offered_tools(model) -> list[str]:
    return [
        str(tool["function"]["name"]) for tool in model.requests[0].tools
    ]


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

    run.equal("the query is answered", reply.status_code, 200, TOOL_CONTRACT)
    run.equal("the final answer streams", _tokens(reply).strip(), "已发布。", PUBLISH)
    state = _state(api)
    run.equal("one search round is kept", len(state["searches"]), 1, STATE)
    run.equal(
        "the selected page is ready",
        state["selectedPages"][0]["status"],
        "ready", STATE,
    )
    run.equal("one frame was drawn", len(state["frames"]), 1, STATE)
    page = _first_page(state)
    run.equal("both sections reached the draft", len(page["sections"]), 2, STATE)
    run.equal("the second compose moved the page version", page["version"], 2, STATE)

    run.equal("the draft page is published", page["status"], "written", STATE)
    run.equal("the publish recorded one effect", len(page["effectIds"]), 1, PUBLISH)
    _wait_for_lease(api)
    run.equal("the writer lease is released", _state(api)["writer"], None, LOCK)

    wiki = (run.layout.root / WIKI).read_text(encoding="utf-8")
    parsed = parse_page(wiki)
    sources = parse_inline_list(parsed.fields.get("sources", ""))
    run.equal("both cited blocks are in sources", len(sources), 2, PROVENANCE)
    run.check(
        "both section headings reached the page",
        "## 定义" in wiki and "## 性质" in wiki, "", PUBLISH,
    )
    run.check("the section body reached the page", "熵衡量不确定性。" in wiki, "", PUBLISH)
    effects = EffectJournal(run.layout).query(kind=WIKI_PAGE_WRITE)
    run.equal("one committed page write", len(effects), 1, PUBLISH)
    run.equal("the effect is committed", effects[0].status, COMMITTED, PUBLISH)
    run.equal("the write is in page history", len(page_snapshots(run.layout, WIKI)), 1, PUBLISH)

    material = str(state["selectedPages"][0]["material"])
    marks = list_source_citations(run.layout).marks.get(material, [])
    run.equal("both markers resolve to the raw", len(marks), 2, PROVENANCE)
    run.equal(
        "the marks carry the pinned blocks",
        [mark.block_ids for mark in marks],
        [("b1", "b2"), ("b1", "b2")], PROVENANCE,
    )
    run.equal(
        "the marks own the two headings",
        [mark.heading for mark in marks],
        ["定义", "性质"], PROVENANCE,
    )
    run.check("the raw original is kept", (run.layout.root / material).is_file(), "", PROVENANCE)

    WritingStateStore(run.layout, clock=run.clock).archive()
    after = list_source_citations(run.layout).marks.get(material, [])
    run.equal(
        "the archive keeps the markers resolvable",
        [(item.id, item.block_ids) for item in after],
        [(item.id, item.block_ids) for item in marks], PROVENANCE,
    )
    run.equal(
        "the archived state is no longer current",
        _state(api)["searches"],
        [], STATE,
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
    run.equal("the first run kept one frame", len(state["frames"]), 1, STATE)
    run.equal("the first run wrote no draft", state["draft"]["pages"], [], STATE)

    second = api.create_session()
    reply = api.query(second, "从 state 继续发布")
    run.equal("the second run answers", reply.status_code, 200, TOOL_CONTRACT)
    run.equal("the second run publishes", _tokens(reply).strip(), "继续发布了。", PUBLISH)
    state = _state(api)
    page = _first_page(state)
    run.equal("the state's frame is published", page["status"], "written", STATE)
    run.check("the wiki page exists", (run.layout.root / WIKI).is_file(), "", PUBLISH)


def writing_multi_search(run: CaseRun) -> None:
    """Two searches are kept, and the first round's hit is still selectable."""
    store = WritingStateStore(run.layout, clock=run.clock)
    store.save(WritingState(draft={"pages": []}))
    model = FauxModelClient(
        _call("c1", "web_search", {"query": "熵"}),
        _call("c2", "web_search", {"query": "贝叶斯"}),
        _call("c3", "get_state", {}),
        _call("c4", "get_state", {"searchId": "search-1"}),
        _call("c5", "select_page", {"hitId": "hit-1"}),
        _final("两轮都搜到了。"),
    )
    api = run.start(
        model, web_search=search_cards, web_fetch=capture_page
    )
    session = api.create_session()
    reply = api.query(session, "先搜熵，再搜贝叶斯，从第一轮选页")

    run.equal("the query is answered", reply.status_code, 200, TOOL_CONTRACT)
    run.equal(
        "the final answer streams",
        _tokens(reply).strip(),
        "两轮都搜到了。",
        PUBLISH,
    )
    state = _state(api)
    run.equal(
        "both search rounds are kept",
        [row["query"] for row in state["searches"]],
        ["熵", "贝叶斯"],
        STATE,
    )
    first_hits = state["searches"][0]["hits"]
    run.equal(
        "the first round keeps its hit", first_hits[0]["id"], "hit-1", STATE
    )
    run.equal(
        "the page came from the first round",
        state["selectedPages"][0]["hitId"],
        "hit-1",
        STATE,
    )
    entries = api.entries(session)
    get_state_ids = [
        str(call.get("id"))
        for entry in entries
        if entry.get("role") == "assistant"
        for call in entry.get("toolCalls") or []
        if call.get("name") == "get_state"
    ]

    def tool_result(call_id: str) -> str:
        return next(
            str(entry.get("content") or "")
            for entry in entries
            if entry.get("role") == "toolResult"
            and entry.get("toolCallId") == call_id
        )

    summary = json.loads(tool_result(get_state_ids[0]))
    searches = (summary.get("areas") or {}).get("searches") or {}
    run.equal("the summary counts both rounds", searches.get("count"), 2, STATE)
    run.equal(
        "the summary names the latest round",
        (searches.get("latest") or {}).get("query"),
        "贝叶斯",
        STATE,
    )
    first_round = json.loads(tool_result(get_state_ids[1]))
    run.check(
        "the first round's hits are readable through get_state",
        any(str(hit.get("id")) == "hit-1" for hit in first_round),
        "",
        STATE,
    )


def writing_state_restart(run: CaseRun) -> None:
    """The state survives an app restart, and an expired lease is taken over."""
    _seed(run)
    model = FauxModelClient(
        _call("c1", "web_search", {"query": "熵"}),
        _final("搜到了。"),
        _call(
            "c2",
            "select_frame",
            {"pageId": "page-1", "blockIds": ["b3"]},
        ),
        _final("画好了。"),
    )
    api = run.start(
        model, web_search=search_cards, web_fetch=capture_page
    )
    session = api.create_session()
    first = api.query(session, "先搜一下")

    run.equal("the first run answers", first.status_code, 200, TOOL_CONTRACT)
    run.equal("the first answer streams", _tokens(first).strip(), "搜到了。", PUBLISH)
    _wait_for_lease(api)
    state = _state(api)
    run.equal("the search is in the state", state["searches"][0]["query"], "熵", STATE)
    run.equal("the first run released its lease", state["writer"], None, LOCK)

    store = WritingStateStore(run.layout, clock=run.clock)
    current = store.load()
    expired = {
        "kind": "agent",
        "id": "expired",
        "since": "2020-01-01T00:00:00Z",
    }
    # A writer that stopped heartbeating for longer than WRITER_TTL_S expires;
    # the next writing tool takes the lease over.
    store.save(replace(current, writer=expired))

    api = run.restart()
    state = _state(api)
    run.equal("the search survives the restart", state["searches"][0]["query"], "熵", STATE)
    run.equal("the page survives the restart", state["selectedPages"][0]["id"], "page-1", STATE)
    run.equal("the frame survives the restart", state["frames"][0]["id"], "frame-1", STATE)
    run.equal("the draft survives the restart", len(state["draft"]["pages"]), 1, STATE)

    session = api.create_session()
    second = api.query(session, "再画一个框")

    run.equal("the second run answers", second.status_code, 200, TOOL_CONTRACT)
    run.equal("the second answer streams", _tokens(second).strip(), "画好了。", PUBLISH)
    _wait_for_lease(api)
    state = _state(api)
    run.equal("the expired lease was taken over", len(state["frames"]), 2, LOCK)
    run.equal(
        "the taken-over frame is the new one",
        state["frames"][1]["blockIds"],
        ["b3"],
        STATE,
    )
    run.equal("the second run released its lease", state["writer"], None, LOCK)


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

    run.equal("the node endpoint answers", reply.status_code, 200, TOOL_CONTRACT)
    payload = reply.json()
    run.equal("the node completed", payload["status"], "completed", NODE_RUN)
    run.equal("one frame is the node's result", len(payload["frames"]), 1, STATE)
    offered = sorted(_offered_tools(model))
    run.equal("the node sees only two tools", offered, ["get_state", "select_frame"], TOOL_CONTRACT)
    sessions = api.request("GET", "/api/v1/sessions").json()["sessions"]
    run.equal("the node session stays out of the chat list", sessions, [], NODE_RUN)
    state = _state(api)
    run.equal("the node wrote the state", state["frames"][0]["blockIds"], ["b1", "b2"], STATE)
    run.equal("the node lease is released", state["writer"], None, LOCK)


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
        run.check("the node started", model.started.wait(5), "", NODE_RUN)
        api.wait_for(
            lambda: _state(api)["writer"] is not None,
            "the node holds the state lease",
        )
        state = _state(api)
        refused = _save(api, int(state["stateVersion"]), {})
        run.equal(
            "a save during the node is refused",
            refused.status_code,
            409, LOCK,
        )
        run.equal(
            "the refusal names the lease",
            refused.json()["code"],
            "state.busy", LOCK,
        )
    finally:
        model.release.set()
        thread.join(5)

    run.equal("the node still completes", responses[0].status_code, 200, NODE_RUN)
    _wait_for_lease(api)
    state = _state(api)
    run.equal(
        "the node wrote its frame",
        state["frames"][0]["blockIds"],
        ["b1"], STATE,
    )
    run.equal("the lease is released", state["writer"], None, LOCK)


def writing_human_edit_and_conflict(run: CaseRun) -> None:
    """A person edits, loses a stale save, stales a frame, publishes and is refused."""
    _seed(run)
    section_args = {
        "id": "definition",
        "heading": "定义",
        "citations": ["frame-1"],
        "baseVersion": 2,
    }
    next_call = iter(range(1, 7))

    def compose(body: str, *, overwrite: bool = False) -> list[ModelEvent]:
        arguments = {
            "wikiFile": WIKI,
            "baseDraftVersion": 2,
            "sections": [{**section_args, "body": body}],
        }
        if overwrite:
            arguments["overwrite"] = True
        return _call(f"c{next(next_call)}", "compose_wiki", arguments)

    def read_section() -> list[ModelEvent]:
        return _call(
            f"c{next(next_call)}",
            "get_state",
            {
                "section": "section",
                "wikiFile": WIKI,
                "sectionId": "definition",
            },
        )

    api = run.start(
        FauxModelClient(
            compose("agent 想覆盖。"),
            read_section(),
            compose("agent 覆盖。", overwrite=True),
            read_section(),
            _final("改好了。"),
            _call(
                "c5",
                "write_wiki",
                {"wikiFile": WIKI, "draftVersion": 3},
            ),
            _final("已发布。"),
            _call(
                "c6",
                "write_wiki",
                {"wikiFile": WIKI, "draftVersion": 4},
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

    run.equal("the human save lands", saved.status_code, 200, LOCK)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the touched section is marked", section["humanEdited"], True, STATE)
    run.equal("the human save bumps the state", state["stateVersion"], base + 1, STATE)
    conflict = _save(api, base, {})
    run.equal("the stale base is a conflict", conflict.status_code, 409, LOCK)
    run.equal("the conflict names its code", conflict.json()["code"], "state.conflict", LOCK)
    run.equal(
        "the conflict returns the current version",
        conflict.json()["params"]["stateVersion"],
        str(state["stateVersion"]), LOCK,
    )

    guard_session = api.create_session()
    guard = api.query(guard_session, "改一下草稿")
    run.equal("the guard query answers", guard.status_code, 200, TOOL_CONTRACT)
    run.equal("the guard answer streams", _tokens(guard).strip(), "改好了。", PUBLISH)
    refused = next(
        (card for card in guard.events_of("observation") if card.get("error")),
        None,
    )
    run.check(
        "the humanEdited section refuses the compose",
        refused is not None, "", STATE,
    )
    diagnostics = (refused or {}).get("diagnostics") or []
    run.check(
        "the refusal names the overwrite",
        any(
            "overwrite" in str(item.get("message") or "")
            for item in diagnostics
        ),
        "",
        STATE,
    )
    run.check(
        "the refused compose leaves the human body",
        "人工改过。" in json.dumps(api.entries(guard_session), ensure_ascii=False),
        "",
        STATE,
    )
    _wait_for_lease(api)
    state = _state(api)
    page = _first_page(state)
    section = _first_section(page)
    run.equal("the overwrite replaces the body", section["body"], "agent 覆盖。", STATE)
    run.equal("the overwrite clears humanEdited", section["humanEdited"], False, STATE)
    run.equal("the overwrite bumps the draft", page["version"], 3, STATE)

    state = _state(api)
    base = int(state["stateVersion"])
    frames = [{**state["frames"][0], "blockIds": ["b1", "b2"]}]
    staled = _save(api, base, {"frames": frames})
    run.equal("the frame edit lands", staled.status_code, 200, STATE)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the citing section is stale", section["staleReason"], "frame_changed", PROVENANCE)

    session = api.create_session()
    first = api.query(session, "发布")
    run.equal("the publish answers", first.status_code, 200, PUBLISH)
    _wait_for_lease(api)
    state = _state(api)
    page = _first_page(state)
    run.equal("the stale page still publishes", page["status"], "written", PUBLISH)
    entries = api.entries(session)
    run.check(
        "the write reports the drift as a warning",
        "frame_changed" in json.dumps(entries, ensure_ascii=False), "", PROVENANCE,
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
    run.equal("the frame delete lands", moved.status_code, 200, STATE)
    state = _state(api)
    section = _first_section(_first_page(state))
    run.equal("the target is now missing", section["staleReason"], "frame_missing", PROVENANCE)

    second = api.create_session()
    refused = api.query(second, "再发布")
    run.equal("the refused publish answers", refused.status_code, 200, PUBLISH)
    run.check(
        "the missing frame refuses the publish",
        "no such frame" in json.dumps(api.entries(second), ensure_ascii=False), "", PROVENANCE,
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

    run.equal("the run still ends cleanly", reply.status_code, 200, TOOL_CONTRACT)
    failed = [
        card
        for card in reply.events_of("observation")
        if card.get("error")
    ]
    run.check("the failures are visible cards", len(failed) >= 5, "", TOOL_CONTRACT)
    state = _state(api)
    run.check(
        "the failed page is recorded",
        any(
            page.get("status") == "failed"
            for page in state["selectedPages"]
        ), "", STATE,
    )
    entries = json.dumps(api.entries(session), ensure_ascii=False)
    run.check(
        "the get_state summary names the fetch failure",
        "fetch_failed" in entries, "", STATE,
    )
    missing_page = state["draft"]["pages"][1]
    missing_section = _first_section(missing_page)
    run.equal(
        "the unknown frame leaves a missing target",
        missing_section["staleReason"],
        "frame_missing", PROVENANCE,
    )
    run.equal(
        "the missing-frame page stays draft",
        missing_page["status"],
        "draft", STATE,
    )
    run.check(
        "no page was written for the missing frame",
        not (
            run.layout.root / "wiki/concepts/missing.md"
        ).exists(), "", PUBLISH,
    )
    run.equal(
        "the empty draft page stays draft",
        state["draft"]["pages"][0]["status"],
        "draft", STATE,
    )
    run.check(
        "the empty frame is refused",
        "invalid arguments for select_frame" in entries, "", TOOL_CONTRACT,
    )
    run.check("the unknown citation is refused", "no such frame" in entries, "", PROVENANCE)
    run.check("the duplicate id is refused", "duplicate section id" in entries, "", TOOL_CONTRACT)
    run.check("the empty draft is refused", "has no sections" in entries, "", PUBLISH)


def _seed_node_run(
    run: CaseRun, node_id: str, run_id: str, page_id: str
) -> None:
    """A node session an interrupted run left behind."""
    store = run.store
    store.create(node_id, "Auto-frame")
    store.append(
        node_id,
        {"type": "node", "node": "auto-frame", "pageId": page_id},
    )
    store.append(
        node_id,
        {
            "type": "message",
            "role": "user",
            "content": auto_frame_instruction(page_id),
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
        node_id,
        marker_entry(
            RunMarker(
                run_id,
                RUNNING,
                fingerprint=RunFingerprint.from_mapping(
                    fingerprint,
                    model="faux",
                    toolset=toolset_name(node_tools(deps, run.layout)),
                    config_hash=config_digest(
                        fingerprint.get("configHash", ""),
                        project(store.load(node_id).entries).sections,
                    ),
                ),
            )
        ),
    )
    store.append(
        node_id,
        {
            "type": "message",
            "role": "assistant",
            "content": "",
            "toolCalls": [
                {
                    "id": "c0",
                    "name": "get_state",
                    "arguments": json.dumps({"pageId": page_id}),
                }
            ],
        },
    )


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
    _seed_node_run(run, "node-1", "run-1", "page-1")
    _seed_node_run(run, "node-2", "run-2", "page-1")
    recover_store(store, clock=run.clock)

    reply = api.request(
        "POST",
        "/api/projects/demo/nodes/node-1/resume",
        json={"runId": "run-1"},
    )

    run.equal("the node resumes", reply.status_code, 200, RECOVERY)
    payload = reply.json()
    run.equal("the resumed node completed", payload["status"], "completed", RECOVERY)
    run.equal("the resumed node wrote its frame", payload["frames"][0]["blockIds"], ["b1"], STATE)
    state = _state(api)
    run.equal("the state kept the frame", state["frames"][0]["blockIds"], ["b1"], STATE)
    run.equal("the resumed lease is released", state["writer"], None, LOCK)
    marker = last_marker(store.load("node-1").entries)
    run.check(
        "the resumed run's marker is finished",
        marker is not None and marker.state == FINISHED, "", RECOVERY,
    )

    abandoned = api.request(
        "POST",
        "/api/v1/sessions/node-2/abandon",
        json={"runId": "run-2"},
    )
    run.equal("the node abandons", abandoned.status_code, 200, RECOVERY)
    run.equal(
        "the abandon is recorded",
        abandoned.json().get("abandoned"),
        True,
        RECOVERY,
    )
    run.equal(
        "the abandoned session is idle",
        abandoned.json()["session"]["state"]["busy"],
        False,
        STATE,
    )
    state = _state(api)
    run.check(
        "the abandoned state is readable",
        state.get("stateVersion") is not None, "", STATE,
    )
    marker = last_marker(store.load("node-2").entries)
    run.check(
        "the abandoned run's marker is terminal",
        marker is not None and marker.state == ABANDONED, "", RECOVERY,
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
    Materials(run.layout, clock=run.clock).stage(
        capture_page("https://example.com/entropy"), run_id="pr-old"
    )
    model = FauxModelClient(_final("好的"))
    api = run.start(model)
    session = api.create_session()
    api.query(session, "你好")

    start_reply = api.request(
        "POST", "/api/v1/pipeline/runs", json={"intent": "熵"}
    )
    run.check(
        "the old start is gone",
        start_reply.status_code in (404, 405),
        f"status was {start_reply.status_code}",
        STATE,
    )
    names = _offered_tools(model)
    run.check("the tool list is not empty", bool(names), "", TOOL_CONTRACT)
    run.check(
        "the six writing tools are offered",
        {
            "web_search",
            "select_page",
            "select_frame",
            "compose_wiki",
            "write_wiki",
            "get_state",
        }.issubset(names),
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the new tool list has no propose",
        "propose" not in names,
        "",
        TOOL_CONTRACT,
    )
    materials = api.request("GET", "/api/v1/pipeline/runs/pr-old/materials")
    run.equal("the old materials are readable", materials.status_code, 200, STATE)
    listed = materials.json()["materials"]
    run.equal("the old run's material is listed", len(listed), 1, STATE)
    run.equal("the old material is staged", listed[0]["state"], "staged", STATE)
    run.equal("the old material names its run", listed[0]["runId"], "pr-old", STATE)

    old = api.request("GET", "/api/v1/pipeline/runs/pr-old")
    run.equal("the old run is readable", old.status_code, 200, STATE)
    run.equal("the old run keeps its id", old.json().get("id"), "pr-old", STATE)

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
    run.equal("the old session is readable", session.status_code, 200, STATE)
    run.check(
        "the old wait entry is readable",
        any(
            entry.get("waitId") == "w-old"
            for entry in run.store.load("s-old").entries
        ), "", STATE,
    )

    archives = store.archive()
    run.equal("the state archives", len(archives), 1, STATE)
    listing = api.request(
        "GET", "/api/projects/demo/writing/archives"
    )
    run.equal("the archive is listed", listing.status_code, 200, STATE)
    items = listing.json()["items"]
    run.equal("one archived state is readable", len(items), 1, STATE)
    restored = api.request(
        "POST",
        f"/api/projects/demo/writing/archives/{items[0]['id']}/activate",
    )
    run.equal("the archived state can be restored", restored.status_code, 200, STATE)
    run.equal(
        "the restored state has its page",
        len(restored.json()["selectedPages"]),
        1, STATE,
    )


WRITING_CASES = (
    Case(
        "writing_full_flow",
        "The agent writes and publishes a two-section wiki page",
        writing_full_flow,
        trace=TraceSpec(
            required=(
                "web_search",
                "select_page",
                "select_frame",
                "compose_wiki",
                "write_wiki",
            ),
            forbidden=("propose",),
        ),
    ),
    Case(
        "writing_partial_then_second_run",
        "A partial run is continued from the state",
        writing_partial_then_second_run,
        trace=TraceSpec(
            required=("compose_wiki", "write_wiki"),
            forbidden=("propose",),
        ),
    ),
    Case(
        "writing_multi_search",
        "Two searches are kept and the first round's hit is selectable",
        writing_multi_search,
        trace=TraceSpec(
            required=("web_search", "select_page"),
            forbidden=("propose",),
        ),
    ),
    Case(
        "writing_state_restart",
        "The state survives an app restart and an expired lease is taken over",
        writing_state_restart,
        trace=TraceSpec(
            required=("select_frame",),
            forbidden=("propose",),
        ),
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
        trace=TraceSpec(forbidden=("propose",)),
    ),
    Case(
        "writing_failure_matrix",
        "Every structural failure is a visible tool error",
        writing_failure_matrix,
        trace=TraceSpec(forbidden=("propose",)),
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
        trace=TraceSpec(forbidden=("propose",)),
    ),
)

__all__ = ["WRITING_CASES"]
