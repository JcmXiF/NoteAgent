"""Classify a URL, Content-Type, or local path into a SourceKind.

PDF, Office, and standalone images have derive-ready adapters. Audio and video
are classified so the fetch layer can refuse them by name.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from noteagent.acquire.adapters.base import SourceKind

_HTML_MEDIA = frozenset({"text/html", "application/xhtml+xml"})
_PDF_MEDIA = frozenset({"application/pdf", "application/x-pdf"})
_OFFICE_MEDIA = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": SourceKind.OFFICE,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": SourceKind.OFFICE,
}
_OFFICE_SUFFIXES = frozenset({".docx", ".pptx"})
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"})
_AUDIO_SUFFIXES = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".flac"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mkv"})
_HTML_SUFFIXES = frozenset({".html", ".htm", ".xhtml"})


@dataclass(frozen=True, slots=True)
class SniffResult:
    """Classified kind, plus a skip code when the payload must be refused."""

    kind: SourceKind | None
    fetch_skip: str | None


def sniff_url(url: str) -> SniffResult:
    """HTTP(S) URL only. Local files use sniff_path."""
    cleaned = url.strip()
    if not cleaned:
        return _skip("unsupported_url")
    try:
        parsed = urlparse(cleaned)
    except ValueError:
        return _skip("unsupported_url")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _skip("unsupported_url")
    return _result(_kind_from_suffix(parsed.path) or SourceKind.HTML)


def sniff_media_type(content_type: str) -> SniffResult:
    """Classify a Content-Type. An empty type is not a skip; the caller may sniff the body."""
    media = _media_type(content_type)
    if not media:
        return _result(None)
    if _is_pdf_media(media):
        return _result(SourceKind.PDF)
    office_kind = _OFFICE_MEDIA.get(media)
    if office_kind is not None:
        return _result(office_kind)
    if media.startswith("video/"):
        return _result(SourceKind.VIDEO, "skipped_video")
    if media.startswith("image/"):
        return _result(SourceKind.IMAGE)
    if media.startswith("audio/"):
        return _result(SourceKind.AUDIO, "unsupported_media")
    if media in _HTML_MEDIA:
        return _result(SourceKind.HTML)
    return _result(None, "unsupported_media")


def sniff_path(path: Path | str) -> SniffResult:
    """Local filename suffix. Markdown stays on the existing CLI path."""
    suffix = Path(path).suffix.lower()
    if suffix in _HTML_SUFFIXES:
        return _result(SourceKind.HTML)
    if suffix in _OFFICE_SUFFIXES:
        return _result(SourceKind.OFFICE)
    if suffix in _IMAGE_SUFFIXES:
        return _result(SourceKind.IMAGE)
    if suffix in _AUDIO_SUFFIXES:
        return _result(SourceKind.AUDIO)
    if suffix in _VIDEO_SUFFIXES:
        return _result(SourceKind.VIDEO)
    if suffix == ".pdf":
        return _result(SourceKind.PDF)
    return _result(None)


def _kind_from_suffix(path: str) -> SourceKind | None:
    lower = path.lower()
    for suffix, kind in (
        (".pdf", SourceKind.PDF),
        *[(ext, SourceKind.OFFICE) for ext in _OFFICE_SUFFIXES],
        *[(ext, SourceKind.IMAGE) for ext in _IMAGE_SUFFIXES],
        *[(ext, SourceKind.AUDIO) for ext in _AUDIO_SUFFIXES],
        *[(ext, SourceKind.VIDEO) for ext in _VIDEO_SUFFIXES],
        *[(ext, SourceKind.HTML) for ext in _HTML_SUFFIXES],
    ):
        if lower.endswith(suffix):
            return kind
    return None


def _result(kind: SourceKind | None, skip: str | None = None) -> SniffResult:
    return SniffResult(kind=kind, fetch_skip=skip)


def _skip(reason: str) -> SniffResult:
    return SniffResult(kind=None, fetch_skip=reason)


def _media_type(content_type: str) -> str:
    return content_type.split(";")[0].strip().lower()


def _is_pdf_media(media: str) -> bool:
    if media in _PDF_MEDIA:
        return True
    return "/" in media and media.rsplit("/", 1)[-1] == "pdf"
