"""Shared fixtures for the writing-state tests."""

from __future__ import annotations

import json
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.tools import ToolContext
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_tools
from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingStateStore


def page_html(count: int = 4) -> str:
    """A page whose blocks are ``b1`` through ``b<count>``."""
    return (
        "<html><body><article>"
        + "".join(
            f'<p data-na-block="b{index}">段落 {index}</p>'
            for index in range(1, count + 1)
        )
        + "</article></body></html>"
    )


def search_cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="信息熵",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="test",
        )
    ]


def capture(html: str = "") -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="信息熵",
        markdown="段落 1 段落 2 段落 3 段落 4",
        html=html or page_html(),
        capture_method="test",
        fidelity="ok",
        reasons=(),
        http_status=200,
        char_count=100,
    )


def prepare_draft(
    tools,
    layout: ProjectLayout,
    *,
    wiki_file: str = "wiki/concepts/信息熵.md",
    body: str = "熵是信息的平均量。",
    blocks: tuple[str, ...] = ("b2", "b3"),
):
    """Search, select, frame and compose one draft page; return its ids."""
    tools.dispatch(
        "web_search",
        json.dumps({"query": "信息熵"}),
        ToolContext("s1"),
    )
    state = WritingStateStore(layout).load()
    hit_id = str(state.searches[0]["hits"][0]["id"])
    selected = tools.dispatch(
        "select_page",
        json.dumps({"hitId": hit_id}),
        ToolContext("s1"),
    )
    page_id = str(json.loads(selected.content)["pageId"])
    framed = tools.dispatch(
        "select_frame",
        json.dumps({"pageId": page_id, "blockIds": list(blocks)}),
        ToolContext("s1"),
    )
    frame_id = str(json.loads(framed.content)["frameId"])
    composed = tools.dispatch(
        "compose_wiki",
        json.dumps(
            {
                "wikiFile": wiki_file,
                "baseDraftVersion": 0,
                "sections": [
                    {
                        "id": "definition",
                        "heading": "定义",
                        "body": body,
                        "citations": [frame_id],
                    }
                ],
            }
        ),
        ToolContext("s1"),
    )
    draft_version = int(
        json.loads(composed.content)["draftVersion"]
    )
    return page_id, frame_id, draft_version


def write_draft(
    tools,
    *,
    wiki_file: str = "wiki/concepts/信息熵.md",
    draft_version: int = 1,
):
    """Publish the composed draft page through the tool."""
    return tools.dispatch(
        "write_wiki",
        json.dumps(
            {
                "wikiFile": wiki_file,
                "draftVersion": draft_version,
            }
        ),
        ToolContext("s1"),
    )


def writing_tools(
    layout: ProjectLayout,
    *,
    html: str = "",
    search=search_cards,
):
    """The session tools over a fake search and a fake capture."""
    selected_html = html or page_html()

    def fetch(_url: str) -> Capture:
        return capture(selected_html)

    with patch(
        "noteagent.api.session_access.rag_backend", return_value=None
    ):
        return session_tools(
            AppDeps(
                clock=FixedClock(),
                ids=SequentialIds(),
                web_search=search,
                web_fetch=fetch,
            ),
            layout,
        )
