"""Bring the old ``chats/*.json`` conversations into the JSONL store.

The old file is never rewritten or removed: a conversation that fails to
migrate is still there to retry, and the one that succeeds leaves a copy the
reader could recover by hand. The marker only says the pass is done.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from noteagent.agent.clock import Clock, iso_timestamp
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.common import (
    RESERVED_SESSION_IDS,
    SESSION_ID_RE,
    SESSION_LOCK,
    session_line,
)

logger = logging.getLogger(__name__)

#: What an unnamed old conversation was stored as. It means "not named yet".
LEGACY_DEFAULT_TITLE = "新对话"
MIGRATION_MARKER = ".migrated"
#: v2 records the ids already handled, so deleting a migrated session does not
#: make the next pass bring it back from the old file.
MIGRATION_VERSION = 2


@dataclass(frozen=True, slots=True)
class LegacyMessage:
    id: str
    role: str
    content: str
    created_at: str
    cards: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True, slots=True)
class LegacyConversation:
    id: str
    title: str
    updated_at: str
    messages: tuple[LegacyMessage, ...]


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What the one migration pass did, for the list endpoint to report."""

    migrated: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {"migrated": len(self.migrated)}
        if self.failed:
            body["failed"] = [
                {"id": ident, "error": error}
                for ident, error in self.failed
            ]
        return body


def migrate_legacy_chats(
    layout: ProjectLayout, *, clock: Clock
) -> MigrationReport:
    """Convert every old chat once, keeping anything that fails.

    The marker records the ids already handled, not a single "done" flag: a
    migrated session may be deleted, and the old file must not bring it back.
    A failed file is left out of that set, so the next pass retries it.
    """
    chats = layout.chats_dir
    if not chats.is_dir():
        return MigrationReport()
    marker = chats / MIGRATION_MARKER

    with SESSION_LOCK:
        handled = _read_marker(marker)
        paths = sorted(
            path
            for path in chats.iterdir()
            if path.is_file()
            and path.suffix == ".json"
            and not path.name.endswith(".tmp")
        )
        migrated: list[str] = []
        skipped: list[str] = []
        failed: list[tuple[str, str]] = []
        for path in paths:
            ident = path.stem
            if (
                not SESSION_ID_RE.fullmatch(ident)
                or ident in RESERVED_SESSION_IDS
            ):
                failed.append((ident, "not a usable session id"))
                continue
            if ident in handled:
                skipped.append(ident)
                continue
            target = layout.session_path(ident)
            if target.is_file():
                handled.add(ident)
                skipped.append(ident)
                continue
            try:
                conversation = _load(path, ident)
                _write_session(layout, conversation, clock)
                handled.add(ident)
                migrated.append(ident)
            except Exception as err:  # noqa: BLE001 - reported per file
                logger.warning(
                    "old chat %s cannot be migrated: %s", path.name, err
                )
                failed.append((ident, str(err)))

        _write_marker(marker, handled, clock)
        return MigrationReport(
            tuple(migrated), tuple(skipped), tuple(failed)
        )


def _load(path: Path, ident: str) -> LegacyConversation:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ValueError(f"old chat is not valid JSON: {err}") from err
    if not isinstance(raw, dict):
        raise ValueError("old chat must be an object")

    title = raw.get("title") or ""
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    title = title.strip()
    if title == LEGACY_DEFAULT_TITLE:
        title = ""

    raw_messages = raw.get("messages")
    if raw_messages is None:
        raw_messages = []
    if not isinstance(raw_messages, list):
        raise ValueError("messages must be a list")

    messages: list[LegacyMessage] = []
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict):
            raise ValueError("each message must be an object")
        role = str(item.get("role") or "").strip()
        if role not in ("user", "assistant"):
            raise ValueError(f"message {index} has an unknown role")
        content = item.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"message {index} has no content")
        created = item.get("createdAt") or ""
        if not isinstance(created, str):
            raise ValueError(f"message {index} has a bad createdAt")
        message_id = item.get("id") or ""
        if not isinstance(message_id, str) or not message_id.strip():
            message_id = f"m{index}"
        cards = item.get("cards") or []
        if not isinstance(cards, list):
            raise ValueError(f"message {index} has bad cards")
        messages.append(
            LegacyMessage(
                id=message_id.strip(),
                role=role,
                content=content,
                created_at=created.strip(),
                cards=tuple(
                    dict(card) for card in cards if isinstance(card, Mapping)
                ),
            )
        )

    updated_at = raw.get("updatedAt") or ""
    if not isinstance(updated_at, str):
        raise ValueError("updatedAt must be a string")
    updated_at = updated_at.strip() or (
        messages[-1].created_at if messages else ""
    )
    return LegacyConversation(ident, title, updated_at, tuple(messages))


def _write_session(
    layout: ProjectLayout, conversation: LegacyConversation, clock: Clock
) -> None:
    created = (
        conversation.messages[0].created_at
        if conversation.messages and conversation.messages[0].created_at
        else conversation.updated_at or iso_timestamp(clock)
    )
    header: dict[str, object] = {
        "v": 1,
        "type": "session",
        "id": conversation.id,
        "createdAt": created,
    }
    if conversation.title:
        header["title"] = conversation.title
    lines = [session_line(header)]
    parent = conversation.id
    used: set[str] = set()
    for index, message in enumerate(conversation.messages):
        ident = message.id
        if not ident or ident in used:
            ident = f"m{index}"
        while ident in used:
            ident = f"m{index}-{len(used)}"
        entry: dict[str, object] = {
            "v": 1,
            "type": "message",
            "id": ident,
            "parentId": parent,
            "ts": message.created_at or created,
            "role": message.role,
            "content": message.content,
        }
        if message.cards:
            entry["meta"] = {"cards": [dict(card) for card in message.cards]}
        lines.append(session_line(entry))
        used.add(ident)
        parent = ident

    layout.sessions_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(layout.session_path(conversation.id), "".join(lines))


def _read_marker(marker: Path) -> set[str]:
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, dict) or raw.get("v") != MIGRATION_VERSION:
        # An older marker recorded only that a pass finished; what it covered is
        # no longer known, so the files are checked again (existing targets are
        # adopted without rewriting them).
        return set()
    handled = raw.get("handled")
    if not isinstance(handled, list):
        return set()
    return {
        item for item in handled if isinstance(item, str) and item
    }


def _write_marker(marker: Path, handled: set[str], clock: Clock) -> None:
    try:
        _write_atomic(
            marker,
            json.dumps(
                {
                    "v": MIGRATION_VERSION,
                    "at": iso_timestamp(clock),
                    "handled": sorted(handled),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
        )
    except OSError as err:
        logger.warning("the chat migration marker cannot be written: %s", err)


def _write_atomic(path: Path, text: str) -> None:
    """Write a file whole, and make sure the bytes reached the disk first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
