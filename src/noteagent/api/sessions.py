"""The versioned session view: what was said, and what the log means.

The transcript is what the reader saw; ``derived`` is the same log read as the
model will see it. Both come from one projection, so a reading that changes in
one place changes in the other.
"""

from __future__ import annotations

from noteagent.agent.model import Message
from noteagent.agent.projection import TranscriptItem, project
from noteagent.agent.store import SessionStore, WaitStore, last_timestamp
from noteagent.agent.wait import wait_payload


def session_payload(
    store: SessionStore,
    session_id: str,
    *,
    busy: bool = False,
    waits: WaitStore | None = None,
) -> dict[str, object]:
    """One session, with its transcript and the state derived from its entries."""
    snapshot = store.load(session_id)
    projection = project(snapshot.entries)
    body: dict[str, object] = {
        "id": snapshot.id,
        "title": snapshot.title,
        "updatedAt": last_timestamp(snapshot.entries),
        "state": {"busy": busy},
        "messages": [_transcript_item(item) for item in projection.transcript],
        "derived": {
            "sections": dict(projection.sections),
            "summary": projection.summary,
            "messages": [_message_payload(item) for item in projection.messages],
            "diagnostics": [
                {
                    "kind": item.kind,
                    "entryId": item.entry_id,
                    "message": item.message,
                }
                for item in projection.diagnostics
            ],
        },
    }
    if waits is not None:
        wait = waits.current(session_id)
        body["wait"] = wait_payload(wait) if wait is not None else None
    return body


def _transcript_item(item: TranscriptItem) -> dict[str, object]:
    body: dict[str, object] = {
        "id": item.id,
        "role": item.role,
        "content": item.content,
        "createdAt": item.created_at,
        "isStep": item.is_step,
    }
    if item.cards:
        body["cards"] = [dict(card) for card in item.cards]
    if item.stop_reason:
        body["stopReason"] = item.stop_reason
    return body


def _message_payload(message: Message) -> dict[str, object]:
    body: dict[str, object] = {
        "role": message.role,
        "content": message.content,
    }
    if message.tool_calls:
        body["toolCalls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        body["toolCallId"] = message.tool_call_id
    if message.name:
        body["name"] = message.name
    return body
