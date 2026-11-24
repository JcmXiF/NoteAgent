"""The four local V4 atoms: frame, write, search and read.

Each atom exercises one capability through the real HTTP/SSE path with a
scripted model and a temporary project. The web atoms use a fake search and a
fake capture of one page, so the atom stays local and free; the wiki atoms seed
their own pages. Nothing here judges content: the checks are about whether the
capability worked.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evals.harness import CaseRun
from evals.quality.v4.runner import AtomCase, UsageCounter
from evals.quality.v4.scripted import (
    as_json,
    call_script,
    final_script,
    tool_results,
)
from evals.report import (
    PUBLISH,
    PROVENANCE,
    STATE,
    TOOL_CONTRACT,
)

from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelStartEvent,
    ModelTextDelta,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
)
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.project.doctor import doctor_project
from noteagent.writing.state import WritingStateStore

WIKI_FILE = "wiki/信息熵.md"

_PAGE_HTML = (
    "<html><body><article>"
    "<p data-na-block='b1'>熵是信息的平均量。</p>"
    "<p data-na-block='b2'>交叉熵衡量两个分布的差异。</p>"
    "<p data-na-block='b3'>KL 散度是交叉熵减去熵。</p>"
    "<p data-na-block='b4'>联合熵衡量两个变量的共同不确定性。</p>"
    "</article></body></html>"
)

_SEARCH_PAGES = (
    ("信息熵", "信息熵", "信息熵衡量一个随机变量的平均不确定性。"),
    ("交叉熵", "交叉熵", "交叉熵衡量两个分布的差异，等于熵加上 KL 散度。"),
    ("联合熵", "联合熵", "联合熵衡量两个变量的共同不确定性。"),
)

_READ_PAGES = (
    ("长页", "长页", "头" + "中" * 70_000 + "尾部标记"),
)


def _search_cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="信息熵",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="eval",
        )
    ]


def _capture(_url: str) -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="信息熵",
        markdown="熵是信息的平均量。交叉熵衡量两个分布的差异。",
        html=_PAGE_HTML,
        capture_method="eval",
        fidelity="ok",
        reasons=(),
        http_status=200,
        char_count=100,
    )


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_material(run: CaseRun) -> None:
    """A raw/ page the wiki search must never reach."""
    path = run.layout.root / "raw" / "secret.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("不确定性 秘密\n", encoding="utf-8")


def frame_atom(run: CaseRun, counter: UsageCounter) -> None:
    """A fetched page is framed into bounded blocks."""
    model = counter.wrap(
        FauxModelClient(
            call_script("c1", "web_search", {"query": "信息熵"}),
            call_script("c2", "select_page", {"hitId": "hit-1"}),
            call_script(
                "c3",
                "select_frame",
                {"pageId": "page-1", "blockIds": ["b1", "b2"]},
            ),
            final_script("已画框。"),
        )
    )
    api = run.start(
        model, web_search=_search_cards, web_fetch=_capture
    )
    session = api.create_session()
    reply = api.query(session, "把前两个块画成一个框")

    run.equal(
        "the frame query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "select_frame")
    run.equal("select_frame ran once", len(results), 1, TOOL_CONTRACT)
    payload = as_json(results[0].get("content") if results else "")
    run.equal(
        "the frame is ready", payload.get("status"), "ok", TOOL_CONTRACT
    )
    run.equal(
        "the frame pins two blocks",
        payload.get("blockCount"),
        2,
        STATE,
    )
    state = WritingStateStore(run.layout).load()
    run.equal("the state has one frame", len(state.frames), 1, STATE)
    frame = state.frames[0] if state.frames else {}
    run.equal(
        "the state frame is the tool's frame",
        frame.get("id"),
        payload.get("frameId"),
        STATE,
    )
    run.equal(
        "the frame names its page",
        frame.get("pageId"),
        "page-1",
        STATE,
    )
    run.equal(
        "the frame keeps its blocks",
        frame.get("blockIds"),
        ["b1", "b2"],
        STATE,
    )
    run.equal("the frame is version 1", frame.get("version"), 1, STATE)


def write_atom(run: CaseRun, counter: UsageCounter) -> None:
    """A frame becomes a page with provenance and a maintained catalog."""
    model = counter.wrap(
        FauxModelClient(
            call_script("c1", "web_search", {"query": "信息熵"}),
            call_script("c2", "select_page", {"hitId": "hit-1"}),
            call_script(
                "c3",
                "select_frame",
                {"pageId": "page-1", "blockIds": ["b1", "b2"]},
            ),
            call_script(
                "c4",
                "compose_wiki",
                {
                    "wikiFile": WIKI_FILE,
                    "baseDraftVersion": 0,
                    "sections": [
                        {
                            "id": "definition",
                            "heading": "定义",
                            "body": (
                                "熵是信息的平均量；交叉熵衡量两个分布的"
                                "差异。参见 [[不存在的页]]。"
                            ),
                            "citations": ["frame-1"],
                        }
                    ],
                },
            ),
            call_script(
                "c5",
                "write_wiki",
                {"wikiFile": WIKI_FILE, "draftVersion": 1},
            ),
            final_script("已发布。"),
        )
    )
    api = run.start(
        model, web_search=_search_cards, web_fetch=_capture
    )
    session = api.create_session()
    reply = api.query(session, "把框里的内容写成 wiki 页")

    run.equal(
        "the write query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "write_wiki")
    run.equal("write_wiki ran once", len(results), 1, TOOL_CONTRACT)
    payload = as_json(results[0].get("content") if results else "")
    run.equal("the publish succeeded", payload.get("status"), "ok", PUBLISH)
    warnings = payload.get("warnings") or []
    codes = [
        str(item.get("code") or "")
        for item in warnings
        if isinstance(item, dict)
    ]
    run.check(
        "no maintenance step failed",
        not {"index_failed", "log_failed", "doctor_failed"}.intersection(
            codes
        ),
        str(codes),
        PUBLISH,
    )
    run.check(
        "the doctor saw the page's broken link",
        "link.broken" in codes,
        str(codes),
        PROVENANCE,
    )

    page = run.layout.root / WIKI_FILE
    run.check("the page is on disk", page.is_file(), "", PUBLISH)
    text = _text(page)
    parsed = parse_page(text)
    sources = parse_inline_list(parsed.fields.get("sources", ""))
    state = WritingStateStore(run.layout).load()
    material = (
        str(state.selected_pages[0].get("material") or "")
        if state.selected_pages
        else ""
    )
    run.equal(
        "both cited blocks are sources",
        sources,
        [f"{material}#b1", f"{material}#b2"],
        PROVENANCE,
    )
    run.check(
        "the clip marker pins the frame",
        "<!-- na-clip:frame-1 b1,b2 -->" in text,
        "",
        PROVENANCE,
    )
    run.check(
        "the section heading reached the page",
        "## 定义" in text,
        "",
        PUBLISH,
    )
    run.check(
        "the section body reached the page",
        "熵是信息的平均量" in text,
        "",
        PUBLISH,
    )
    index = _text(run.layout.wiki_index)
    run.check(
        "the page is in the index", "[[信息熵]]" in index, "", PUBLISH
    )
    log = _text(run.layout.wiki_log)
    run.check(
        "the publish is in the log",
        "- `wiki/信息熵.md` (draft 1)" in log,
        "",
        PUBLISH,
    )
    report = doctor_project(run.layout)
    missing = [
        finding
        for finding in report.findings
        if finding.code == "index.missing"
    ]
    run.check(
        "the doctor sees no missing index entry",
        not missing,
        str([finding.message for finding in missing]),
        PUBLISH,
    )


def search_atom(run: CaseRun, counter: UsageCounter) -> None:
    """The agent searches its wiki and sees only wiki pages."""
    _write_material(run)
    model = counter.wrap(
        FauxModelClient(
            call_script("c1", "search", {"query": "不确定性"}),
            final_script("搜到了。"),
        )
    )
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "在 wiki 里搜不确定性")

    run.equal(
        "the search query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "search")
    run.equal("search ran once", len(results), 1, TOOL_CONTRACT)
    content = str(results[0].get("content") or "") if results else ""
    meta = (results[0].get("meta") or {}) if results else {}
    run.check(
        "the entropy page is a hit",
        "[[信息熵]]" in content,
        content[:200],
        TOOL_CONTRACT,
    )
    run.check(
        "the hit comes with an excerpt",
        "excerpt:" in content,
        content[:200],
        TOOL_CONTRACT,
    )
    run.equal(
        "the search stayed in the wiki",
        meta.get("scope"),
        "wiki",
        TOOL_CONTRACT,
    )
    run.check(
        "at least one hit was counted",
        int(meta.get("hits") or 0) >= 1,
        str(meta),
        TOOL_CONTRACT,
    )
    run.check(
        "raw is not searched", "secret" not in content, "", TOOL_CONTRACT
    )


def _tool_text(messages: tuple[Any, ...], name: str) -> str:
    """The latest result of one tool in the conversation the model received."""
    for message in reversed(messages):
        if message.role == "tool" and message.name == name:
            return str(message.content or "")
    return ""


class _ContinueRead:
    """A scripted model that continues at the offset the tool advertised.

    The second call reads the first result's continuation note, so the check
    exercises the real ``nextOffset`` rather than an offset the test guessed.
    """

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.calls = 0
        self.offset = 0

    def stream(self, request):
        self.calls += 1
        yield ModelStartEvent("eval")
        if self.calls == 1:
            yield ModelToolCallEnd(
                ToolCall(
                    "c1",
                    "wiki_read",
                    json.dumps({"slug": self.slug}, ensure_ascii=False),
                )
            )
            yield ModelDoneEvent(
                AssistantMessage(stop_reason="tool_calls")
            )
            return
        if self.calls == 2:
            match = re.search(
                r"next offset=(\d+)",
                _tool_text(request.messages, "wiki_read"),
            )
            if match is None:
                yield ModelDoneEvent(
                    AssistantMessage(
                        blocks=(TextBlock("no continuation"),),
                        stop_reason="stop",
                    )
                )
                return
            self.offset = int(match.group(1))
            yield ModelToolCallEnd(
                ToolCall(
                    "c2",
                    "wiki_read",
                    json.dumps(
                        {"slug": self.slug, "offset": self.offset},
                        ensure_ascii=False,
                    ),
                )
            )
            yield ModelDoneEvent(
                AssistantMessage(stop_reason="tool_calls")
            )
            return
        yield ModelTextDelta("读完了。")
        yield ModelDoneEvent(
            AssistantMessage(
                blocks=(TextBlock("读完了。"),), stop_reason="stop"
            )
        )


def read_atom(run: CaseRun, counter: UsageCounter) -> None:
    """A long page is read to its tail through offset/limit."""
    script = _ContinueRead("长页")
    model = counter.wrap(script)
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "读完这个长页")

    run.equal(
        "the read query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "wiki_read")
    run.equal("wiki_read ran twice", len(results), 2, TOOL_CONTRACT)
    first = results[0] if results else {}
    second = results[1] if len(results) > 1 else {}
    first_meta = first.get("meta") or {}
    first_content = str(first.get("content") or "")
    second_meta = second.get("meta") or {}
    second_content = str(second.get("content") or "")
    run.check(
        "the default read was cut",
        first_meta.get("truncated") is True,
        str(first_meta),
        TOOL_CONTRACT,
    )
    run.check(
        "the first read returns body text",
        "头" in first_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the cut names the next offset",
        "next offset=" in first_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the tail is not in the first read",
        "尾部标记" not in first_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the second read used the advertised offset",
        script.offset > 0,
        str(script.offset),
        TOOL_CONTRACT,
    )
    run.check(
        "the second read starts where the first stopped",
        f"next offset={script.offset}" in first_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the offset read returns body text",
        "中" in second_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the offset read reaches the tail",
        "尾部标记" in second_content,
        "",
        TOOL_CONTRACT,
    )
    run.check(
        "the tail read is complete",
        second_meta.get("truncated") is not True,
        str(second_meta),
        TOOL_CONTRACT,
    )


LOCAL_ATOMS = (
    AtomCase(
        "atom-03-frame",
        "A fetched page is framed into bounded blocks",
        frame_atom,
    ),
    AtomCase(
        "atom-04-write",
        "A frame becomes a page with provenance and a maintained catalog",
        write_atom,
    ),
    AtomCase(
        "atom-05-search",
        "The agent searches the wiki and sees only its own pages",
        search_atom,
        pages=_SEARCH_PAGES,
    ),
    AtomCase(
        "atom-06-read",
        "A long page is read to its tail through offset and limit",
        read_atom,
        pages=_READ_PAGES,
    ),
)

#: What the V4 command runs by default; the live atoms live in ``live.py``
#: and run only under ``--live``.
ATOMS = LOCAL_ATOMS

__all__ = ["ATOMS", "LOCAL_ATOMS"]
