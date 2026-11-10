"""The deterministic behaviour evals, one user story per case.

Every case drives the real HTTP API: a question is asked over SSE, the log and
the session view are read back, and the model is the only scripted part. The
cases cover the behaviour a change to the agent harness is most likely to break:
reading the wiki, searching it, proposing work, entering a wait and resuming,
refusing a mixed batch, the tool-round budget, compaction, tool errors,
cancellation, and bringing old chats into the new store.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

from evals.harness import Case, CaseRun, Exchange

from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelEvent,
    ModelStartEvent,
    ModelTextDelta,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
)
from noteagent.agent.tools import ToolRegistry, ToolResult, ToolSpec

PLAN_PAYLOAD = {
    "intent": "熵",
    "candidates": [
        {"title": "Entropy", "slug": "concepts/entropy", "coverage": "write"}
    ],
    "primary": {
        "title": "Entropy",
        "slug": "concepts/entropy",
        "coverage": "write",
    },
    "alsoInSources": [],
    "notes": "",
}


def _tokens(reply: Exchange) -> str:
    return "".join(
        str(data.get("text") or "") for data in reply.events_of("token")
    )


def _only_event(reply: Exchange, kind: str) -> dict[str, object]:
    items = reply.events_of(kind)
    if len(items) != 1:
        raise AssertionError(
            f"expected one {kind} event, got {len(items)}"
        )
    return items[0]


def _tool_batch(*calls: tuple[str, str, str]) -> list[ModelEvent]:
    """One model turn that asks for several tools at once."""
    events: list[ModelEvent] = [ModelStartEvent("faux")]
    events.extend(
        ModelToolCallEnd(ToolCall(call_id, name, arguments))
        for call_id, name, arguments in calls
    )
    events.append(ModelDoneEvent(AssistantMessage(stop_reason="tool_calls")))
    return events


def _propose_script(
    job: str, interface: str, **fields: str
) -> list[ModelEvent]:
    arguments = {"job": job, "interface": interface, **fields}
    return FauxModelClient.tool_call(
        "w1", "propose", json.dumps(arguments, ensure_ascii=False)
    ).script


def wiki_read_and_answer(run: CaseRun) -> None:
    """A question about a page becomes a card, then an answer that read it."""
    model = FauxModelClient(
        FauxModelClient.tool_call(
            "t1", "wiki_read", '{"slug": "concepts/entropy"}'
        ).script,
        FauxModelClient.text("熵衡量不确定性。").script,
    )
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "什么是熵？")

    run.equal("the query is answered", reply.status_code, 200)
    run.equal(
        "the answer is streamed as the model writes it",
        _tokens(reply),
        "熵衡量不确定性。",
    )
    card = _only_event(reply, "observation")
    run.equal("the card names the page", card.get("slug"), "concepts/entropy")
    run.equal(
        "the card names the file it opened",
        card.get("path"),
        "wiki/concepts/entropy.md",
    )
    run.check(
        "the card leaves the page's words with the model",
        "熵衡量不确定性" not in json.dumps(card, ensure_ascii=False),
        "the observation carried the page body",
    )
    done = _only_event(reply, "done")
    run.equal("the run completed", done["run"]["status"], "completed")

    entries = api.entries(session)
    run.equal(
        "the log holds the question, the tool pair and the answer",
        [entry.get("role") for entry in entries],
        ["user", "assistant", "toolResult", "assistant"],
    )
    run.check(
        "the tool result carries the page to the model",
        "熵衡量不确定性" in str(entries[2].get("content") or ""),
        "the page body never reached the model",
    )
    trace = api.trace(session)
    run.check(
        "the trace names the tool that ran",
        any(
            item.get("type") == "tool_result"
            and item.get("tool") == "wiki_read"
            for item in trace
        ),
        "the trace has no tool_result record",
    )
    run.check(
        "the trace keeps the page's title out",
        "Entropy" not in json.dumps(trace, ensure_ascii=False),
        "the trace carried the page title",
    )


def search_and_answer(run: CaseRun) -> None:
    """A search is a locator card; the answer follows from what it found."""
    model = FauxModelClient(
        FauxModelClient.tool_call(
            "t1", "search", '{"query": "不确定性"}'
        ).script,
        FauxModelClient.text("熵和信息都围绕不确定性。").script,
    )
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "熵和信息有什么关系？")

    card = _only_event(reply, "observation")
    run.equal("the card is a wiki search", card.get("scope"), "wiki")
    run.equal("the card repeats the query", card.get("query"), "不确定性")
    run.check(
        "the search found both pages",
        isinstance(card.get("hits"), int) and card["hits"] >= 2,
        f"hits was {card.get('hits')!r}",
    )
    run.check(
        "the card carries locators, not excerpts",
        "excerpt" not in card,
        "the observation carried search excerpts",
    )
    run.equal(
        "the answer is the model's text",
        _tokens(reply),
        "熵和信息都围绕不确定性。",
    )
    run.equal(
        "the run completed",
        _only_event(reply, "done")["run"]["status"],
        "completed",
    )
    follow_up = model.requests[-1].messages
    run.check(
        "the model saw the pages the search named",
        any(
            message.role == "tool" and "[[concepts/entropy]]" in message.content
            for message in follow_up
        ),
        "the search result never reached the model",
    )


def propose_plan(run: CaseRun) -> None:
    """A proposal stops the run at a wait the human can enter elsewhere."""
    model = FauxModelClient(
        _propose_script(
            "plan",
            "source",
            intent="熵",
            reason="先把资料排成页",
        )
    )
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "整理一下熵的资料")

    wait = _only_event(reply, "wait")
    run.equal("the wait proposes a plan", wait.get("job"), "plan")
    run.equal("the plan belongs to the source screen", wait.get("interface"), "source")
    run.equal("the wait is only proposed", wait.get("status"), "proposed")
    run.equal("the human's action is named", wait.get("completion"), "start_plan")
    run.equal("the intent travels with the wait", wait.get("intent"), "熵")
    done = _only_event(reply, "done")
    run.equal("the run ends waiting", done["run"]["status"], "waiting")

    view = api.get_session(session)
    run.equal(
        "the session view carries the same wait",
        view["wait"]["waitId"],
        wait["waitId"],
    )
    run.equal("the session is not busy", view["state"]["busy"], False)
    entries = api.entries(session)
    run.equal(
        "the log holds one proposed wait",
        [entry.get("type") for entry in entries if entry.get("type") == "wait_proposed"],
        ["wait_proposed"],
    )
    run.equal(
        "a proposal is not a tool call",
        [entry for entry in entries if entry.get("role") == "toolResult"],
        [],
    )
    run.equal(
        "the proposal took one model turn",
        len(model.requests),
        1,
    )


def wait_resume(run: CaseRun) -> None:
    """A job entered from its own screen completes the wait and resumes the chat."""
    model = FauxModelClient(
        _propose_script("plan", "source", intent="熵"),
        FauxModelClient.text("计划完成，继续。").script,
    )
    api = run.start(model)
    session = api.create_session()
    asked = api.query(session, "整理一下熵的资料")
    wait = _only_event(asked, "wait")

    # The plan job is provider-backed. It is replaced at its own boundary so
    # the wait and resume path runs without a network or a key; everything
    # between the endpoint and the session is the real implementation.
    with (
        patch("noteagent.api.app.ensure_llm_configured"),
        patch(
            "noteagent.api.topic_gate.run_topic_map_job",
            return_value=PLAN_PAYLOAD,
        ),
    ):
        planned = api.request(
            "POST",
            "/api/projects/demo/topic-map",
            json={
                "intent": "熵",
                "sessionId": session,
                "waitId": wait["waitId"],
            },
        )

    run.equal("the plan job ran", planned.status_code, 200)
    result = _only_event(planned, "job_result")
    run.equal("the job succeeded", result.get("status"), "succeeded")
    run.equal(
        "the result says what the plan found",
        result.get("summary"),
        "planned 1 page(s): 1 write, 0 mention, 0 skip",
    )
    api.wait_for(
        lambda: any(
            entry.get("role") == "assistant"
            and entry.get("content") == "计划完成，继续。"
            for entry in api.entries(session)
        ),
        "the chat resumed after the plan",
    )

    entries = api.entries(session)
    completed = next(
        entry for entry in entries if entry.get("type") == "wait_completed"
    )
    run.equal("the completion names its wait", completed.get("waitId"), wait["waitId"])
    run.equal("the completion names the job", completed.get("job"), "plan")
    run.equal("the wait completed", completed.get("status"), "completed")
    run.equal(
        "the result names the planned page",
        completed.get("artifacts"),
        [{"kind": "write", "id": "concepts/entropy", "label": "Entropy"}],
    )
    run.equal(
        "the continuation ran once",
        len(
            [
                entry
                for entry in entries
                if entry.get("type") == "resume_started"
            ]
        ),
        1,
    )
    view = api.get_session(session)
    run.equal("the view reads the wait as completed", view["wait"]["status"], "completed")
    run.equal("the continuation was the second model turn", len(model.requests), 2)


def mixed_propose_rejected(run: CaseRun) -> None:
    """A proposal beside another call is refused whole; the run recovers."""
    model = FauxModelClient(
        _tool_batch(
            ("p1", "propose", '{"job": "plan", "interface": "source"}'),
            ("t1", "wiki_read", '{"slug": "concepts/entropy"}'),
        ),
        FauxModelClient.text("这次没有启动作业。").script,
    )
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "先计划再读一下熵")

    cards = reply.events_of("observation")
    run.equal("both calls are answered", len(cards), 2)
    for card in cards:
        run.check(
            f"{card.get('tool')} is refused",
            card.get("error") is True,
            "a refused call was not marked as an error",
        )
        diagnostics = card.get("diagnostics") or []
        run.equal(
            "the refusal names the mixed batch",
            diagnostics[0].get("code") if diagnostics else None,
            "propose_not_alone",
        )
    run.equal("no wait is proposed", reply.events_of("wait"), [])
    run.equal(
        "the run still answers",
        _only_event(reply, "done")["run"]["status"],
        "completed",
    )
    entries = api.entries(session)
    run.equal(
        "nothing was dispatched or recorded as a wait",
        [entry.get("type") for entry in entries if entry.get("type") == "wait_proposed"],
        [],
    )
    run.equal(
        "the answer is the last entry",
        entries[-1].get("content"),
        "这次没有启动作业。",
    )
    run.equal("the session is not waiting", api.get_session(session)["wait"], None)


def max_tool_rounds(run: CaseRun) -> None:
    """At the tool-round budget the model is asked once more, without tools."""
    scripts = [
        FauxModelClient.tool_call(
            f"t{index}", "wiki_read", '{"slug": "concepts/entropy"}'
        ).script
        for index in range(6)
    ]
    scripts.append(
        FauxModelClient.text("从读到的页面看，熵衡量不确定性。").script
    )
    model = FauxModelClient(*scripts)
    api = run.start(model)
    session = api.create_session()
    reply = api.query(session, "多读几遍熵")

    run.equal(
        "every round before the budget ran",
        len(reply.events_of("observation")),
        6,
    )
    run.equal(
        "the model was asked one more time",
        len(model.requests),
        7,
    )
    last = model.requests[-1]
    run.equal("the last turn offers no tools", last.tools, ())
    run.check(
        "the run says it is answering from the tool results",
        any(
            data.get("stepId") == "answer"
            and "tool results" in str(data.get("message") or "")
            for data in reply.events_of("status")
        ),
        "no answering-from-tool-results status was sent",
    )
    entries = api.entries(session)
    run.equal(
        "the log holds every tool result",
        len([entry for entry in entries if entry.get("role") == "toolResult"]),
        6,
    )
    run.equal(
        "the answer is committed",
        entries[-1].get("content"),
        "从读到的页面看，熵衡量不确定性。",
    )
    run.equal(
        "the run completed",
        _only_event(reply, "done")["run"]["status"],
        "completed",
    )


def compaction_threshold(run: CaseRun) -> None:
    """A long session is summarized, and the answer reads the summary."""
    summary = "较早的对话：读者反复问过熵是什么。"
    model = FauxModelClient(
        FauxModelClient.text(summary).script,
        FauxModelClient.text("现在总结如下。").script,
    )
    api = run.start(model)
    session = api.create_session()
    for index in range(60):
        run.store.append(
            session,
            {
                "type": "message",
                "role": "user",
                "content": f"old question {index} " + "x" * 1_200,
            },
        )
        run.store.append(
            session,
            {
                "type": "message",
                "role": "assistant",
                "content": f"old answer {index} " + "y" * 1_200,
            },
        )

    reply = api.query(session, "现在总结一下")

    run.equal("the answer is committed", _only_event(reply, "done")["run"]["status"], "completed")
    entries = api.entries(session)
    compaction = next(
        entry for entry in entries if entry.get("type") == "compaction"
    )
    run.equal("the summary is the model's text", compaction.get("summary"), summary)
    run.check(
        "the cut keeps a later entry",
        isinstance(compaction.get("firstKeptEntryId"), str)
        and bool(compaction.get("firstKeptEntryId")),
        "the compaction entry names no first kept entry",
    )
    run.check(
        "the prompt was over the threshold",
        isinstance(compaction.get("tokensBefore"), int)
        and compaction["tokensBefore"] >= 24_000,
        f"tokensBefore was {compaction.get('tokensBefore')!r}",
    )
    run.equal(
        "the session view derives the summary",
        api.get_session(session)["derived"]["summary"],
        summary,
    )

    run.equal("the summary took one model turn", len(model.requests), 2)
    summary_request = model.requests[0]
    run.equal(
        "the summary request is bounded",
        summary_request.max_tokens,
        2_000,
    )
    run.check(
        "the summary request reads the old transcript",
        any(
            message.role == "user" and "old question 0" in message.content
            for message in summary_request.messages
        ),
        "the oldest turns were missing from the summary request",
    )
    answer_request = model.requests[1]
    run.check(
        "the answer reads the summary",
        any(
            message.role == "system"
            and "Summary of earlier conversation:" in message.content
            and summary in message.content
            for message in answer_request.messages
        ),
        "the answer prompt did not carry the summary",
    )
    run.equal(
        "the answer is the last entry",
        entries[-1].get("content"),
        "现在总结如下。",
    )


def tool_error_recovers(run: CaseRun) -> None:
    """A tool that fails is an observation; the model can try again."""
    calls = {"count": 0}

    def read_page(arguments, _context):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("the page store is unavailable")
        return ToolResult(
            content="熵衡量不确定性。",
            meta={"title": "Entropy", "slug": arguments["slug"]},
        )

    tools = ToolRegistry(
        [
            ToolSpec(
                name="read_page",
                description="Read one wiki page.",
                parameters={
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
                handler=read_page,
            )
        ]
    )
    model = FauxModelClient(
        FauxModelClient.tool_call(
            "t1", "read_page", '{"slug": "concepts/entropy"}'
        ).script,
        FauxModelClient.tool_call(
            "t2", "read_page", '{"slug": "concepts/entropy"}'
        ).script,
        FauxModelClient.text("第一次失败，第二次读到了熵。").script,
    )
    api = run.start(model, tools=tools)
    session = api.create_session()
    reply = api.query(session, "读一下熵")

    cards = reply.events_of("observation")
    run.equal("both calls are answered", len(cards), 2)
    run.check("the failed call is an error card", cards[0].get("error") is True)
    diagnostics = cards[0].get("diagnostics") or []
    run.equal(
        "the failed call names its problem",
        diagnostics[0].get("code") if diagnostics else None,
        "handler_error",
    )
    run.check(
        "the failure is explained to the model",
        "page store is unavailable" in str(diagnostics[0].get("message") or ""),
        "the handler's failure never reached the model",
    )
    run.equal("the retry is a normal card", cards[1].get("error"), None)
    run.equal("the retry opened the page", cards[1].get("slug"), "concepts/entropy")
    run.equal(
        "the run recovered and answered",
        _only_event(reply, "done")["run"]["status"],
        "completed",
    )
    entries = api.entries(session)
    results = [entry for entry in entries if entry.get("role") == "toolResult"]
    run.equal("both results are in the log", len(results), 2)
    run.check(
        "the failed result carries the reason",
        "the page store is unavailable" in str(results[0].get("content") or ""),
        "the tool error was not committed",
    )
    run.check(
        "the retry carries the page",
        "熵衡量不确定性" in str(results[1].get("content") or ""),
        "the retried result was empty",
    )
    run.equal(
        "the answer is the last entry",
        entries[-1].get("content"),
        "第一次失败，第二次读到了熵。",
    )


class GatedModel:
    """A model that starts an answer and waits to be let go."""

    def __init__(self, first: str, rest: str = "") -> None:
        self.first = first
        self.rest = rest
        self.started = threading.Event()
        self.release = threading.Event()

    def stream(self, request):
        self.started.set()
        yield ModelStartEvent("faux")
        yield ModelTextDelta(self.first)
        self.release.wait(5)
        if self.rest:
            yield ModelTextDelta(self.rest)
        yield ModelDoneEvent(
            AssistantMessage(
                blocks=(TextBlock(self.first + self.rest),),
                stop_reason="stop",
            )
        )


def cancel_keeps_partial(run: CaseRun) -> None:
    """Stopping a run keeps what was already written, marked as aborted."""
    model = GatedModel("部分", rest="剩余")
    api = run.start(model)
    session = api.create_session()
    holder: dict[str, object] = {}

    def ask() -> None:
        try:
            holder["reply"] = api.query(session, "写一段解释")
        except Exception as err:  # noqa: BLE001 - reported by the case
            holder["error"] = err

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    run.check(
        "the answer started streaming",
        model.started.wait(timeout=5),
        "the model was never asked",
    )
    cancelled = api.cancel(session)
    model.release.set()
    worker.join(timeout=10)

    run.check(
        "the query request finished",
        not worker.is_alive(),
        "the query stayed blocked after the cancel",
    )
    run.equal(
        "the stop was accepted",
        cancelled.json().get("cancelled"),
        True,
    )
    reply = holder.get("reply")
    if not isinstance(reply, Exchange):
        raise AssertionError(f"the query never answered: {holder.get('error')}")
    error = _only_event(reply, "error")
    run.equal("the run ends cancelled", error.get("code"), "cancelled")
    run.equal("only the written part was streamed", _tokens(reply), "部分")

    entries = api.entries(session)
    run.equal(
        "the log keeps the question and the aborted answer",
        [entry.get("role") for entry in entries],
        ["user", "assistant"],
    )
    run.equal("the partial is what was streamed", entries[-1].get("content"), "部分")
    run.equal("the partial is marked aborted", entries[-1].get("stopReason"), "aborted")
    run.equal("the session is idle again", api.get_session(session)["state"]["busy"], False)


def old_chats_migrate(run: CaseRun) -> None:
    """An old chat is listed and read as a session, without losing its cards."""
    api = run.start(FauxModelClient.text("unused"))
    run.layout.chats_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "version": 1,
        "id": "c-1",
        "title": "熵",
        "updatedAt": "2026-11-09T10:00:02Z",
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "content": "什么是熵？",
                "createdAt": "2026-11-09T10:00:01Z",
            },
            {
                "id": "m2",
                "role": "assistant",
                "content": "熵衡量不确定性。",
                "createdAt": "2026-11-09T10:00:02Z",
                "cards": [
                    {
                        "tool": "wiki_read",
                        "title": "Entropy",
                        "slug": "concepts/entropy",
                    }
                ],
            },
        ],
    }
    (run.layout.chats_dir / "c-1.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )

    listed = api.request("GET", "/api/v1/sessions")
    body = listed.json()
    run.equal("the old chat was migrated", body["migration"]["migrated"], 1)
    run.equal(
        "the list shows the migrated session",
        [(item["id"], item["title"]) for item in body["sessions"]],
        [("c-1", "熵")],
    )
    view = api.get_session("c-1")
    run.equal(
        "the transcript keeps both messages",
        [message["role"] for message in view["messages"]],
        ["user", "assistant"],
    )
    run.equal(
        "the answer keeps its cards",
        view["messages"][1]["cards"][0]["slug"],
        "concepts/entropy",
    )
    entries = api.entries("c-1")
    run.equal(
        "the new store holds the messages",
        [entry.get("role") for entry in entries],
        ["user", "assistant"],
    )
    run.equal(
        "the cards moved into the entry",
        entries[1]["meta"]["cards"][0]["slug"],
        "concepts/entropy",
    )
    run.check(
        "the old file is left in place",
        (run.layout.chats_dir / "c-1.json").is_file(),
        "the migration removed the old chat",
    )
    again = api.request("GET", "/api/v1/sessions").json()
    run.check(
        "a second list does not migrate again",
        "migration" not in again,
        "the second list reported another migration",
    )


CASES: tuple[Case, ...] = (
    Case(
        "wiki_read_and_answer",
        "A wiki page is opened, shown as a card, and answered from.",
        wiki_read_and_answer,
    ),
    Case(
        "search_and_answer",
        "A wiki search returns locators the answer is built from.",
        search_and_answer,
    ),
    Case(
        "propose_plan",
        "A plan proposal ends the run at a wait the source screen can enter.",
        propose_plan,
    ),
    Case(
        "wait_resume",
        "A plan entered from its own screen completes the wait and resumes the chat.",
        wait_resume,
    ),
    Case(
        "mixed_propose_rejected",
        "A proposal beside another call is refused whole and the run recovers.",
        mixed_propose_rejected,
    ),
    Case(
        "max_tool_rounds",
        "At the tool-round budget the model answers once more without tools.",
        max_tool_rounds,
    ),
    Case(
        "compaction_threshold",
        "A long session is summarized at the threshold and the prompt reads it.",
        compaction_threshold,
    ),
    Case(
        "tool_error_recovers",
        "A refused tool call is an observation and the model corrects it.",
        tool_error_recovers,
    ),
    Case(
        "cancel_keeps_partial",
        "Stopping a run keeps the part already written, marked aborted.",
        cancel_keeps_partial,
    ),
    Case(
        "old_chats_migrate",
        "An old chat is listed and read as a session, cards included.",
        old_chats_migrate,
    ),
)

__all__ = ["CASES"]
