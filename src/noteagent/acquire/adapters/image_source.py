"""Standalone image originals: immutable bytes + one whole-image leaf."""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass
from pathlib import Path

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.derive import DerivedBlock
from noteagent.acquire.media.captions import PLACEHOLDER_CAPTION, CaptionStore
from noteagent.acquire.media.extract import image_marker
from noteagent.acquire.media.data_uri import sniff_image
from noteagent.acquire.render import wrap_article_document
from noteagent.project.layout import ProjectLayout

IMAGE_CAPTURE_METHOD = "image-bytes"
IMAGE_BLOCK_ID = "img"
MAX_IMAGE_ORIGINAL_BYTES = 20_000_000
IMAGE_ACCEPT = (
    "image/avif,image/webp,image/png,image/jpeg,image/gif,image/svg+xml,"
    "application/octet-stream;q=0.9,*/*;q=0.8"
)
_GENERIC_STEMS = frozenset(
    {
        "image",
        "img",
        "photo",
        "pic",
        "picture",
        "download",
        "untitled",
        "figure",
        "fig",
        "file",
        "unnamed",
    }
)
_HASH_STEM = re.compile(r"^[0-9a-f]{8,}$", re.I)


class ImageDeriveError(RuntimeError):
    """Could not classify this as an image original. Raw bytes are unchanged."""


@dataclass(frozen=True, slots=True)
class ImageDerived:
    blocks: list[DerivedBlock]
    html: str
    media_type: str
    extension: str
    width: int
    height: int


class ImageSourceAdapter:
    """Persist png/jpeg/gif/webp/svg/avif under raw/media/."""

    kind = SourceKind.IMAGE

    def derive(
        self,
        path: Path,
        *,
        layout: ProjectLayout | None = None,
    ) -> list[DerivedBlock]:
        return self.derive_bytes(
            Path(path).read_bytes(),
            filename=Path(path).name,
            layout=layout,
        ).blocks

    def derive_bytes(
        self,
        data: bytes,
        *,
        filename: str = "",
        layout: ProjectLayout | None = None,
        title: str = "",
        source_url: str = "",
        capture_method: str = IMAGE_CAPTURE_METHOD,
        fidelity: str = "ok",
    ) -> ImageDerived:
        classified = classify_standalone_image(data)
        if classified is None:
            raise ImageDeriveError("not a standalone image")
        media_type, extension, width, height = classified
        store = CaptionStore.for_layout(layout) if layout is not None else CaptionStore.memory()
        hint = filename_caption(Path(title).stem if title else "") or filename_caption(
            Path(filename).stem
        )
        digest = hashlib.sha256(data).hexdigest()
        record = store.remember(
            digest,
            caption=hint or PLACEHOLDER_CAPTION,
            source="filename" if hint else "placeholder",
            media_type=media_type,
            width=width,
            height=height,
        )
        if layout is not None:
            store.save()
        caption = record.caption.strip() or PLACEHOLDER_CAPTION
        text = image_marker(caption)
        block = DerivedBlock(id=IMAGE_BLOCK_ID, tag="figure", text=text)
        safe_caption = html_lib.escape(caption)
        safe_text = html_lib.escape(text)
        body = (
            f'<figure id="na-{IMAGE_BLOCK_ID}" data-na-block="{IMAGE_BLOCK_ID}">'
            f'<img alt="{safe_caption}">'
            f"<figcaption>{safe_text}</figcaption>"
            "</figure>"
        )
        html = wrap_article_document(
            title=title or Path(filename).stem or "image",
            source_url=source_url,
            capture_method=capture_method,
            fidelity=fidelity,
            body_html=body,
        )
        return ImageDerived(
            blocks=[block],
            html=html,
            media_type=media_type,
            extension=extension,
            width=width,
            height=height,
        )

    def preview(self, path: Path) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "renderer": "image",
            "path": Path(path).as_posix(),
        }


def classify_standalone_image(data: bytes) -> tuple[str, str, int, int] | None:
    """Return ``(media_type, extension, width, height)`` for a standalone original."""
    if not data:
        return None
    return sniff_image(data)


def looks_like_image(data: bytes) -> bool:
    return classify_standalone_image(data) is not None


def filename_caption(stem: str) -> str:
    compact = " ".join((stem or "").replace("_", " ").replace("-", " ").split())
    if not compact:
        return ""
    token = compact.casefold()
    if token in _GENERIC_STEMS:
        return ""
    if _HASH_STEM.fullmatch(token.replace(" ", "")):
        return ""
    return compact
