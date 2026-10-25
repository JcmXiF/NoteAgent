"""HTTP helpers for project chat sessions."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from noteagent.api.sse import iter_job_sse
from noteagent.chat.limits import (
    ChatLimits,
    limits_bounds,
    limits_from_dict,
    limits_to_dict,
    load_limits,
    save_limits,
)
from noteagent.chat.query import run_query
from noteagent.chat.store import (
    ChatConversation,
    ChatSummary,
    conversation_from_dict,
    conversation_to_dict,
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    rename_conversation,
    save_conversation,
)
from noteagent.project.layout import ProjectLayout


def limits_payload(limits: ChatLimits) -> dict[str, Any]:
    data = limits_to_dict(limits)
    data.pop("version", None)
    # The ranges travel with the values: a view that shows these knobs has to be able
    # to say what is wrong with one, and the ranges are the core's to decide.
    data["bounds"] = limits_bounds()
    return data


def summary_payload(item: ChatSummary) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "updatedAt": item.updated_at,
        "messageCount": item.message_count,
    }


def conversation_payload(conversation: ChatConversation) -> dict[str, Any]:
    data = conversation_to_dict(conversation)
    return {
        "id": data["id"],
        "title": data["title"],
        "updatedAt": data["updatedAt"],
        "messages": data["messages"],
    }


def list_chats_payload(layout: ProjectLayout) -> dict[str, Any]:
    return {
        "limits": limits_payload(load_limits(layout)),
        "conversations": [summary_payload(item) for item in list_conversations(layout)],
    }


def get_limits_payload(layout: ProjectLayout) -> dict[str, Any]:
    return limits_payload(load_limits(layout))


def put_limits_payload(layout: ProjectLayout, body: dict[str, Any]) -> dict[str, Any]:
    limits = limits_from_dict(body)
    save_limits(layout, limits)
    return limits_payload(load_limits(layout))


def get_conversation_payload(layout: ProjectLayout, conversation_id: str) -> dict[str, Any]:
    return conversation_payload(load_conversation(layout, conversation_id))


def put_conversation_payload(
    layout: ProjectLayout,
    conversation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    load_conversation(layout, conversation_id)
    limits = load_limits(layout)
    conversation = conversation_from_dict(
        body,
        conversation_id=conversation_id,
        limits=limits,
        enforce_limits=True,
    )
    saved = save_conversation(layout, conversation, limits=limits)
    return conversation_payload(saved)


def post_conversation_payload(
    layout: ProjectLayout,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = ""
    if body and isinstance(body.get("title"), str):
        title = body["title"]
    created = create_conversation(layout, title)
    return conversation_payload(created)


def delete_conversation_payload(
    layout: ProjectLayout,
    conversation_id: str,
) -> dict[str, Any]:
    delete_conversation(layout, conversation_id)
    return list_chats_payload(layout)


def rename_conversation_payload(
    layout: ProjectLayout,
    conversation_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    title = body.get("title", "")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    return conversation_payload(rename_conversation(layout, conversation_id, title))


def iter_chat_query_sse(
    layout: ProjectLayout,
    conversation_id: str,
    content: str,
) -> Iterator[bytes]:
    """The answer as it is written, then the saved conversation."""

    def worker(on_status, cancel, emit):
        def on_token(token: str) -> None:
            emit("token", {"text": token})

        def on_reset() -> None:
            # A round that turned out to be a tool call was not writing the answer,
            # so what it wrote is taken back rather than left in front of the reader.
            emit("token", {"text": "", "reset": True})

        def on_observation(observation) -> None:
            # What the assistant looked at, as data rather than prose: a card to open
            # belongs in the interface, and reading it back out of the answer only
            # works when the model happens to name it.
            emit("observation", observation.as_event())

        return conversation_payload(
            run_query(
                layout,
                conversation_id,
                content,
                on_token=on_token,
                on_reset=on_reset,
                on_observation=on_observation,
                on_status=on_status,
                cancel=cancel,
            )
        )

    return iter_job_sse(worker, default_step_id="answer")
