"""Local file intake: human-chosen bytes → Capture. Does not write raw/."""

from __future__ import annotations

import hashlib
import html as html_lib
from dataclasses import replace
from pathlib import Path

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.image_source import (
    MAX_IMAGE_ORIGINAL_BYTES,
    looks_like_image,
)
from noteagent.acquire.adapters.office_source import (
    MAX_OFFICE_BYTES,
    looks_like_office,
)
from noteagent.acquire.adapters.pdf_source import looks_like_pdf
from noteagent.acquire.adapters.sniff import sniff_media_type, sniff_path
from noteagent.acquire.quality import assess_local_html
from noteagent.acquire.render import wrap_article_document
from noteagent.acquire.tools.fetchers.article import (
    build_article_body,
    visible_text_from_html,
)
from noteagent.acquire.tools.fetchers.html import (
    MAX_HTML_BYTES,
    MAX_PDF_BYTES,
    decode_body,
    extract_page_title,
)
from noteagent.acquire.tools.web_fetch import (
    capture_from_image,
    capture_from_office,
    capture_from_pdf,
)
from noteagent.acquire.types import Capture

MAX_LOCAL_FILES = 4
LOCAL_HTML_CAPTURE_METHOD = "local-html"
LOCAL_PDF_CAPTURE_METHOD = "local-pdf"
LOCAL_OFFICE_CAPTURE_METHOD = "local-office"
LOCAL_IMAGE_CAPTURE_METHOD = "local-image"
LOCAL_FILE_CAPTURE_METHOD = "local-file"

_NOT_READY_KINDS = frozenset(
    {
        SourceKind.AUDIO,
        SourceKind.VIDEO,
    }
)


def local_capture_url(kind: str, data: bytes) -> str:
    """Stable identity for a local file. Hash is of the original bytes."""
    digest = hashlib.sha256(data).hexdigest()[:12]
    return f"local:{kind}:{digest}"


def capture_from_local_file(
    *,
    filename: str,
    data: bytes,
    media_type: str = "",
) -> Capture:
    """
    Classify a dropped/picked file and produce a Capture.

    HTML is sanitized and stamped, not Trafilatura-rewritten, and images are
    not fetched. PDF reuses page-level derive. Office derives stamped HTML
    leaves from the OOXML original. Standalone images derive one whole-image
    leaf. Audio and video are refused with ``adapter_not_ready``.
    """
    name = Path(filename).name or "file"
    sniffed = sniff_path(name)
    if sniffed.kind is None and (media_type or "").strip():
        sniffed = sniff_media_type(media_type)

    if looks_like_pdf(data) or sniffed.kind is SourceKind.PDF:
        return _local_pdf(name, data)
    if sniffed.kind is SourceKind.HTML or _looks_like_html(data):
        return _local_html(name, data)
    if sniffed.kind is SourceKind.OFFICE or looks_like_office(data):
        return _local_office(name, data)
    if sniffed.kind is SourceKind.IMAGE or looks_like_image(data):
        return _local_image(name, data)
    if sniffed.kind in _NOT_READY_KINDS:
        return _failed_local(
            name,
            data,
            kind=sniffed.kind.value,
            reason="adapter_not_ready",
        )
    return _failed_local(name, data, kind="", reason="unsupported_kind")


def _local_pdf(filename: str, data: bytes) -> Capture:
    url = local_capture_url("pdf", data)
    truncated = len(data) > MAX_PDF_BYTES
    capture = capture_from_pdf(
        url,
        data if not truncated else b"",
        truncated=truncated,
    )
    title = filename or capture.title
    return replace(
        capture,
        capture_method=LOCAL_PDF_CAPTURE_METHOD,
        title=title,
        url=url,
        final_url=url,
    )


def _local_office(filename: str, data: bytes) -> Capture:
    url = local_capture_url("office", data)
    title = Path(filename).name or filename or "document"
    truncated = len(data) > MAX_OFFICE_BYTES
    capture = capture_from_office(
        url,
        data if not truncated else b"",
        truncated=truncated,
        capture_method=LOCAL_OFFICE_CAPTURE_METHOD,
        title=Path(filename).stem or title,
    )
    return replace(
        capture,
        title=title,
        url=url,
        final_url=url,
    )


def _local_image(filename: str, data: bytes) -> Capture:
    url = local_capture_url("image", data)
    title = Path(filename).name or filename or "image"
    truncated = len(data) > MAX_IMAGE_ORIGINAL_BYTES
    capture = capture_from_image(
        url,
        data if not truncated else b"",
        truncated=truncated,
        capture_method=LOCAL_IMAGE_CAPTURE_METHOD,
        title=Path(filename).stem or title,
    )
    return replace(
        capture,
        title=title,
        url=url,
        final_url=url,
    )


def _local_html(filename: str, data: bytes) -> Capture:
    url = local_capture_url("html", data)
    title_fallback = Path(filename).stem or filename or "page"
    if len(data) > MAX_HTML_BYTES:
        return _failed_local(
            filename,
            data,
            kind="html",
            reason="truncated",
            method=LOCAL_HTML_CAPTURE_METHOD,
        )
    document_html = decode_body(data, "text/html")
    title = extract_page_title(document_html, url=url) or title_fallback
    inner, image_ok, image_failed = build_article_body(
        document_html,
        page_url=url,
        localize=False,
    )
    inner = _ensure_block_ids(inner, page_url=url)
    text = visible_text_from_html(inner)
    fidelity, reasons = assess_local_html(text)
    wrapped = wrap_article_document(
        title=title,
        source_url=url,
        capture_method=LOCAL_HTML_CAPTURE_METHOD,
        fidelity=fidelity,
        body_html=inner,
    )
    return Capture(
        url=url,
        title=title,
        markdown=text,
        html=wrapped,
        capture_method=LOCAL_HTML_CAPTURE_METHOD,
        fidelity=fidelity,
        reasons=reasons,
        final_url=url,
        char_count=len(text),
        image_count=image_ok,
        images_failed=image_failed,
        kind="html",
    )


def _ensure_block_ids(inner: str, *, page_url: str) -> str:
    if "data-na-block" in inner:
        return inner
    text = visible_text_from_html(inner).strip()
    if not text:
        return inner
    fallback = f"<p>{html_lib.escape(text)}</p>"
    stamped, _, _ = build_article_body(fallback, page_url=page_url, localize=False)
    return stamped


def _looks_like_html(data: bytes) -> bool:
    head = data.lstrip()[:256].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _failed_local(
    filename: str,
    data: bytes,
    *,
    kind: str,
    reason: str,
    method: str = LOCAL_FILE_CAPTURE_METHOD,
) -> Capture:
    token = kind or "file"
    url = local_capture_url(token, data) if data else f"local:{token}:empty"
    return Capture(
        url=url,
        title=filename,
        markdown="",
        html="",
        capture_method=method,
        fidelity="failed",
        reasons=(reason,),
        final_url=url,
        kind=kind or "html",
    )
