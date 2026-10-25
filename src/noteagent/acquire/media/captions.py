"""Project-wide caption cache keyed by image-bytes SHA-256."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from noteagent.project.layout import ProjectLayout

PLACEHOLDER_CAPTION = "有图无说明"
_NON_PLACEHOLDER_SOURCES = frozenset({"alt", "figcaption", "filename", "vlm"})


@dataclass(frozen=True, slots=True)
class CaptionRecord:
    caption: str
    source: str
    media_type: str = ""
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "caption": self.caption,
            "source": self.source,
        }
        if self.media_type:
            payload["mediaType"] = self.media_type
        if self.width:
            payload["width"] = self.width
        if self.height:
            payload["height"] = self.height
        return payload

    @classmethod
    def from_dict(cls, data: object) -> CaptionRecord | None:
        if not isinstance(data, dict):
            return None
        caption = str(data.get("caption") or "").strip()
        if not caption:
            return None
        return cls(
            caption=caption,
            source=str(data.get("source") or "placeholder"),
            media_type=str(data.get("mediaType") or ""),
            width=_as_int(data.get("width")),
            height=_as_int(data.get("height")),
        )


class CaptionStore:
    """``layout.media_captions`` JSON map. In-memory when *path* is None."""

    def __init__(self, path: Path | None = None, items: dict[str, CaptionRecord] | None = None):
        self.path = path
        self._items = dict(items or {})
        self._dirty = False

    @classmethod
    def for_layout(cls, layout: ProjectLayout) -> CaptionStore:
        path = layout.media_captions
        items: dict[str, CaptionRecord] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = None
            items = _parse_items(raw)
        return cls(path=path, items=items)

    @classmethod
    def memory(cls) -> CaptionStore:
        return cls(path=None, items={})

    def get(self, digest: str) -> CaptionRecord | None:
        return self._items.get(digest)

    def lookup(self, digest: str) -> str | None:
        record = self.get(digest)
        if record is None:
            return None
        caption = record.caption.strip()
        return caption or None

    def remember(
        self,
        digest: str,
        *,
        caption: str,
        source: str,
        media_type: str = "",
        width: int = 0,
        height: int = 0,
    ) -> CaptionRecord:
        cleaned = caption.strip() or PLACEHOLDER_CAPTION
        incoming = CaptionRecord(
            caption=cleaned,
            source=source if cleaned != PLACEHOLDER_CAPTION else "placeholder",
            media_type=media_type,
            width=width,
            height=height,
        )
        existing = self.get(digest)
        if existing is not None and _keeps_existing(existing):
            merged = CaptionRecord(
                caption=existing.caption,
                source=existing.source,
                media_type=existing.media_type or incoming.media_type,
                width=existing.width or incoming.width,
                height=existing.height or incoming.height,
            )
            if merged != existing:
                self._items[digest] = merged
                self._dirty = True
            return merged
        if existing != incoming:
            self._items[digest] = incoming
            self._dirty = True
        return incoming

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        if not self._items and not self.path.is_file():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {digest: record.to_dict() for digest, record in sorted(self._items.items())}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._dirty = False


def _keeps_existing(existing: CaptionRecord) -> bool:
    """A real caption from the page always outranks a filename or placeholder."""
    return (
        existing.caption != PLACEHOLDER_CAPTION
        and existing.source in _NON_PLACEHOLDER_SOURCES
    )


def _parse_items(raw: object) -> dict[str, CaptionRecord]:
    if not isinstance(raw, dict):
        return {}
    items: dict[str, CaptionRecord] = {}
    for key, value in raw.items():
        digest = str(key).strip().lower()
        if len(digest) != 64:
            continue
        record = CaptionRecord.from_dict(value)
        if record is not None:
            items[digest] = record
    return items


def _as_int(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
