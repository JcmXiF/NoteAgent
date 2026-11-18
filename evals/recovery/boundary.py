"""Infer where a run stopped from its transcript, and wait until it gets there.

The eval needs two things from a session file: the shape the crash left behind
(so a scenario can assert it hit its boundary) and a way to kill the server at
that shape. Both read the same committed entries; markers are pointers, and a
missing terminal marker is inferred from the last content entry, exactly as the
startup scan does it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ANSWER_COMMITTED",
    "EMPTY",
    "QUESTION_ONLY",
    "SETTLED_STATES",
    "STARTED",
    "TERMINAL",
    "TOOL_CALL_OPEN",
    "TOOL_RESULT_OPEN",
    "Observation",
    "classify_entries",
    "classify_file",
    "pending_calls",
    "wait_for_boundary",
]

EMPTY = "empty"
QUESTION_ONLY = "question_only"
STARTED = "started"
TOOL_CALL_OPEN = "tool_call_open"
TOOL_RESULT_OPEN = "tool_result_open"
ANSWER_COMMITTED = "answer_committed"
TERMINAL = "terminal"

#: Marker states that mean the run is settled; nothing after them is inferred.
SETTLED_STATES = frozenset(
    {"finished", "failed", "cancelled", "waiting", "abandoned", "interrupted"}
)


@dataclass(frozen=True, slots=True)
class Observation:
    """The boundary a poll observed."""

    boundary: str


def classify_entries(entries: Sequence[Mapping[str, Any]]) -> str:
    """The boundary of the latest run, inferred from committed entries."""
    last_marker = -1
    last_state = ""
    for index, entry in enumerate(entries):
        if entry.get("type") == "run_marker":
            last_marker = index
            last_state = str(entry.get("state") or "")

    if last_state in SETTLED_STATES:
        later = entries[last_marker + 1 :]
        if later and _last_role(later) == "user":
            return QUESTION_ONLY
        return TERMINAL

    if last_marker >= 0:
        content = entries[last_marker + 1 :]
        if not content:
            return STARTED
        return _classify_content(content)

    if not entries:
        return EMPTY
    return _classify_content(entries)


def classify_file(path: Path) -> str:
    """Read a session JSONL and classify it, tolerating a partial tail."""
    return classify_entries(read_entries(path))


def read_entries(path: Path) -> list[dict[str, Any]]:
    """The committed entries of a session file; malformed lines are skipped."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def wait_for_boundary(
    path: Path,
    target: str,
    *,
    timeout_s: float,
    poll_s: float = 0.005,
) -> Observation | None:
    """Poll *path* until it classifies as *target*, or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while True:
        entries = read_entries(path)
        boundary = classify_entries(entries)
        if boundary == target:
            return Observation(boundary)
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_s)


def pending_calls(entries: Sequence[Mapping[str, Any]]) -> set[str]:
    """Tool call ids the transcript shows without a committed result."""
    pending: set[str] = set()
    for entry in entries:
        if entry.get("type") != "message":
            continue
        role = entry.get("role")
        if role == "assistant":
            calls = entry.get("toolCalls") or []
            if isinstance(calls, list):
                for item in calls:
                    call_id = _call_id(item)
                    if call_id:
                        pending.add(call_id)
        elif role == "toolResult":
            call_id = entry.get("toolCallId")
            if isinstance(call_id, str):
                pending.discard(call_id)
    return pending


def _classify_content(entries: Sequence[Mapping[str, Any]]) -> str:
    pending = pending_calls(entries)
    last_kind = ""
    for entry in entries:
        if entry.get("type") != "message":
            continue
        role = entry.get("role")
        if role == "assistant":
            calls = entry.get("toolCalls") or []
            last_kind = "assistant_tools" if calls else "assistant"
        elif role == "toolResult":
            last_kind = "tool_result"
        elif role == "user":
            last_kind = "user"

    if pending:
        return TOOL_CALL_OPEN
    if last_kind == "tool_result":
        return TOOL_RESULT_OPEN
    if last_kind == "user":
        return QUESTION_ONLY
    if last_kind in {"assistant", "assistant_tools"}:
        return ANSWER_COMMITTED
    return EMPTY


def _last_role(entries: Sequence[Mapping[str, Any]]) -> str:
    for entry in reversed(entries):
        if entry.get("type") == "message":
            role = entry.get("role")
            return role if isinstance(role, str) else ""
    return ""


def _call_id(item: object) -> str:
    if isinstance(item, Mapping):
        value = item.get("id")
        if isinstance(value, str):
            return value
    return ""
