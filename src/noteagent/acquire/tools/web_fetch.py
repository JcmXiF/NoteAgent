"""Generic web_fetch: open a URL and return HTML, PDF, Office, or a standalone image."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

import httpx

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.adapters.image_source import (
    IMAGE_ACCEPT,
    IMAGE_CAPTURE_METHOD,
    MAX_IMAGE_ORIGINAL_BYTES,
    ImageDeriveError,
    ImageSourceAdapter,
    looks_like_image,
)
from noteagent.acquire.adapters.office_source import (
    MAX_OFFICE_BYTES,
    OFFICE_ACCEPT,
    OFFICE_CAPTURE_METHOD,
    OfficeDeriveError,
    OfficeSourceAdapter,
    looks_like_office,
)
from noteagent.acquire.adapters.pdf_source import (
    EMPTY_PAGE_TEXT,
    PDF_CAPTURE_METHOD,
    PdfDeriveError,
    PdfSourceAdapter,
    looks_like_pdf,
)
from noteagent.acquire.adapters.sniff import sniff_media_type, sniff_url
from noteagent.acquire.derive import format_derived_text
from noteagent.acquire.quality import (
    assess_capture,
    assess_image_capture,
    assess_office_capture,
    assess_pdf_capture,
)
from noteagent.acquire.render import wrap_article_document
from noteagent.acquire.tools.fetchers.article import (
    build_article_body,
    extract_article_html,
    visible_text_from_html,
)
from noteagent.acquire.tools.fetchers.html import (
    HTML_ACCEPT,
    MAX_HTML_BYTES,
    MAX_PDF_BYTES,
    PDF_ACCEPT,
    FetchRequestError,
    decode_body,
    fetch_url_document,
    html_to_markdown,
    media_skip_reason,
)
from noteagent.acquire.types import Capture

TOOL_NAME = "web_fetch"
TOOL_DESCRIPTION = (
    "Fetch a public webpage, PDF, Office file, or standalone image and return "
    "reader-mode HTML, page text, or derived leaves plus a quality gate. Does "
    "not render JavaScript, bypass logins, or write raw/."
)
CAPTURE_METHOD = "trafilatura-html"
RENDERED_CAPTURE_METHOD = "rendered-html"

# Access / rate-limit statuses: not the requested resource (RFC 9110).
# Keep in sync with desktop/guest-auth.cjs httpAccessReason.
_HTTP_ACCESS = {
    401: "http_unauthorized",
    403: "http_forbidden",
    429: "http_rate_limited",
}


def _failed(
    url: str,
    *,
    method: str,
    reason: str,
    status: int | None = None,
    title: str = "",
    markdown: str = "",
    html: str = "",
    final_url: str = "",
    kind: str = "html",
) -> Capture:
    return Capture(
        url=url,
        title=title,
        markdown=markdown,
        html=html,
        capture_method=method,
        fidelity="failed",
        reasons=(reason,),
        http_status=status,
        final_url=final_url or url,
        char_count=len(markdown),
        kind=kind,
    )


def _http_failure_reason(status: int) -> str | None:
    """
    Non-2xx responses are not the requested resource. 401/403/429 are labeled
    separately for the UI; other failures share http_error.
    """
    if httpx.codes.is_success(status):
        return None
    return _HTTP_ACCESS.get(status, "http_error")


def _pdf_title(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if leaf.lower().endswith(".pdf"):
        leaf = leaf[:-4]
    return leaf or "PDF"


def _office_title(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    lowered = leaf.lower()
    if lowered.endswith(".docx"):
        leaf = leaf[:-5]
    elif lowered.endswith(".pptx"):
        leaf = leaf[:-5]
    return leaf or "Office"


def capture_from_office(
    url: str,
    office_bytes: bytes,
    *,
    truncated: bool = False,
    http_status: int | None = None,
    final_url: str = "",
    capture_method: str = OFFICE_CAPTURE_METHOD,
    title: str = "",
) -> Capture:
    """OOXML original + stamped HTML leaves. Does not write raw/."""
    cleaned = url.strip()
    source = (final_url or cleaned).strip() or cleaned
    heading = (title or "").strip() or _office_title(source)
    if truncated:
        fidelity, reasons = assess_office_capture(
            block_count=0,
            text_chars=0,
            truncated=True,
        )
        return Capture(
            url=cleaned,
            title=heading,
            markdown="",
            html="",
            capture_method=capture_method,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="office",
        )
    try:
        derived = OfficeSourceAdapter().derive_bytes(
            office_bytes,
            filename=heading,
            title=heading,
            source_url=source,
            capture_method=capture_method,
        )
    except OfficeDeriveError:
        fidelity, reasons = assess_office_capture(
            block_count=0,
            text_chars=0,
            invalid=True,
        )
        return Capture(
            url=cleaned,
            title=heading,
            markdown="",
            html="",
            capture_method=capture_method,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="office",
        )
    text = format_derived_text(derived.blocks)
    text_chars = len(" ".join(text.split()))
    fidelity, reasons = assess_office_capture(
        block_count=len(derived.blocks),
        text_chars=text_chars,
    )
    return Capture(
        url=cleaned,
        title=heading,
        markdown=text,
        html=derived.html,
        capture_method=capture_method,
        fidelity=fidelity,
        reasons=reasons,
        http_status=http_status,
        final_url=source,
        char_count=text_chars,
        kind="office",
        office_bytes=office_bytes,
        page_count=len(derived.blocks),
    )


def _image_title(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    if "." in leaf:
        leaf = leaf.rsplit(".", 1)[0]
    return leaf or "image"


def capture_from_image(
    url: str,
    image_bytes: bytes,
    *,
    truncated: bool = False,
    http_status: int | None = None,
    final_url: str = "",
    capture_method: str = IMAGE_CAPTURE_METHOD,
    title: str = "",
) -> Capture:
    """Standalone image original + one whole-image leaf. Does not write raw/."""
    cleaned = url.strip()
    source = (final_url or cleaned).strip() or cleaned
    heading = (title or "").strip() or _image_title(source)
    if truncated:
        fidelity, reasons = assess_image_capture(truncated=True)
        return Capture(
            url=cleaned,
            title=heading,
            markdown="",
            html="",
            capture_method=capture_method,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="image",
        )
    try:
        derived = ImageSourceAdapter().derive_bytes(
            image_bytes,
            filename=heading,
            title=heading,
            source_url=source,
            capture_method=capture_method,
        )
    except ImageDeriveError:
        fidelity, reasons = assess_image_capture(invalid=True)
        return Capture(
            url=cleaned,
            title=heading,
            markdown="",
            html="",
            capture_method=capture_method,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="image",
        )
    text = format_derived_text(derived.blocks)
    fidelity, reasons = assess_image_capture()
    return Capture(
        url=cleaned,
        title=heading,
        markdown=text,
        html=derived.html,
        capture_method=capture_method,
        fidelity=fidelity,
        reasons=reasons,
        http_status=http_status,
        final_url=source,
        char_count=len(text),
        kind="image",
        image_bytes=image_bytes,
        page_count=1,
    )


def capture_from_pdf(
    url: str,
    pdf_bytes: bytes,
    *,
    truncated: bool = False,
    http_status: int | None = None,
    final_url: str = "",
) -> Capture:
    """Page-level text extract + quality gate. Does not write raw/."""
    cleaned = url.strip()
    source = (final_url or cleaned).strip() or cleaned
    title = _pdf_title(source)
    if truncated:
        fidelity, reasons = assess_pdf_capture(
            page_count=0,
            text_chars=0,
            truncated=True,
        )
        return Capture(
            url=cleaned,
            title=title,
            markdown="",
            html="",
            capture_method=PDF_CAPTURE_METHOD,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="pdf",
        )
    try:
        blocks = PdfSourceAdapter().derive_bytes(pdf_bytes)
    except PdfDeriveError:
        fidelity, reasons = assess_pdf_capture(
            page_count=0,
            text_chars=0,
            invalid=True,
        )
        return Capture(
            url=cleaned,
            title=title,
            markdown="",
            html="",
            capture_method=PDF_CAPTURE_METHOD,
            fidelity=fidelity,
            reasons=reasons,
            http_status=http_status,
            final_url=source,
            char_count=0,
            kind="pdf",
        )

    text_chars = 0
    for block in blocks:
        body = block.text.strip()
        if body and body != EMPTY_PAGE_TEXT:
            text_chars += len(body)
    fidelity, reasons = assess_pdf_capture(
        page_count=len(blocks),
        text_chars=text_chars,
    )
    return Capture(
        url=cleaned,
        title=title,
        markdown=format_derived_text(blocks),
        html="",
        capture_method=PDF_CAPTURE_METHOD,
        fidelity=fidelity,
        reasons=reasons,
        http_status=http_status,
        final_url=source,
        char_count=text_chars,
        kind="pdf",
        pdf_bytes=pdf_bytes,
        page_count=len(blocks),
    )


def _treat_as_pdf(url: str, content_type: str, body: bytes) -> bool:
    url_kind = sniff_url(url).kind
    media = sniff_media_type(content_type)
    if media.kind is SourceKind.PDF:
        return True
    if looks_like_pdf(body) and (url_kind is SourceKind.PDF or media.kind is None):
        return True
    return False


def _treat_as_office(url: str, content_type: str, body: bytes) -> bool:
    url_kind = sniff_url(url).kind
    media = sniff_media_type(content_type)
    if media.kind is SourceKind.OFFICE:
        return True
    if looks_like_office(body) and (
        url_kind is SourceKind.OFFICE or media.kind is None
    ):
        return True
    return False


def _treat_as_image(url: str, content_type: str, body: bytes) -> bool:
    if _looks_like_html_body(body):
        return False
    url_kind = sniff_url(url).kind
    media = sniff_media_type(content_type)
    if media.kind is SourceKind.IMAGE:
        return True
    if looks_like_image(body) and (
        url_kind is SourceKind.IMAGE or media.kind is None
    ):
        return True
    return False


def _looks_like_html_body(body: bytes) -> bool:
    head = body.lstrip()[:256].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def capture_from_html(
    url: str,
    html: str,
    *,
    capture_method: str,
    truncated: bool = False,
    http_status: int | None = None,
    final_url: str = "",
    client: httpx.Client | None = None,
) -> Capture:
    """
    Reader-mode extract + localize + block ids + quality gate.

    *html* is already the page document (public GET body or a rendered DOM).
    Does not write raw/. Image downloads stay anonymous httpx; no cookies.
    """
    cleaned = url.strip()
    source = (final_url or cleaned).strip() or cleaned
    title, article = extract_article_html(html, url=source)
    if not article.strip():
        return _failed(
            cleaned,
            method=capture_method,
            reason="empty",
            status=http_status,
            title=title,
            final_url=source,
        )

    _, markdown = html_to_markdown(article, url=source)
    inner, image_ok, image_failed = build_article_body(
        article,
        page_url=source,
        client=client,
    )
    text = visible_text_from_html(inner)
    fidelity, reasons = assess_capture(
        text,
        truncated=truncated,
        images_failed=image_failed,
    )
    document = wrap_article_document(
        title=title,
        source_url=source,
        capture_method=capture_method,
        fidelity=fidelity,
        body_html=inner,
    )
    return Capture(
        url=cleaned,
        title=title,
        markdown=markdown,
        html=document,
        capture_method=capture_method,
        fidelity=fidelity,
        reasons=reasons,
        http_status=http_status,
        final_url=source,
        char_count=len(text),
        image_count=image_ok,
        images_failed=image_failed,
        kind="html",
    )


def capture_rendered_html(
    url: str,
    html: str,
    *,
    client: httpx.Client | None = None,
) -> Capture:
    """
    Turn a caller-supplied rendered page into the same Capture as web_fetch.

    The caller already has the visible DOM (a browser view or a test fixture).
    This does not GET the url, does not send cookies, and does not write
    raw/. Oversized HTML raises ValueError; the API maps that to HTTP
    400. PDF, Office, and image URLs are rejected: from-rendered is HTML-only.
    """
    cleaned = url.strip()
    sniffed = sniff_url(cleaned)
    if sniffed.kind is SourceKind.PDF:
        return _failed(
            cleaned,
            method="pdf_not_html",
            reason="pdf_not_html",
            kind="pdf",
        )
    if sniffed.kind is SourceKind.OFFICE:
        return _failed(
            cleaned,
            method="office_not_html",
            reason="office_not_html",
            kind="office",
        )
    if sniffed.kind is SourceKind.IMAGE:
        return _failed(
            cleaned,
            method="image_not_html",
            reason="image_not_html",
            kind="image",
        )
    skip = sniffed.fetch_skip
    if skip:
        method = skip if skip.startswith("skipped_") else "unsupported_url"
        return _failed(cleaned, method=method, reason=skip)

    body = html or ""
    if len(body.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError(f"html exceeds {MAX_HTML_BYTES} bytes")

    return capture_from_html(
        cleaned,
        body,
        capture_method=RENDERED_CAPTURE_METHOD,
        client=client,
    )


def web_fetch(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    client: httpx.Client | None = None,
) -> Capture:
    """
    Generic webpage, PDF, Office, or standalone-image fetch. Returns a Capture
    even when the gate fails.
    """
    cleaned = url.strip()
    sniffed = sniff_url(cleaned)
    skip = sniffed.fetch_skip
    if skip:
        method = skip if skip.startswith("skipped_") else "unsupported_url"
        return _failed(cleaned, method=method, reason=skip)

    want_pdf = sniffed.kind is SourceKind.PDF
    want_office = sniffed.kind is SourceKind.OFFICE
    want_image = sniffed.kind is SourceKind.IMAGE
    if want_pdf:
        max_bytes, accept = MAX_PDF_BYTES, PDF_ACCEPT
    elif want_office:
        max_bytes, accept = MAX_OFFICE_BYTES, OFFICE_ACCEPT
    elif want_image:
        max_bytes, accept = MAX_IMAGE_ORIGINAL_BYTES, IMAGE_ACCEPT
    else:
        max_bytes, accept = MAX_HTML_BYTES, HTML_ACCEPT
    try:
        status, final_url, raw_type, body, truncated, encoding = fetch_url_document(
            cleaned,
            timeout_seconds=timeout_seconds,
            client=client,
            max_bytes=max_bytes,
            accept=accept,
        )
    except FetchRequestError as err:
        return _failed(
            cleaned,
            method="http_error",
            reason="http_error",
            title=str(err)[:120],
        )

    http_reason = _http_failure_reason(status)
    if http_reason:
        return _failed(
            cleaned,
            method=http_reason,
            reason=http_reason,
            status=status,
            final_url=final_url,
        )

    if _treat_as_pdf(cleaned, raw_type, body):
        return capture_from_pdf(
            cleaned,
            body,
            truncated=truncated,
            http_status=status,
            final_url=final_url,
        )

    if _treat_as_office(cleaned, raw_type, body):
        return capture_from_office(
            cleaned,
            body,
            truncated=truncated,
            http_status=status,
            final_url=final_url,
        )

    if _treat_as_image(cleaned, raw_type, body):
        return capture_from_image(
            cleaned,
            body,
            truncated=truncated,
            http_status=status,
            final_url=final_url,
        )

    media_skip = media_skip_reason(raw_type)
    if media_skip:
        return _failed(
            cleaned,
            method=media_skip,
            reason=media_skip,
            status=status,
            final_url=final_url,
        )

    page = decode_body(body, raw_type, encoding)
    return capture_from_html(
        cleaned,
        page,
        capture_method=CAPTURE_METHOD,
        truncated=truncated,
        http_status=status,
        final_url=final_url,
        client=client,
    )
