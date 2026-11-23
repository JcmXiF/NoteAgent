"""The two live V4 atoms: a real web search and a real fetch.

These are the one part of V4 that leaves the machine. A missing provider or a
network failure is a diagnosable failure, kept in the live-smoke section and
the failure library; it never touches a local atom or a complex case's record.
"""

from __future__ import annotations

from evals.harness import CaseRun
from evals.quality.v4.runner import AtomCase, UsageCounter
from evals.quality.v4.scripted import (
    as_json,
    call_script,
    final_script,
    tool_results,
)
from evals.report import STATE, TOOL_CONTRACT

from noteagent.agent.clock import iso_timestamp
from noteagent.agent.faux import FauxModelClient
from noteagent.writing.state import WritingState, WritingStateStore

SEARCH_QUERY = "信息熵 定义"
WIKIPEDIA_ENTROPY = "https://zh.wikipedia.org/wiki/熵_(信息论)"


def _seed_wikipedia_hit(run: CaseRun) -> None:
    """The one hit the fetch atom selects; the fetch itself stays live."""
    store = WritingStateStore(run.layout, clock=run.clock)
    store.save(
        WritingState(
            searches=(
                {
                    "id": "search-wiki",
                    "query": SEARCH_QUERY,
                    "status": "ok",
                    "hits": [
                        {
                            "id": "hit-wiki",
                            "title": "熵 (信息论) - 维基百科",
                            "url": WIKIPEDIA_ENTROPY,
                            "snippet": "",
                            "rank": 1,
                        }
                    ],
                    "error": "",
                    "createdAt": iso_timestamp(run.clock),
                },
            )
        )
    )


def live_search_atom(run: CaseRun, counter: UsageCounter) -> None:
    """A live web search returns titled, linked hits."""
    model = counter.wrap(
        FauxModelClient(
            call_script("c1", "web_search", {"query": SEARCH_QUERY}),
            final_script("搜到了。"),
        )
    )
    # No web_search injected: the product's live search is the thing under test.
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "搜一下信息熵的定义")

    run.equal(
        "the live search query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "web_search")
    run.equal("web_search ran once", len(results), 1, TOOL_CONTRACT)
    payload = as_json(results[0].get("content") if results else "")
    run.equal(
        "the live search says ok",
        payload.get("status"),
        "ok",
        TOOL_CONTRACT,
    )
    count = int(payload.get("count") or 0)
    run.check(
        "the live search returned a hit",
        count >= 1,
        str(payload),
        TOOL_CONTRACT,
    )
    state = WritingStateStore(run.layout).load()
    search = next(
        (
            row
            for row in state.searches
            if row.get("id") == payload.get("searchId")
        ),
        {},
    )
    hits = search.get("hits")
    hits = hits if isinstance(hits, list) else []
    first = hits[0] if hits and isinstance(hits[0], dict) else {}
    run.check(
        "the first hit has a title",
        bool(str(first.get("title") or "").strip()),
        str(first),
        STATE,
    )
    run.check(
        "the first hit has a url",
        str(first.get("url") or "").startswith("http"),
        str(first),
        STATE,
    )
    failed = [
        row for row in state.searches if row.get("status") == "failed"
    ]
    if failed:
        run.check(
            "the search failure is diagnosable",
            False,
            str(failed[0].get("error") or ""),
            TOOL_CONTRACT,
        )


def live_fetch_atom(run: CaseRun, counter: UsageCounter) -> None:
    """The Wikipedia entropy page is fetched into readable blocks."""
    _seed_wikipedia_hit(run)
    model = counter.wrap(
        FauxModelClient(
            call_script("c1", "select_page", {"hitId": "hit-wiki"}),
            final_script("抓到了。"),
        )
    )
    # No web_fetch injected: the product's live fetch is the thing under test.
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "抓取维基百科的熵条目")

    run.equal(
        "the live fetch query is answered",
        reply.status_code,
        200,
        TOOL_CONTRACT,
    )
    results = tool_results(api, session, "select_page")
    run.equal("select_page ran once", len(results), 1, TOOL_CONTRACT)
    raw_content = str(results[0].get("content") or "") if results else ""
    payload = as_json(raw_content)
    run.equal(
        "the live fetch says ok",
        payload.get("status"),
        "ok",
        TOOL_CONTRACT,
    )
    # The failure row keeps its cause even when the tool answer is an error
    # string, so the report says why the fetch failed, not just that it did.
    store = WritingStateStore(run.layout)
    state = store.load()
    page = next(
        (
            row
            for row in state.selected_pages
            if row.get("url") == WIKIPEDIA_ENTROPY
        ),
        {},
    )
    page_id = str(page.get("id") or "")
    run.check(
        "the fetch made a page",
        bool(page_id),
        raw_content[:300] or str(payload),
        STATE,
    )
    run.check(
        "the fetch produced blocks",
        int(payload.get("blockCount") or 0) > 0,
        str(payload) if payload else raw_content[:300],
        STATE,
    )
    run.equal("the page is ready", page.get("status"), "ready", STATE)
    run.equal(
        "the page is the Wikipedia entry",
        page.get("url"),
        WIKIPEDIA_ENTROPY,
        STATE,
    )
    blocks = store.load_page_blocks(page_id) if page_id else []
    run.check(
        "the page has readable blocks",
        len(blocks) > 0,
        str(len(blocks)),
        STATE,
    )
    if page.get("status") != "ready":
        run.check(
            "the fetch failure is diagnosable",
            False,
            (
                f"{page.get('errorKind') or ''} "
                f"{page.get('error') or ''} {raw_content[:300]}"
            ).strip(),
            TOOL_CONTRACT,
        )


LIVE_ATOMS = (
    AtomCase(
        "live-01-web-search",
        "A live web search returns titled, linked hits",
        live_search_atom,
        share_settings=True,
    ),
    AtomCase(
        "live-02-web-fetch",
        "The Wikipedia entropy page is fetched into readable blocks",
        live_fetch_atom,
        share_settings=True,
    ),
)

__all__ = ["LIVE_ATOMS"]
