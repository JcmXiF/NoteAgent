"""Keeping a long session inside the model's window.

The log stays append-only: deterministic trimming changes only the prompt, and
an LLM summary is written as a ``compaction`` entry. The entries before the cut
are still in the file, still auditable, and still the truth if a summary turns
out to be wrong.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from noteagent.agent.model import Message
from noteagent.agent.projection import pair_start
from noteagent.agent.store import Entry


@dataclass(frozen=True, slots=True)
class CompactionLimits:
    """How much history the prompt may carry, and how it is shed."""

    #: The model's window. v1 has no model catalog, so this is a project default.
    context_tokens: int = 32_000
    #: Room for the answer and for the estimate being wrong.
    reserve_tokens: int = 8_000
    #: Recent history that stays verbatim after a summary.
    keep_recent_tokens: int = 12_000
    #: The summary request's own output limit.
    max_summary_tokens: int = 2_000
    #: A provider token is roughly this many characters. The estimate only has
    #: to be close enough to decide *when* to compact, not to bill anyone.
    chars_per_token: int = 4
    #: How much of an old tool result survives deterministic trimming.
    tool_result_chars: int = 800

    @property
    def threshold_tokens(self) -> int:
        return max(1, self.context_tokens - self.reserve_tokens)


def estimate_tokens(
    messages: Sequence[Message],
    chars_per_token: int = 4,
) -> int:
    """A cheap size estimate for a prompt, in provider-token order."""
    chars = 0
    for message in messages:
        chars += len(message.content)
        for call in message.tool_calls:
            chars += len(call.arguments)
    if chars <= 0:
        return 0
    return max(1, math.ceil(chars / max(1, chars_per_token)))


def trim_tool_results(
    messages: Sequence[Message], limits: CompactionLimits
) -> tuple[Message, ...]:
    """Cut old tool output before spending a model call on a summary.

    Everything outside the recent window is truncated to its opening words plus
    a marker saying how much was omitted. A recent tool result is kept whole;
    a user's question and the assistant's conclusions are never touched.
    """
    if not messages:
        return ()
    seen = 0
    trimmed: list[Message] = []
    for message in reversed(messages):
        tokens = estimate_tokens((message,), limits.chars_per_token)
        if (
            message.role == "tool"
            and seen >= limits.keep_recent_tokens
            and len(message.content) > limits.tool_result_chars
        ):
            omitted = len(message.content) - limits.tool_result_chars
            marker = f"\n[tool result truncated: {omitted} chars omitted]"
            trimmed.append(
                replace(
                    message,
                    content=message.content[: limits.tool_result_chars] + marker,
                )
            )
        else:
            trimmed.append(message)
        seen += tokens
    trimmed.reverse()
    return tuple(trimmed)


def choose_cut(entries: Sequence[Entry], limits: CompactionLimits) -> str | None:
    """The first entry the prompt keeps, or ``None`` when nothing can be shed.

    The recent window is measured from the tail. A cut that lands on a tool
    result moves back to the call it answers, so a pair is never split. The
    latest job result is kept even when it sits outside the recent window,
    because the model must still know what the last collaboration job did.
    """
    if not entries:
        return None
    total = 0
    recent_start = len(entries)
    for index in range(len(entries) - 1, -1, -1):
        tokens = _entry_tokens(entries[index], limits.chars_per_token)
        if total + tokens > limits.keep_recent_tokens and recent_start < len(entries):
            break
        total += tokens
        recent_start = index
    if recent_start <= 0:
        return None

    cut = pair_start(entries, recent_start)

    # ADR-0005: the turns from the wait's proposal through its result are what
    # a resume needs; keep them even when they sit outside the recent window.
    anchor = _last_wait_start(entries)
    if anchor is None:
        anchor = _last_job_result(entries)
    if anchor is not None and anchor < cut:
        cut = anchor

    # The cut must name a message (or a job result). A cut on a compaction or a
    # silent event would start the next prompt after the live request.
    cut = _message_boundary(entries, cut)
    if cut is None or cut <= 0:
        return None
    if not _has_summarizable(entries[:cut]):
        return None
    ident = entries[cut].get("id")
    return ident if isinstance(ident, str) and ident else None


def bounded_summary(text: str, limits: CompactionLimits) -> str:
    """The summary the log keeps: stripped and bounded by the output budget."""
    summary = text.strip()
    max_chars = max(200, limits.max_summary_tokens * limits.chars_per_token)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 24] + "\n[summary truncated]"
    return summary


def compaction_entry(
    summary: str,
    first_kept_id: str,
    tokens_before: int,
    usage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """The one entry compaction writes: a reading, not a rewrite of the log."""
    entry: dict[str, object] = {
        "type": "compaction",
        "summary": summary,
        "firstKeptEntryId": first_kept_id,
        "tokensBefore": tokens_before,
    }
    if usage:
        entry["usage"] = dict(usage)
    return entry


def render_transcript(messages: Sequence[Message]) -> str:
    """The old messages as one text, for the summary request."""
    lines: list[str] = []
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            calls = ", ".join(
                f"{call.name}({call.arguments})" for call in message.tool_calls
            )
            lines.append(f"assistant: {message.content} [calls: {calls}]")
        elif message.role == "tool":
            lines.append(f"tool {message.name or ''}: {message.content}")
        else:
            lines.append(f"{message.role}: {message.content}")
    return "\n\n".join(lines)


def _entry_tokens(entry: Entry, chars_per_token: int) -> int:
    kind = entry.get("type")
    chars = 0
    if kind == "message":
        content = entry.get("content")
        if isinstance(content, str):
            chars += len(content)
        calls = entry.get("toolCalls")
        if isinstance(calls, list):
            for call in calls:
                if isinstance(call, Mapping):
                    arguments = call.get("arguments")
                    if isinstance(arguments, str):
                        chars += len(arguments)
    elif kind == "wait_completed":
        summary = entry.get("summary")
        if isinstance(summary, str):
            chars += len(summary)
        artifacts = entry.get("artifacts")
        if isinstance(artifacts, list):
            for item in artifacts:
                if isinstance(item, Mapping):
                    chars += len(str(item.get("id") or ""))
                    chars += len(str(item.get("label") or ""))
    if chars <= 0:
        return 0
    return max(1, math.ceil(chars / max(1, chars_per_token)))


def _last_wait_start(entries: Sequence[Entry]) -> int | None:
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].get("type") == "wait_proposed":
            return index
    return None


def _last_job_result(entries: Sequence[Entry]) -> int | None:
    for index in range(len(entries) - 1, -1, -1):
        if entries[index].get("type") == "wait_completed":
            return index
    return None


def _message_boundary(entries: Sequence[Entry], index: int) -> int | None:
    """The nearest entry at or before *index* a prompt may start on."""
    for position in range(index, -1, -1):
        kind = entries[position].get("type")
        if kind == "wait_completed":
            return position
        if kind == "message" and entries[position].get("role") in (
            "user",
            "assistant",
        ):
            return position
    return None


def _has_summarizable(entries: Sequence[Entry]) -> bool:
    return any(
        entry.get("type") in ("message", "wait_completed") for entry in entries
    )
