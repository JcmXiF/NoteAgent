"""HTTP GET for HTML extract and PDF / Office / standalone-image originals. No JS rendering."""

from __future__ import annotations

from email.message import Message

import httpx
import trafilatura
from trafilatura.settings import use_config

from noteagent.acquire.adapters.sniff import sniff_media_type

FETCH_USER_AGENT = (
    "NoteAgent/0.1 (local personal wiki; +http://127.0.0.1; html extract)"
)
MAX_HTML_BYTES = 2_000_000
MAX_PDF_BYTES = 20_000_000
DEFAULT_TIMEOUT = 30.0
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
PDF_ACCEPT = "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"

EXTRACT_CONFIG = use_config()
EXTRACT_CONFIG.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")


def media_skip_reason(content_type: str) -> str | None:
    """Classify a Content-Type into a skip code, or None if extraction may run."""
    return sniff_media_type(content_type).fetch_skip


def decode_body(body: bytes, content_type: str, encoding: str | None = None) -> str:
    header_charset = _charset_from_content_type(content_type)
    for candidate in (encoding, header_charset, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return body.decode("utf-8", errors="replace")
    best = from_bytes(body).best()
    if best is None:
        return body.decode("utf-8", errors="replace")
    return str(best)


def html_to_markdown(html: str, *, url: str = "") -> tuple[str, str]:
    """
    Extract the main article as Markdown. Empty markdown means extraction failed.
    """
    title = extract_page_title(html, url=url)
    markdown = trafilatura.extract(
        html,
        url=url or None,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        include_comments=False,
        include_images=True,
        favor_precision=True,
        config=EXTRACT_CONFIG,
    )
    return title, (markdown or "").strip()


def fetch_url_document(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
    max_bytes: int = MAX_HTML_BYTES,
    accept: str = HTML_ACCEPT,
) -> tuple[int, str, str, bytes, bool, str | None]:
    """
    GET *url*. Returns (status, final_url, raw Content-Type, body, truncated, encoding).

    Truncated bodies are sliced to *max_bytes*; callers must fail closed for PDF.
    """
    headers = {
        "User-Agent": FETCH_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    own_client = client is None
    http = client or httpx.Client(
        timeout=timeout_seconds,
        follow_redirects=True,
        headers=headers,
    )
    try:
        response = http.get(url, headers=headers)
    except httpx.HTTPError as err:
        raise FetchRequestError(str(err)[:300]) from err
    finally:
        if own_client:
            http.close()
    raw_type = response.headers.get("content-type") or ""
    body = response.content
    truncated = len(body) > max_bytes
    if truncated:
        body = body[:max_bytes]
    encoding = response.encoding
    return response.status_code, str(response.url), raw_type, body, truncated, encoding


def _charset_from_content_type(content_type: str) -> str | None:
    if not content_type.strip():
        return None
    message = Message()
    message["content-type"] = content_type
    charset = message.get_content_charset()
    return charset or None


def extract_page_title(html: str, *, url: str = "") -> str:
    try:
        meta = trafilatura.extract_metadata(html, default_url=url or None)
    except Exception:
        return ""
    if meta is None:
        return ""
    title = getattr(meta, "title", None) or ""
    return " ".join(str(title).split())


class FetchRequestError(RuntimeError):
    """Network failure talking to the page host."""
