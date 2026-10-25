"""Named intake channels. Not source kinds."""

from __future__ import annotations

from enum import StrEnum


class IntakeChannel(StrEnum):
    """How a capture reached the import page."""

    SEARCH = "search"
    URL = "url"
    GUEST = "guest"
    LOCAL = "local"


def infer_intake(*, url: str, capture_method: str) -> str:
    """Infer channel when Capture has no dedicated field."""
    cleaned = url.strip()
    if cleaned.startswith("local:"):
        return IntakeChannel.LOCAL.value
    if capture_method == "rendered-html":
        return IntakeChannel.GUEST.value
    return IntakeChannel.URL.value
