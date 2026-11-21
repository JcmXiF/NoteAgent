"""Shared fixtures for the writing-state tests."""

from __future__ import annotations

from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_tools
from noteagent.project.layout import ProjectLayout


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
