"""Derived text for ingest: numbered leaf blocks with media markers.

Does not mutate raw HTML. Parent blocks that wrap other stamped blocks are not
emitted; the picker only offers leaves.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from lxml import html as lxml_html

from noteagent.acquire.media.captions import PLACEHOLDER_CAPTION
from noteagent.acquire.media.data_uri import decode_data_uri

PREVIEW_MAX = 80
_SKIP_TAGS = frozenset({"script", "style", "noscript"})


@dataclass(frozen=True, slots=True)
class DerivedBlock:
    id: str
    tag: str
    text: str

    @property
    def preview(self) -> str:
        compact = " ".join(self.text.split())
        if not compact:
            return "（空）"
        if len(compact) <= PREVIEW_MAX:
            return compact
        return compact[: PREVIEW_MAX - 1] + "…"

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "tag": self.tag,
            "text": self.text,
            "preview": self.preview,
        }


def extract_derived_blocks(
    html: str,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> list[DerivedBlock]:
    """Leaf ``data-na-block`` nodes, in document order."""
    tree = _parse(html)
    if tree is None:
        return []
    blocks: list[DerivedBlock] = []
    for el in tree.xpath("//*[@data-na-block]"):
        if not isinstance(el.tag, str):
            continue
        if el.xpath(".//*[@data-na-block]"):
            continue
        bid = (el.get("data-na-block") or "").strip()
        if not bid:
            continue
        blocks.append(
            DerivedBlock(
                id=bid,
                tag=el.tag.lower(),
                text=_block_text(el, caption_lookup=caption_lookup),
            )
        )
    return blocks


def format_derived_text(
    blocks: list[DerivedBlock],
    selected_ids: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Join selected (or all) non-empty blocks as ingest-ready text."""
    wanted = None if selected_ids is None else set(selected_ids)
    lines: list[str] = []
    for block in blocks:
        if wanted is not None and block.id not in wanted:
            continue
        body = block.text.strip()
        if not body:
            continue
        lines.append(f"[{block.id}] {body}")
    return "\n\n".join(lines)


def blocks_payload(
    html: str,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> list[dict[str, str]]:
    return [
        block.to_dict()
        for block in extract_derived_blocks(html, caption_lookup=caption_lookup)
    ]


def _parse(html: str) -> lxml_html.HtmlElement | None:
    fragment = (html or "").strip()
    if not fragment:
        return None
    try:
        return lxml_html.fromstring(fragment)
    except Exception:
        return None


def _block_text(
    el: lxml_html.HtmlElement,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    tag = el.tag.lower() if isinstance(el.tag, str) else ""
    if tag == "figure":
        return _figure_text(el, caption_lookup=caption_lookup)
    if tag == "table":
        return _table_text(el, caption_lookup=caption_lookup)
    if tag == "pre":
        return _pre_text(el, caption_lookup=caption_lookup)
    return _walk_text(el, caption_lookup=caption_lookup)


def _figure_text(
    el: lxml_html.HtmlElement,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    captions = el.xpath(".//figcaption")
    caption = (
        " ".join(captions[0].text_content().split()) if captions else ""
    )
    parts: list[str] = []
    for img in el.xpath(".//img"):
        parts.append(
            _image_marker(img, fallback=caption, caption_lookup=caption_lookup)
        )
    extra = _walk_text(
        el,
        skip_tags=frozenset({"img", "figcaption"}),
        caption_lookup=caption_lookup,
    )
    if extra:
        parts.append(extra)
    elif caption and not any(caption in part for part in parts):
        parts.append(caption)
    return " ".join(part for part in parts if part)


def _table_text(
    el: lxml_html.HtmlElement,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    rows: list[str] = []
    for tr in el.xpath("./thead/tr | ./tbody/tr | ./tfoot/tr | ./tr"):
        cells = [
            " ".join(cell.text_content().split())
            for cell in tr.xpath("./th | ./td")
        ]
        if any(cells):
            rows.append(" | ".join(cells))
    body = "\n".join(rows)
    markers = [
        _image_marker(img, caption_lookup=caption_lookup)
        for img in el.xpath(".//img")
    ]
    if markers:
        extra = " ".join(markers)
        return f"{body}\n{extra}".strip() if body else extra
    return body


def _pre_text(
    el: lxml_html.HtmlElement,
    *,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    lines = [line.rstrip() for line in el.text_content().splitlines()]
    body = "\n".join(lines).strip()
    markers = [
        _image_marker(img, caption_lookup=caption_lookup)
        for img in el.xpath(".//img")
    ]
    if markers:
        extra = " ".join(markers)
        return f"{body}\n{extra}".strip() if body else extra
    return body


def _walk_text(
    el: lxml_html.HtmlElement,
    *,
    skip_tags: frozenset[str] | None = None,
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    skip = skip_tags or frozenset()
    parts: list[str] = []

    def push(raw: str | None) -> None:
        if not raw:
            return
        text = " ".join(raw.split())
        if text:
            parts.append(text)

    def visit(node: lxml_html.HtmlElement) -> None:
        if not isinstance(node.tag, str):
            return
        name = node.tag.lower()
        if name in _SKIP_TAGS:
            return
        if name in skip and node is not el:
            return
        if node is not el and node.get("data-na-block"):
            return
        if name == "img":
            parts.append(_image_marker(node, caption_lookup=caption_lookup))
            return
        push(node.text)
        for child in node:
            visit(child)
            push(child.tail)

    visit(el)
    return " ".join(parts)


def _image_marker(
    img: lxml_html.HtmlElement,
    *,
    fallback: str = "",
    caption_lookup: Callable[[str], str | None] | None = None,
) -> str:
    caption = (
        (img.get("alt") or img.get("title") or "").strip()
        or fallback.strip()
        or PLACEHOLDER_CAPTION
    )
    src = (img.get("src") or "").strip()
    if caption_lookup is not None and src.startswith("data:"):
        decoded = decode_data_uri(src)
        if decoded is not None:
            digest = hashlib.sha256(decoded[0]).hexdigest()
            hit = (caption_lookup(digest) or "").strip()
            # A cached caption wins unless it is only the placeholder and the
            # page already carries real alt text.
            if hit and (hit != PLACEHOLDER_CAPTION or caption == PLACEHOLDER_CAPTION):
                caption = hit
    if img.get("data-na-asset") == "missing" or not src:
        kind = "图缺失"
    elif src.startswith("data:"):
        kind = "图已本地化"
    else:
        kind = "图未本地化"
    return f"[{kind} {caption}]"


__all__ = [
    "DerivedBlock",
    "blocks_payload",
    "extract_derived_blocks",
    "format_derived_text",
]
