"""Pages a run could not read: sign-in walls waiting for the browser.

A page behind a login is not a failed fetch to retry or a material to drop: the
person can open it in the built-in browser, sign in, and capture it by hand. This
is the small queue that says which pages those are, beside the other things the
attention panel already holds.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from noteagent.io.atomic import write_text_atomic

VERSION = 1
STATUS_OPEN = "open"
STATUS_DISMISSED = "dismissed"

#: How many open pages the attention panel is handed.
PAYLOAD_LIMIT = 20

@dataclass(frozen=True, slots=True)
class LoginWall:
    """One page a run met behind a sign-in."""

    url: str
    title: str = ""
    reason: str = ""
    http_status: int | None = None
    run_id: str = ""


_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


class CaptureInbox:
    """The sign-in walls one project is waiting on, and their state."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def add(
        self,
        *,
        url: str,
        title: str = "",
        reason: str = "",
        http_status: int | None = None,
        run_id: str = "",
        at: str = "",
    ) -> dict[str, object]:
        cleaned = (url or "").strip()
        if not cleaned:
            raise ValueError("a capture inbox item needs a url")
        with _lock_for(self._path):
            items = self._read()
            item_id = _item_id(cleaned)
            found = next((item for item in items if item.get("id") == item_id), None)
            if found is None:
                found = {
                    "id": item_id,
                    "url": cleaned,
                    "title": title,
                    "reason": reason,
                    "httpStatus": http_status,
                    "runId": run_id,
                    "at": at,
                    "status": STATUS_OPEN,
                }
                items.append(found)
            else:
                # The same page met again is the same thing to do, not a second
                # row; a dismissed page that turns up again is open again.
                found.update(
                    {
                        "url": cleaned,
                        "title": title or found.get("title", ""),
                        "reason": reason or found.get("reason", ""),
                        "runId": run_id or found.get("runId", ""),
                        "at": at or found.get("at", ""),
                        "status": STATUS_OPEN,
                    }
                )
                if http_status is not None:
                    found["httpStatus"] = http_status
            self._write(items)
            return dict(found)

    def payload(self, *, limit: int = PAYLOAD_LIMIT) -> dict[str, object]:
        opened = [
            item for item in self._read() if item.get("status") == STATUS_OPEN
        ]
        return {"openCount": len(opened), "items": opened[: max(0, limit)]}

    def dismiss(self, item_id: str) -> bool:
        key = (item_id or "").strip()
        if not key:
            return False
        with _lock_for(self._path):
            items = self._read()
            changed = False
            for item in items:
                if item.get("id") == key and item.get("status") == STATUS_OPEN:
                    item["status"] = STATUS_DISMISSED
                    changed = True
            if changed:
                self._write(items)
            return changed

    def _read(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, Mapping):
            return []
        items = raw.get("items")
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, Mapping)]

    def _write(self, items: list[dict[str, object]]) -> None:
        write_text_atomic(
            self._path,
            json.dumps(
                {"version": VERSION, "items": items},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )


def _item_id(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"capture-{digest}"


__all__ = [
    "CaptureInbox",
    "LoginWall",
    "PAYLOAD_LIMIT",
    "STATUS_DISMISSED",
    "STATUS_OPEN",
]
