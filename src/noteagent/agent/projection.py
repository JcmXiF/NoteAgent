"""A pure function from committed entries to a prompt and a transcript.

The loop and the interface read the same log the same way. Nothing here writes:
the bytes are the record, and the projection is one reading of them, so a golden
entry list can pin the reading every time it changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from noteagent.agent.model import Message, ToolCall

#: What a tool call says when the run never got a result back. The model can see
#: that the call did not finish and decide whether to try again.
MISSING_RESULT = "missing_result"

#: How many artifacts one job result names in the prompt. The entry keeps them
#: all; the prompt gets a bounded reading.
MAX_RESULT_ARTIFACTS = 24

#: Entry types that contribute to the prompt through another step, or that the
#: transcript does not show. They are known, so they produce no warning.
_SILENT_TYPES = frozenset(
    {
        "compaction",
        "model_change",
        "reset",
        "resume_started",
        "session",
        "session_info",
        "thinking_level_change",
        "usage",
        "wait_cancelled",
        "wait_entered",
        "wait_proposed",
    }
)


@dataclass(frozen=True, slots=True)
class ProjectionDiagnostic:
    """Something the log said that the reading could not use as written."""

    kind: str
    entry_id: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class TranscriptItem:
    """One turn the interface draws. A tool result is not one of them."""

    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str = ""
    cards: tuple[Mapping[str, object], ...] = ()
    stop_reason: str = ""
    #: True when the assistant was calling a tool rather than answering; the
    #: preamble is not the answer, but it is still what the reader saw happen.
    is_step: bool = False


@dataclass(frozen=True, slots=True)
class Projection:
    """What the active entries say, in the three forms each reader needs."""

    sections: Mapping[str, str]
    messages: tuple[Message, ...]
    transcript: tuple[TranscriptItem, ...]
    summary: str = ""
    diagnostics: tuple[ProjectionDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class _Piece:
    entry_id: str
    message: Message | None
    is_tool_result: bool = False
    tool_call_id: str = ""


def project(entries: Sequence[Mapping[str, object]]) -> Projection:
    """Read *entries* as a prompt and a transcript.

    The latest reset chooses where the context starts; the latest compaction adds
    its summary and starts at the entry it kept; sections are replayed as patches;
    tool results are placed after the call they answer, with ``missing_result``
    standing in for one that never came.
    """
    records: list[Mapping[str, object]] = []
    diagnostics: list[ProjectionDiagnostic] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            records.append(entry)
        else:
            diagnostics.append(
                ProjectionDiagnostic(
                    "invalid_entry", "", f"entry {index} is not an object"
                )
            )

    reset_start = _after_reset(records)
    start, summary = _after_compaction(records, reset_start)

    sections: dict[str, str] = {}
    # Sections are not part of the compacted history: a cut drops messages, not
    # the identity, tools, skills and brief every prompt must carry. Replay
    # every patch after the last reset, whichever side of the cut it sits on.
    for index in range(reset_start, len(records)):
        entry = records[index]
        if entry.get("type") == "system_section":
            _apply_section_patch(sections, entry.get("sections"))
    pieces: list[_Piece] = []
    transcript: list[TranscriptItem] = []
    #: The step the current tool results belong to. A step with prose already
    #: has a row; a silent one gets its row when its first card arrives, so a
    #: run that stopped after a tool round still shows what it looked at.
    step_index: int | None = None
    step_calls: set[str] = set()
    step_id = ""
    step_created_at = ""

    for index in range(reset_start, len(records)):
        in_context = index >= start
        entry = records[index]
        kind = entry.get("type")
        entry_id = _entry_id(entry)
        if not isinstance(kind, str):
            if in_context:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "invalid_entry", entry_id, "entry has no type"
                    )
                )
            continue
        if kind == "system_section":
            # Already replayed before the cut, so that a compaction cannot drop
            # a section the prompt still needs.
            continue
        if kind == "wait_completed":
            # The job result is a message the model must see; it is not a turn
            # the reader wrote, so it has no transcript row of its own. The
            # interface shows it from the wait the session carries.
            if in_context:
                pieces.append(
                    _Piece(entry_id, Message("system", _job_result_text(entry)))
                )
            continue
        if kind == "message":
            # The transcript is the reader's whole log, so it keeps the turns
            # the prompt no longer carries; only the prompt starts at the cut.
            piece, item, problem = _message_piece(entry, entry_id, index)
            if problem and in_context:
                diagnostics.append(problem)
            if piece is not None and in_context:
                pieces.append(piece)
            if item is not None:
                transcript.append(item)
                if item.is_step:
                    step_index = len(transcript) - 1
                    step_id = item.id
                    step_created_at = item.created_at
                    step_calls = _call_ids(piece)
                else:
                    step_index = None
                    step_calls = set()
                continue
            if piece is not None and piece.message is not None and piece.message.tool_calls:
                # A step with no prose: it gets a row once a result has a card.
                step_index = None
                step_id = entry_id or f"entry-{index}"
                step_created_at = _string(entry.get("ts"))
                step_calls = _call_ids(piece)
                continue
            card = _tool_card(entry)
            call_id = entry.get("toolCallId")
            if (
                card is not None
                and isinstance(call_id, str)
                and call_id in step_calls
            ):
                if step_index is None:
                    step_index = len(transcript)
                    transcript.append(
                        TranscriptItem(
                            id=step_id,
                            role="assistant",
                            content="",
                            created_at=step_created_at,
                            cards=(card,),
                            is_step=True,
                        )
                    )
                else:
                    current = transcript[step_index]
                    transcript[step_index] = replace(
                        current, cards=current.cards + (card,)
                    )
            continue
        if kind == "context_edit":
            # v1 replays the log as written; an edit waits for the slice that
            # gives the interface a way to make one.
            if in_context:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "unsupported_entry",
                        entry_id,
                        "context_edit is not applied",
                    )
                )
            continue
        if kind in _SILENT_TYPES:
            continue
        if in_context:
            diagnostics.append(
                ProjectionDiagnostic(
                    "unknown_type", entry_id, f"unknown entry type: {kind}"
                )
            )

    messages, pair_diagnostics = _order_tool_pairs(pieces)
    diagnostics.extend(pair_diagnostics)
    return Projection(
        sections=sections,
        messages=tuple(messages),
        transcript=tuple(transcript),
        summary=summary,
        diagnostics=tuple(diagnostics),
    )


def _after_reset(records: Sequence[Mapping[str, object]]) -> int:
    """Where the latest reset says the context starts, or zero."""
    start = 0
    for index, entry in enumerate(records):
        if entry.get("type") != "reset":
            continue
        head = entry.get("head")
        if isinstance(head, str) and head != "self":
            found = next(
                (
                    position
                    for position, candidate in enumerate(records)
                    if candidate.get("id") == head
                ),
                None,
            )
            start = found if found is not None else index + 1
        else:
            start = index + 1
    return start


def _after_compaction(
    records: Sequence[Mapping[str, object]], start: int
) -> tuple[int, str]:
    """The cut the latest usable compaction kept, and its summary."""
    summary = ""
    for index in range(start, len(records)):
        entry = records[index]
        if entry.get("type") != "compaction":
            continue
        kept_id = entry.get("firstKeptEntryId")
        if not isinstance(kept_id, str):
            continue
        found = next(
            (
                position
                for position, candidate in enumerate(records)
                if position >= start and candidate.get("id") == kept_id
            ),
            None,
        )
        if found is None:
            continue
        start = pair_start(records, found)
        raw_summary = entry.get("summary")
        summary = raw_summary if isinstance(raw_summary, str) else ""
    return start, summary


def pair_start(records: Sequence[Mapping[str, object]], index: int) -> int:
    """Move a cut that landed on a tool result back to the call it answers."""
    entry = records[index]
    if entry.get("type") != "message" or entry.get("role") != "toolResult":
        return index
    call_id = entry.get("toolCallId")
    if not isinstance(call_id, str) or not call_id:
        return index
    for position in range(index - 1, -1, -1):
        candidate = records[position]
        if (
            candidate.get("type") != "message"
            or candidate.get("role") != "assistant"
        ):
            continue
        calls = candidate.get("toolCalls")
        if isinstance(calls, list) and any(
            isinstance(call, Mapping) and call.get("id") == call_id
            for call in calls
        ):
            return position
    return index


def _apply_section_patch(target: dict[str, str], raw: object) -> None:
    if not isinstance(raw, Mapping):
        return
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if value is None:
            target.pop(key, None)
        elif isinstance(value, str):
            target[key] = value


def _message_piece(
    entry: Mapping[str, object], entry_id: str, index: int
) -> tuple[_Piece | None, TranscriptItem | None, ProjectionDiagnostic | None]:
    raw_role = entry.get("role")
    role = "tool" if raw_role == "toolResult" else raw_role
    if role not in ("user", "assistant", "tool"):
        return (
            None,
            None,
            ProjectionDiagnostic(
                "invalid_entry", entry_id, f"unknown message role: {raw_role!r}"
            ),
        )
    content = entry.get("content")
    text = content if isinstance(content, str) else ""

    if role == "tool":
        call_id = entry.get("toolCallId")
        name = entry.get("name")
        message = Message(
            "tool",
            text,
            tool_call_id=call_id if isinstance(call_id, str) else "",
            name=name if isinstance(name, str) else "",
        )
        return (
            _Piece(
                entry_id,
                message,
                is_tool_result=True,
                tool_call_id=message.tool_call_id,
            ),
            None,
            None,
        )

    calls: list[ToolCall] = []
    raw_calls = entry.get("toolCalls")
    if isinstance(raw_calls, list):
        for raw_call in raw_calls:
            call = _tool_call(raw_call)
            if call is not None:
                calls.append(call)
    message = Message(role, text, tool_calls=tuple(calls))
    item = None
    if text.strip():
        item = TranscriptItem(
            id=entry_id or f"entry-{index}",
            role=role,
            content=text,
            created_at=_string(entry.get("ts")),
            cards=_cards(entry),
            stop_reason=_string(entry.get("stopReason")),
            is_step=bool(calls),
        )
    return _Piece(entry_id, message), item, None


def _tool_call(raw: object) -> ToolCall | None:
    if not isinstance(raw, Mapping):
        return None
    call_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(call_id, str) or not call_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    arguments = raw.get("arguments")
    if isinstance(arguments, str):
        text = arguments
    elif arguments is None:
        text = "{}"
    else:
        text = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    return ToolCall(call_id, name, text)


def _order_tool_pairs(
    pieces: Sequence[_Piece],
) -> tuple[list[Message], list[ProjectionDiagnostic]]:
    """Each call is followed by its result, or by ``missing_result``."""
    messages: list[Message] = []
    diagnostics: list[ProjectionDiagnostic] = []
    used: set[int] = set()

    for index, piece in enumerate(pieces):
        if index in used:
            continue
        if piece.is_tool_result:
            diagnostics.append(
                ProjectionDiagnostic(
                    "orphan_tool_result",
                    piece.entry_id,
                    f"tool result {piece.tool_call_id} has no call",
                )
            )
            continue
        if piece.message is None:
            continue
        messages.append(piece.message)
        for call in piece.message.tool_calls:
            found = next(
                (
                    position
                    for position in range(index + 1, len(pieces))
                    if position not in used
                    and pieces[position].is_tool_result
                    and pieces[position].tool_call_id == call.id
                ),
                None,
            )
            if found is None:
                messages.append(
                    Message(
                        "tool",
                        MISSING_RESULT,
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )
                diagnostics.append(
                    ProjectionDiagnostic(
                        "missing_result",
                        piece.entry_id,
                        f"tool call {call.id} has no result",
                    )
                )
            else:
                used.add(found)
                if pieces[found].message is not None:
                    messages.append(pieces[found].message)
    return messages, diagnostics


def _job_result_text(entry: Mapping[str, object]) -> str:
    """What the model reads after a waited-on job finished."""
    job = _string(entry.get("job")) or "job"
    status = _string(entry.get("status")) or "completed"
    lines = [f"Job result ({job}, {status})."]
    summary = _string(entry.get("summary"))
    if summary:
        lines.append(summary)
    raw_artifacts = entry.get("artifacts")
    if isinstance(raw_artifacts, list):
        items = [
            f"{_string(item.get('kind')) or 'item'}:{_string(item.get('id'))}"
            for item in raw_artifacts
            if isinstance(item, Mapping) and _string(item.get("id"))
        ]
        if items:
            lines.append("Artifacts: " + ", ".join(items[:MAX_RESULT_ARTIFACTS]))
    error = _string(entry.get("error"))
    if error:
        lines.append("Error: " + error)
    return " ".join(lines)


def _entry_id(entry: Mapping[str, object]) -> str:
    value = entry.get("id")
    return value if isinstance(value, str) else ""


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _cards(entry: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    meta = entry.get("meta")
    raw = meta.get("cards") if isinstance(meta, Mapping) else None
    if raw is None:
        raw = entry.get("cards")
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _call_ids(piece: _Piece | None) -> set[str]:
    if piece is None or piece.message is None:
        return set()
    return {call.id for call in piece.message.tool_calls}


def _tool_card(entry: Mapping[str, object]) -> Mapping[str, object] | None:
    """A tool result's own card, for the step that asked for it.

    A tool result is not a transcript row, but the card it carries is what the
    reader saw the step do. Keeping it on the step is what lets a reload — or a
    run that stopped after the tool round — show the pages it looked at.
    """
    meta = entry.get("meta")
    if not isinstance(meta, Mapping):
        return None
    tool = meta.get("tool")
    if not isinstance(tool, str) or not tool:
        return None
    return dict(meta)
