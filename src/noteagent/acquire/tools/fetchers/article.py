"""Turn a public HTML page into a sandboxed, self-contained article."""

from __future__ import annotations

import base64
import html as html_lib
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from lxml import html as lxml_html

from noteagent.acquire.tools.fetchers.html import (
    EXTRACT_CONFIG,
    FETCH_USER_AGENT,
    extract_page_title,
)

MAX_ASSET_BYTES = 5_000_000
MAX_ASSETS = 40
ASSET_TIMEOUT = 20.0

_BLOCK_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "pre",
        "blockquote",
        "table",
        "figure",
        "ul",
        "ol",
    }
)
_DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "applet",
        "form",
        "input",
        "button",
        "textarea",
        "select",
        "link",
        "meta",
        "base",
        "noscript",
        "template",
    }
)
_IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/avif": "avif",
}

# Trafilatura drops data: / blob: images. Park them as http URLs with an image suffix.
_INLINE_PLACEHOLDER_PREFIX = "https://cdn.example.com/na-inline/"


def extract_article_html(html: str, *, url: str = "") -> tuple[str, str]:
    """
    Reader-mode HTML fragment. Empty body means extraction failed.
    """
    title = extract_page_title(html, url=url)
    prepared, parked = _park_non_http_images(html)
    extracted = trafilatura.extract(
        prepared,
        url=url or None,
        output_format="html",
        include_links=True,
        include_tables=True,
        include_comments=False,
        include_images=True,
        include_formatting=True,
        config=EXTRACT_CONFIG,
    )
    body = (extracted or "").strip()
    if parked and body:
        body = _restore_parked_images(body, parked)
    return title, body


def _park_non_http_images(html: str) -> tuple[str, dict[str, str]]:
    fragment = (html or "").strip()
    if not fragment:
        return html, {}
    try:
        tree = lxml_html.fromstring(fragment)
    except Exception:
        return html, {}
    parked: dict[str, str] = {}
    index = 0
    for el in tree.iter():
        if not isinstance(el.tag, str) or el.tag.lower() != "img":
            continue
        src = (el.get("src") or "").strip()
        if not src or not _needs_image_park(src):
            continue
        token = f"{_INLINE_PLACEHOLDER_PREFIX}{index}.png"
        parked[token] = src
        index += 1
    if not parked:
        return html, {}
    prepared = html
    for token, original in parked.items():
        prepared = prepared.replace(original, token, 1)
    return prepared, parked


def _restore_parked_images(article: str, parked: dict[str, str]) -> str:
    try:
        tree = lxml_html.fromstring(article)
    except Exception:
        text = article
        for token, original in parked.items():
            text = text.replace(token, original)
        return text
    for el in tree.iter():
        if not isinstance(el.tag, str) or el.tag.lower() != "img":
            continue
        src = (el.get("src") or "").strip()
        if src in parked:
            el.set("src", parked[src])
            continue
        for token, original in parked.items():
            if token in src:
                el.set("src", original)
                break
    return lxml_html.tostring(tree, encoding="unicode")


def _needs_image_park(src: str) -> bool:
    lowered = src.strip()
    if lowered.startswith("data:") or lowered.startswith("blob:"):
        return True
    parsed = urlparse(lowered)
    return bool(parsed.scheme) and parsed.scheme not in {"http", "https"}


def visible_text_from_html(html: str) -> str:
    fragment = (html or "").strip()
    if not fragment:
        return ""
    try:
        tree = lxml_html.fromstring(fragment)
    except Exception:
        return " ".join(fragment.split())
    return " ".join(tree.text_content().split())


def build_article_body(
    article_html: str,
    *,
    page_url: str,
    client: httpx.Client | None = None,
    localize: bool = True,
) -> tuple[str, int, int]:
    """Sanitize a reader-mode fragment and optionally inline its images."""
    tree = _parse_article(article_html)
    _sanitize_tree(tree)
    _unwrap_nested_pre(tree)
    _drop_trailing_duplicate_images(tree)
    if localize:
        image_ok, image_failed = localize_images(
            tree,
            page_url=page_url,
            client=client,
        )
    else:
        image_ok, image_failed = 0, 0
    stamp_block_ids(tree)
    return _serialize_article_inner(tree), image_ok, image_failed


def stamp_block_ids(tree: lxml_html.HtmlElement) -> None:
    """Give headings, paragraphs, figures, and images stable na-bN ids."""
    index = 0
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.lower()
        if tag in _BLOCK_TAGS:
            pass
        elif tag == "img" and not _inside_tag(el, "figure"):
            parent = el.getparent()
            if (
                parent is not None
                and isinstance(parent.tag, str)
                and parent.tag.lower() in _BLOCK_TAGS
            ):
                continue
        else:
            continue
        index += 1
        bid = f"b{index}"
        el.set("id", f"na-{bid}")
        el.set("data-na-block", bid)


def localize_images(
    tree: lxml_html.HtmlElement,
    *,
    page_url: str,
    client: httpx.Client | None = None,
) -> tuple[int, int]:
    """
    Download http(s) images with Referer=page and rewrite src to data URIs.
    """
    ok = 0
    failed = 0
    seen = 0
    own_client = client is None
    http = client or httpx.Client(
        timeout=ASSET_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": FETCH_USER_AGENT},
    )
    try:
        for el in list(tree.iter()):
            if not isinstance(el.tag, str) or el.tag.lower() != "img":
                continue
            src = _image_source(el)
            if not src:
                failed += 1
                _mark_missing_image(el)
                continue
            if src.startswith("data:"):
                ok += 1
                continue
            seen += 1
            if seen > MAX_ASSETS:
                failed += 1
                _mark_missing_image(el)
                continue
            absolute = urljoin(page_url, src)
            data_uri = _download_image_data_uri(http, absolute, referer=page_url)
            if data_uri is None:
                failed += 1
                _mark_missing_image(el)
                continue
            el.set("src", data_uri)
            if "data-src" in el.attrib:
                del el.attrib["data-src"]
            if "srcset" in el.attrib:
                del el.attrib["srcset"]
            ok += 1
    finally:
        if own_client:
            http.close()
    return ok, failed


def _parse_article(article_html: str) -> lxml_html.HtmlElement:
    fragment = (article_html or "").strip() or "<div></div>"
    tree = lxml_html.fromstring(fragment)
    if tree.tag.lower() == "html":
        body = tree.find("body")
        if body is not None:
            return body
    return tree


def _serialize_article_inner(tree: lxml_html.HtmlElement) -> str:
    tag = tree.tag.lower() if isinstance(tree.tag, str) else ""
    if tag in {"body", "html"}:
        parts = []
        if tree.text and tree.text.strip():
            parts.append(html_lib.escape(tree.text))
        for child in tree:
            parts.append(
                lxml_html.tostring(child, encoding="unicode", with_tail=True)
            )
        return "".join(parts)
    return lxml_html.tostring(tree, encoding="unicode")


def _sanitize_tree(tree: lxml_html.HtmlElement) -> None:
    for el in list(tree.iter()):
        if not isinstance(el.tag, str):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
            continue
        tag = el.tag.lower()
        if tag in _DROP_TAGS:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)
            continue
        for attr in list(el.attrib):
            lowered = attr.lower()
            if lowered.startswith("on") or lowered in {"srcdoc", "formaction"}:
                del el.attrib[attr]
                continue
            if lowered in {"href", "src", "xlink:href"}:
                value = (el.get(attr) or "").strip()
                if value.lower().startswith("javascript:"):
                    del el.attrib[attr]


def _unwrap_nested_pre(tree: lxml_html.HtmlElement) -> None:
    for pre in list(tree.xpath(".//pre")):
        children = [child for child in pre if isinstance(child.tag, str)]
        if (
            len(children) == 1
            and children[0].tag.lower() == "pre"
            and not (pre.text or "").strip()
        ):
            inner = children[0]
            pre.text = inner.text
            for child in list(inner):
                pre.append(child)
            pre.remove(inner)


def _drop_trailing_duplicate_images(tree: lxml_html.HtmlElement) -> None:
    images = [el for el in tree.iter() if isinstance(el.tag, str) and el.tag.lower() == "img"]
    if len(images) < 2:
        return
    last = images[-1]
    src = _image_source(last)
    if not src:
        return
    if not any(item is not last and _image_source(item) == src for item in images):
        return
    parent = last.getparent()
    if parent is None:
        return
    siblings = [child for child in parent if isinstance(child.tag, str)]
    if parent.tag.lower() == "p" and siblings == [last] and not (parent.text or "").strip():
        grand = parent.getparent()
        if grand is not None:
            grand.remove(parent)
        return
    parent.remove(last)


def _inside_tag(el: lxml_html.HtmlElement, tag: str) -> bool:
    current = el.getparent()
    while current is not None:
        if isinstance(current.tag, str) and current.tag.lower() == tag:
            return True
        current = current.getparent()
    return False


def _image_source(el: lxml_html.HtmlElement) -> str:
    src = (el.get("src") or "").strip()
    if not src or src.startswith("data:"):
        data_src = (el.get("data-src") or "").strip()
        if data_src:
            return data_src
    if src.startswith("data:"):
        return src
    return src


def _mark_missing_image(el: lxml_html.HtmlElement) -> None:
    el.set("data-na-asset", "missing")
    if "src" in el.attrib:
        del el.attrib["src"]
    if "srcset" in el.attrib:
        del el.attrib["srcset"]
    if not (el.get("alt") or "").strip():
        el.set("alt", "有图无说明")


def _download_image_data_uri(
    client: httpx.Client,
    url: str,
    *,
    referer: str,
) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    headers = {
        "User-Agent": FETCH_USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": referer,
    }
    try:
        response = client.get(url, headers=headers, timeout=ASSET_TIMEOUT)
    except httpx.HTTPError:
        return None
    if not httpx.codes.is_success(response.status_code):
        return None
    body = response.content[: MAX_ASSET_BYTES + 1]
    if len(body) > MAX_ASSET_BYTES or not body:
        return None
    media = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media not in _IMAGE_TYPES:
        media = _sniff_image_type(body)
        if media is None:
            return None
    encoded = base64.b64encode(body).decode("ascii")
    return f"data:{media};base64,{encoded}"


def _sniff_image_type(body: bytes) -> str | None:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"GIF87a") or body.startswith(b"GIF89a"):
        return "image/gif"
    if body.startswith(b"RIFF") and b"WEBP" in body[:16]:
        return "image/webp"
    if body.lstrip().startswith(b"<svg") or body.lstrip().startswith(b"<?xml"):
        return "image/svg+xml"
    return None
