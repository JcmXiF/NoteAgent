"""Run one chat query as a tool loop, then persist the final assistant turn.

HTTP stays ``POST .../chats/{id}/query``. The tool messages themselves are not
stored: what the answer keeps is the short record of the places it looked at, so a
conversation reopened later still shows what the answer rested on.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from noteagent.chat.assemble import assemble_messages, render_observations
from noteagent.chat.limits import load_limits
from noteagent.chat.store import (
    ChatConversation,
    append_turn,
    load_conversation,
    save_conversation,
)
from noteagent.chat.tools import observe_tool, openai_tools, parse_tool_arguments
from noteagent.chat.tools.types import ToolObservation
from noteagent.chat.tools.run import TOOL_NAME as RUN_NAME
from noteagent.chat.tools.search import TOOL_NAME as SEARCH_NAME
from noteagent.chat.tools.skill import TOOL_NAME as SKILL_NAME
from noteagent.chat.tools.wiki_read import TOOL_NAME as WIKI_READ_NAME
from noteagent.chat.tools.write import TOOL_NAME as WRITE_NAME
from noteagent.llm.client import ChatMessage, LlmClient, LlmError, ToolCall
from noteagent.llm.config import LlmConfig, load_llm_config
from noteagent.project.layout import ProjectLayout
from noteagent.util.cancel import check_cancelled
from noteagent.util.run_log import emit_status


def run_query(
    layout: ProjectLayout,
    conversation_id: str,
    content: str,
    *,
    on_token: Callable[[str], None] | None = None,
    on_reset: Callable[[], None] | None = None,
    on_observation: Callable[[ToolObservation], None] | None = None,
    on_status: Callable[..., None] | None = None,
    llm_config: LlmConfig | None = None,
    cancel: threading.Event | None = None,
) -> ChatConversation:
    """Answers one question, reporting the answer as it is written.

    ``on_token`` is called for every piece of prose the model produces, as it
    produces it. A round that turns out to be a tool call was not writing the
    answer, so what it wrote is taken back through ``on_reset``: only the answer
    is ever left in front of the reader. When ``on_token`` is not given, each
    round is fetched in one piece instead.

    ``on_observation`` is called for every tool result, as it arrives, so that what
    the assistant looked at can be shown rather than only described in the answer.
    What is handed over is the result itself; what a caller does with it is theirs.
    """
    limits = load_limits(layout)
    conversation = load_conversation(layout, conversation_id)
    text = content.strip()
    last = conversation.messages[-1] if conversation.messages else None
    if last is None or last.role != "user" or last.content != text:
        conversation = append_turn(
            conversation, role="user", content=text, limits=limits
        )
        conversation = save_conversation(layout, conversation, limits=limits)

    check_cancelled(cancel)
    client = LlmClient(llm_config or load_llm_config())
    messages = assemble_messages(layout, conversation, limits)
    tools = openai_tools()
    answer = ""
    # What this turn looked at, in the order it saw it. Kept with the answer so a
    # conversation reopened tomorrow still says what the answer rested on.
    cards: list[dict[str, Any]] = []

    def report(observation: ToolObservation) -> None:
        cards.append(observation.as_event())
        if on_observation is not None:
            on_observation(observation)

    for step in range(limits.max_tool_rounds + 1):
        force_answer = step >= limits.max_tool_rounds
        if force_answer:
            emit_status(on_status, "answering from the pages opened", step_id="answer")
        elif step == 0:
            emit_status(
                on_status,
                "deciding whether to open, search, check, or plan",
                step_id="think",
            )
        else:
            emit_status(on_status, "continuing from the tool results", step_id="think")
        offered = None if force_answer or tools is None else tools
        try:
            turn = client.complete(
                messages,
                tools=offered,
                on_token=on_token,
                fallback_reasoning=False,
                cancel=cancel,
            )
        except LlmError as err:
            if not offered or "HTTP 400" not in str(err):
                raise
            emit_status(
                on_status,
                "this model does not support tool calls; answering directly",
                step_id="answer",
            )
            tools = None
            turn = client.complete(
                messages,
                tools=None,
                on_token=on_token,
                fallback_reasoning=False,
                cancel=cancel,
            )
        if turn.tool_calls and not force_answer:
            # This round was deciding what to look up, not answering, so whatever it
            # wrote on the way was not the answer and is taken back.
            if on_reset is not None:
                on_reset()
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=turn.content or "",
                    tool_calls=turn.tool_calls,
                )
            )
            for call in turn.tool_calls:
                check_cancelled(cancel)
                emit_status(on_status, _status_for_call(call), step_id="tool")
                observations = observe_tool(
                    call.name,
                    layout,
                    parse_tool_arguments(call.arguments),
                    limits,
                )
                # Reported one by one, before the round is rendered for the model:
                # a search gives several, and each is a separate thing the
                # assistant looked at.
                for observation in observations:
                    report(observation)
                rendered = render_observations(observations) or "(empty)"
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=rendered,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
            continue
        answer = turn.content.strip()
        break

    if not answer:
        raise ValueError("model returned no final answer")
    check_cancelled(cancel)
    emit_status(on_status, "saving the answer", step_id="answer")
    conversation = append_turn(
        conversation,
        role="assistant",
        content=answer,
        limits=limits,
        cards=cards,
    )
    return save_conversation(layout, conversation, limits=limits)


def _status_for_call(call: ToolCall) -> str:
    if call.name == WIKI_READ_NAME:
        args = parse_tool_arguments(call.arguments)
        targets = args.get("targets")
        n = len(targets) if isinstance(targets, list) else 0
        if n:
            return f"opening the pages it found ({n})"
        return "opening the pages it found"
    if call.name == SKILL_NAME:
        args = parse_tool_arguments(call.arguments)
        name = str(args.get("name") or "").strip()
        if name:
            return f"reading the skill {name}"
        return "reading the skill"
    if call.name == SEARCH_NAME:
        args = parse_tool_arguments(call.arguments)
        scope = str(args.get("scope") or "wiki").strip().lower()
        if scope == "web":
            return "searching the web"
        return "searching the notes"
    if call.name == RUN_NAME:
        args = parse_tool_arguments(call.arguments)
        job = str(args.get("job") or "").strip().lower()
        if job == "doctor":
            return "checking the wiki"
        if job == "fetch":
            return "fetching a preview"
        return "running the job"
    if call.name == WRITE_NAME:
        args = parse_tool_arguments(call.arguments)
        action = str(args.get("action") or "").strip().lower()
        if action == "propose_topic_map":
            return "planning the pages for this round"
        if action == "ingest_confirmed":
            return "writing the ticked pages"
        return "handling the write"
    return f"calling {call.name}"
