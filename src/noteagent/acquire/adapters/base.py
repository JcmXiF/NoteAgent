"""Source kinds. Mixed payloads inside one file are block roles, not kinds."""

from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """Original-file kinds this build can classify."""

    HTML = "html"
    PDF = "pdf"
    OFFICE = "office"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
