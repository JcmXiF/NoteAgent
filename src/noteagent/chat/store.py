"""Persist multiple conversations per project under ``.noteagent/chats/``."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noteagent.chat.limits import ChatLimits, load_limits
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.link_syntax import find_wikilinks

CHAT_VERSION = 1
ALLOWED_ROLES = frozenset({"user", "assistant"})
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_RESERVED_IDS = frozenset({"index"})

#: How much of what a turn looked at is kept with it. A turn can search six times
#: and open as many pages; what the reader needs afterwards is the places, not an
#: unbounded log, and a conversation file is read whole before it is drawn.
MAX_CARDS_PER_TURN = 40
MAX_CARD_TEXT = 400

#: What an unnamed conversation used to be stored as. It was also the sentinel for
#: "not named yet", so a file carrying it means the same thing as an empty title —
#: and reading one that way is the whole of the migration.
LEGACY_DEFAULT_TITLE = "新对话"


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """One visible user or assistant message. System / tool turns are not stored here."""

    id: str
    role: str
    content: str
    created_at: str
    """
    What the answer was built from, as the interface showed it.

    A card is the same thing the live answer offers under itself — a page it read, a
    result it found — kept with the message so reopening the conversation still says
    what the answer rested on. Only an assistant turn has them, and a turn from an
    older file has none.
    """
    cards: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ChatConversation:
    id: str
    title: str
    updated_at: str
    messages: tuple[ChatTurn, ...]


@dataclass(frozen=True, slots=True)
class ChatSummary:
    id: str
    title: str
    updated_at: str
    message_count: int


def empty_conversation(conversation_id: str, title: str = "") -> ChatConversation:
    """A conversation with nothing said in it yet.

    An empty title means it has not been named; whoever shows it says so in the
    reader's own language rather than reading a name out of the file.
    """
    return ChatConversation(
        id=conversation_id,
        title=title.strip(),
        updated_at="",
        messages=(),
    )


def new_conversation_id() -> str:
    return f"c-{secrets.token_hex(6)}"


def new_message_id() -> str:
    return f"m-{secrets.token_hex(6)}"


def append_turn(
    conversation: ChatConversation,
    *,
    role: str,
    content: str,
    limits: ChatLimits,
    cards: Iterable[dict[str, Any]] | None = None,
) -> ChatConversation:
    text = content.strip()
    if role not in ALLOWED_ROLES:
        raise ValueError("message role must be user or assistant")
    if not text:
        raise ValueError("message content must not be empty")
    if len(text) > limits.max_content_chars:
        raise ValueError(f"message content exceeds {limits.max_content_chars} characters")
    if len(conversation.messages) >= limits.max_messages:
        raise ValueError(f"at most {limits.max_messages} chat messages")
    turn = ChatTurn(
        id=new_message_id(),
        role=role,
        content=text,
        created_at=_now(),
        # A question is not built from anything; only an answer has sources.
        cards=_cards_from(cards) if role == "assistant" else (),
    )
    messages = conversation.messages + (turn,)
    title = conversation.title.strip()
    if not title:
        title = _title_from_messages(list(messages))
    return ChatConversation(
        id=conversation.id,
        title=title,
        updated_at=conversation.updated_at,
        messages=messages,
    )


def history_for_llm(
    conversation: ChatConversation,
    limits: ChatLimits | None = None,
) -> tuple[dict[str, str], ...]:
    """The last ``history_limit`` user/assistant turns, for one query call.

    System text and tool observations are assembled per request, never stored.
    """
    window = limits.history_limit if limits is not None else len(conversation.messages)
    turns = [
        {"role": item.role, "content": item.content}
        for item in conversation.messages
        if item.role in ALLOWED_ROLES
    ]
    return tuple(turns[-window:])


def list_conversations(layout: ProjectLayout) -> tuple[ChatSummary, ...]:
    items = [
        ChatSummary(
            id=conversation.id,
            title=conversation.title,
            updated_at=conversation.updated_at,
            message_count=len(conversation.messages),
        )
        for conversation in (_load_path(path) for path in _conversation_paths(layout))
    ]
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return tuple(items)


def load_conversation(layout: ProjectLayout, conversation_id: str) -> ChatConversation:
    ident = _require_id(conversation_id)
    path = layout.chat_path(ident)
    if not path.is_file():
        raise FileNotFoundError(f"chat session not found: {ident}")
    return _load_path(path)


def save_conversation(
    layout: ProjectLayout,
    conversation: ChatConversation,
    *,
    limits: ChatLimits | None = None,
) -> ChatConversation:
    ident = _require_id(conversation.id)
    bounds = limits if limits is not None else load_limits(layout)
    stamped = ChatConversation(
        id=ident,
        title=_clip_chars(conversation.title.strip(), bounds.max_title_chars),
        updated_at=_now(),
        messages=conversation.messages,
    )
    if len(stamped.messages) > bounds.max_messages:
        raise ValueError(f"at most {bounds.max_messages} chat messages")
    layout.chats_dir.mkdir(parents=True, exist_ok=True)
    _write_json(layout.chat_path(ident), conversation_to_dict(stamped))
    return stamped


def create_conversation(
    layout: ProjectLayout,
    title: str = "",
    *,
    limits: ChatLimits | None = None,
) -> ChatConversation:
    bounds = limits if limits is not None else load_limits(layout)
    if len(list_conversations(layout)) >= bounds.max_conversations:
        raise ValueError(f"at most {bounds.max_conversations} conversations")
    ident = new_conversation_id()
    while layout.chat_path(ident).is_file():
        ident = new_conversation_id()
    clipped = _clip_chars(title.strip(), bounds.max_title_chars)
    return save_conversation(
        layout,
        empty_conversation(ident, clipped),
        limits=bounds,
    )


def rename_conversation(
    layout: ProjectLayout,
    conversation_id: str,
    title: str,
    *,
    limits: ChatLimits | None = None,
) -> ChatConversation:
    """Gives a conversation a name, or gives it back the one it had.

    A name is one line of plain text, so runs of space and line breaks collapse: the
    list draws it as a single row whatever was typed.

    An empty name means the reader has not named it, which is the state a conversation
    starts in. So clearing a name puts the automatic one back — the first question —
    rather than leaving a conversation that is shown as "unnamed" while its question
    is right there.

    The time the conversation was last spoken in is left alone: renaming it is not
    talking in it, and the list is ordered by when it was last used.
    """
    conversation = load_conversation(layout, conversation_id)
    bounds = limits if limits is not None else load_limits(layout)
    name = _clip_chars(" ".join(title.split()), bounds.max_title_chars)
    if not name:
        name = _title_from_messages(list(conversation.messages))
    renamed = ChatConversation(
        id=conversation.id,
        title=name,
        updated_at=conversation.updated_at,
        messages=conversation.messages,
    )
    _write_json(layout.chat_path(conversation.id), conversation_to_dict(renamed))
    return renamed


def delete_conversation(layout: ProjectLayout, conversation_id: str) -> None:
    ident = _require_id(conversation_id)
    path = layout.chat_path(ident)
    if not path.is_file():
        raise FileNotFoundError(f"chat session not found: {ident}")
    path.unlink()


def conversation_to_dict(conversation: ChatConversation) -> dict[str, Any]:
    return {
        "version": CHAT_VERSION,
        "id": conversation.id,
        "title": conversation.title,
        "updatedAt": conversation.updated_at,
        "messages": [
            _turn_to_dict(item) for item in conversation.messages
        ],
    }


def _turn_to_dict(turn: ChatTurn) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": turn.id,
        "role": turn.role,
        "content": turn.content,
        "createdAt": turn.created_at,
    }
    # Left out when there is nothing: a conversation where nothing was looked at
    # reads exactly as it did before cards existed.
    if turn.cards:
        data["cards"] = [dict(card) for card in turn.cards]
    return data


def conversation_from_dict(
    raw: dict[str, Any],
    *,
    conversation_id: str | None = None,
    limits: ChatLimits | None = None,
    enforce_limits: bool = True,
) -> ChatConversation:
    if not isinstance(raw, dict):
        raise ValueError("chat session must be an object")

    ident = conversation_id or raw.get("id") or ""
    ident = _require_id(ident if isinstance(ident, str) else "")

    title = raw.get("title") or ""
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    max_title = limits.max_title_chars if limits is not None else 240
    title = _clip_chars(title.strip(), max_title)
    # A file from before titles were language-neutral: what it stores is what
    # "not named yet" used to look like, so it reads as unnamed.
    if title == LEGACY_DEFAULT_TITLE:
        title = ""

    items = raw.get("messages")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise ValueError("messages must be a list")
    if enforce_limits and limits is not None and len(items) > limits.max_messages:
        raise ValueError(f"at most {limits.max_messages} chat messages")

    messages: list[ChatTurn] = []
    seen_ids: set[str] = set()
    now = _now()
    max_content = limits.max_content_chars if limits is not None else None
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each message must be an object")
        turn = _turn_from_dict(
            item,
            fallback_created=now,
            max_content_chars=max_content if enforce_limits else None,
        )
        if turn.id in seen_ids:
            raise ValueError(f"duplicate message id: {turn.id}")
        seen_ids.add(turn.id)
        messages.append(turn)

    updated_at = raw.get("updatedAt") or ""
    if not isinstance(updated_at, str):
        raise ValueError("updatedAt must be a string")
    updated_at = updated_at.strip() or (messages[-1].created_at if messages else now)

    if not title:
        title = _title_from_messages(messages)

    return ChatConversation(
        id=ident,
        title=title,
        updated_at=updated_at,
        messages=tuple(messages),
    )


def _conversation_paths(layout: ProjectLayout) -> list[Path]:
    if not layout.chats_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in layout.chats_dir.iterdir():
        if not path.is_file() or not path.name.endswith(".json"):
            continue
        if path.name.endswith(".tmp"):
            continue
        stem = path.stem
        if stem in _RESERVED_IDS or not _ID_RE.fullmatch(stem):
            continue
        paths.append(path)
    return paths


def _load_path(path: Path) -> ChatConversation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"chat session is not valid JSON: {path.name}: {err}") from err
    return conversation_from_dict(
        raw,
        conversation_id=path.stem,
        enforce_limits=False,
    )


def _turn_from_dict(
    raw: dict[str, Any],
    *,
    fallback_created: str,
    max_content_chars: int | None,
) -> ChatTurn:
    ident = raw.get("id") or ""
    if not isinstance(ident, str) or not _ID_RE.fullmatch(ident.strip()):
        raise ValueError("message id must be 1–80 letters, digits, _ or -")
    role = raw.get("role") or ""
    if not isinstance(role, str) or role.strip() not in ALLOWED_ROLES:
        raise ValueError("message role must be user or assistant")
    content = raw.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("message content must be a string")
    content = content.strip()
    if not content:
        raise ValueError("message content must not be empty")
    if max_content_chars is not None and len(content) > max_content_chars:
        raise ValueError(f"message content exceeds {max_content_chars} characters")
    created = raw.get("createdAt") or ""
    if not isinstance(created, str):
        raise ValueError("createdAt must be a string")
    created = created.strip() or fallback_created
    return ChatTurn(
        id=ident.strip(),
        role=role.strip(),
        content=content,
        created_at=created,
        cards=_cards_from(raw.get("cards")),
    )


def _cards_from(raw: object) -> tuple[dict[str, Any], ...]:
    """What a turn looked at, as it is kept: small flat records, bounded in number.

    A card is what the interface draws, not what the tool held, so only plain values
    are kept and every string is clipped. The bounds matter because a conversation
    file is read whole before it is shown: a card list that grew without limit would
    make opening a conversation slower than reading the answer it belongs to.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("cards must be a list")
    cards: list[dict[str, Any]] = []
    for item in raw[:MAX_CARDS_PER_TURN]:
        card = _card(item)
        if card is not None:
            cards.append(card)
    return tuple(cards)


def _card(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    card: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            card[key] = value
        elif isinstance(value, str):
            card[key] = _clip_chars(value.strip(), MAX_CARD_TEXT)
    return card or None


def _require_id(value: str) -> str:
    ident = value.strip()
    if not _ID_RE.fullmatch(ident) or ident in _RESERVED_IDS:
        raise ValueError("conversation id must be 1–80 letters, digits, _ or -")
    return ident


def _title_from_messages(messages: list[ChatTurn]) -> str:
    """A name taken from the first thing the reader asked.

    Plain text: the question may be written with links to pages, and a title is a
    line of prose rather than markup, so what a link points at is used and the
    brackets are not.
    """
    for item in messages:
        if item.role == "user" and item.content:
            plain = _plain_text(item.content)
            if plain:
                return _clip_chars(plain, 42)
    return ""


def _plain_text(text: str) -> str:
    """Wikilinks read as the page they name, and runs of space become one space."""
    out: list[str] = []
    cursor = 0
    for link in find_wikilinks(text):
        out.append(text[cursor : link.start])
        out.append(link.display)
        cursor = link.end
    out.append(text[cursor:])
    return " ".join("".join(out).split())


def _clip_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
