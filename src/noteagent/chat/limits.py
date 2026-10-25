"""Per-project chat limits. Tune with the wiki's task complexity, not code edits."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import MAX_BRIEF_CHARS

LIMITS_VERSION = 1

# Operational defaults written into ``config/chat-limits.json`` on first use.
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_MAX_MESSAGES = 200
DEFAULT_MAX_CONTENT_CHARS = 8_000
DEFAULT_MAX_CONVERSATIONS = 40
DEFAULT_MAX_TITLE_CHARS = 80
DEFAULT_MAX_BRIEF_CHARS = MAX_BRIEF_CHARS
DEFAULT_MAX_INDEX_CHARS = 12_000
DEFAULT_MAX_OPEN_PAGES = 6
DEFAULT_MAX_PAGE_CHARS = 8_000
DEFAULT_MAX_PAGES_CHARS = 24_000
DEFAULT_MAX_TOOL_ROUNDS = 6

# Last-resort ceilings so a bad file cannot blow disk. Edit the JSON for real knobs.
_BOUNDS = {
    "historyLimit": (1, 200),
    "maxMessages": (1, 2_000),
    "maxContentChars": (200, 100_000),
    "maxConversations": (1, 200),
    "maxTitleChars": (16, 240),
    "maxBriefChars": (0, 40_000),
    "maxIndexChars": (200, 80_000),
    "maxOpenPages": (1, 30),
    "maxPageChars": (200, 40_000),
    "maxPagesChars": (200, 120_000),
    "maxToolRounds": (1, 20),
}


@dataclass(frozen=True, slots=True)
class ChatLimits:
    history_limit: int
    max_messages: int
    max_content_chars: int
    max_conversations: int
    max_title_chars: int
    max_brief_chars: int
    max_index_chars: int
    max_open_pages: int
    max_page_chars: int
    max_pages_chars: int
    max_tool_rounds: int


def limits_bounds() -> dict[str, dict[str, int]]:
    """The range each knob may take.

    Sent with the values so that whoever shows the knobs can say what is wrong with
    one without keeping a second copy of these numbers: the ranges here are the ones
    this file enforces, so anything else would be a guess that can drift.
    """
    return {key: {"min": low, "max": high} for key, (low, high) in _BOUNDS.items()}


def default_limits() -> ChatLimits:
    return ChatLimits(
        history_limit=DEFAULT_HISTORY_LIMIT,
        max_messages=DEFAULT_MAX_MESSAGES,
        max_content_chars=DEFAULT_MAX_CONTENT_CHARS,
        max_conversations=DEFAULT_MAX_CONVERSATIONS,
        max_title_chars=DEFAULT_MAX_TITLE_CHARS,
        max_brief_chars=DEFAULT_MAX_BRIEF_CHARS,
        max_index_chars=DEFAULT_MAX_INDEX_CHARS,
        max_open_pages=DEFAULT_MAX_OPEN_PAGES,
        max_page_chars=DEFAULT_MAX_PAGE_CHARS,
        max_pages_chars=DEFAULT_MAX_PAGES_CHARS,
        max_tool_rounds=DEFAULT_MAX_TOOL_ROUNDS,
    )


def limits_to_dict(limits: ChatLimits) -> dict[str, Any]:
    return {
        "version": LIMITS_VERSION,
        "historyLimit": limits.history_limit,
        "maxMessages": limits.max_messages,
        "maxContentChars": limits.max_content_chars,
        "maxConversations": limits.max_conversations,
        "maxTitleChars": limits.max_title_chars,
        "maxBriefChars": limits.max_brief_chars,
        "maxIndexChars": limits.max_index_chars,
        "maxOpenPages": limits.max_open_pages,
        "maxPageChars": limits.max_page_chars,
        "maxPagesChars": limits.max_pages_chars,
        "maxToolRounds": limits.max_tool_rounds,
    }


def limits_from_dict(raw: dict[str, Any]) -> ChatLimits:
    if not isinstance(raw, dict):
        raise ValueError("chat limits must be an object")
    return ChatLimits(
        history_limit=_int_field(raw, "historyLimit", DEFAULT_HISTORY_LIMIT),
        max_messages=_int_field(raw, "maxMessages", DEFAULT_MAX_MESSAGES),
        max_content_chars=_int_field(raw, "maxContentChars", DEFAULT_MAX_CONTENT_CHARS),
        max_conversations=_int_field(raw, "maxConversations", DEFAULT_MAX_CONVERSATIONS),
        max_title_chars=_int_field(raw, "maxTitleChars", DEFAULT_MAX_TITLE_CHARS),
        max_brief_chars=_int_field(raw, "maxBriefChars", DEFAULT_MAX_BRIEF_CHARS),
        max_index_chars=_int_field(raw, "maxIndexChars", DEFAULT_MAX_INDEX_CHARS),
        max_open_pages=_int_field(raw, "maxOpenPages", DEFAULT_MAX_OPEN_PAGES),
        max_page_chars=_int_field(raw, "maxPageChars", DEFAULT_MAX_PAGE_CHARS),
        max_pages_chars=_int_field(raw, "maxPagesChars", DEFAULT_MAX_PAGES_CHARS),
        max_tool_rounds=_int_field(raw, "maxToolRounds", DEFAULT_MAX_TOOL_ROUNDS),
    )


def load_limits(layout: ProjectLayout) -> ChatLimits:
    path = layout.chat_limits
    if not path.is_file() and layout.legacy_chat_limits.is_file():
        path = layout.legacy_chat_limits
    if not path.is_file():
        limits = default_limits()
        save_limits(layout, limits)
        return limits
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"chat limits are not valid JSON: {err}") from err
    return limits_from_dict(raw)


def save_limits(layout: ProjectLayout, limits: ChatLimits) -> None:
    layout.config.mkdir(parents=True, exist_ok=True)
    payload = limits_to_dict(limits)
    tmp = layout.chat_limits.with_name(layout.chat_limits.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(layout.chat_limits)


def _int_field(raw: dict[str, Any], key: str, default: int) -> int:
    value = raw.get(key, default)
    if value is None:
        value = default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    low, high = _BOUNDS[key]
    if value < low or value > high:
        raise ValueError(f"{key} must be {low}–{high}")
    return value
