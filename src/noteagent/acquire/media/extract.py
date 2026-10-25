"""Extract images to ``.noteagent/media/`` work copies. Never writes wiki/media."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from lxml import html as lxml_html

from noteagent.acquire.media.captions import (
    PLACEHOLDER_CAPTION,
    CaptionStore,
)
from noteagent.acquire.media.data_uri import decode_data_uri, sniff_image
from noteagent.acquire.media.png import png_from_bitmap
from noteagent.project.layout import ProjectLayout

MIN_SIDE = 32
MIN_PIXELS = 80 * 80
MAX_PER_SOURCE = 20
PAGE_SCAN_COVER = 0.85
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_BYTES = 8_000_000
PDF_MARKER_KIND = "图已抽出"

_ENV_OFF = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class ExtractedImage:
    sha256: str
    data: bytes
    media_type: str
    extension: str
    width: int
    height: int
    page: int | None
    hint: str
    caption_source: str = "placeholder"
    skip_reason: str = ""


def media_extract_enabled() -> bool:
    raw = os.environ.get("NOTEAGENT_EXTRACT_MEDIA", "1").strip().lower()
    return raw not in _ENV_OFF


def media_work_dir(layout: ProjectLayout, identity: str) -> Path:
    rel = _safe_identity(identity)
    return layout.media / rel


def image_marker(caption: str, *, kind: str = PDF_MARKER_KIND) -> str:
    cap = caption.strip() or PLACEHOLDER_CAPTION
    return f"[{kind} {cap}]"


def join_page_text(page_text: str, markers: list[str], *, empty: str) -> str:
    compact = " ".join((page_text or "").split())
    kept = [m for m in markers if m]
    if compact:
        return " ".join([compact, *kept]) if kept else compact
    if kept:
        return " ".join(kept)
    return empty


def extract_pdf_page_images(
    page: pdfium.PdfPage,
    *,
    page_number: int,
    has_text: bool,
) -> list[ExtractedImage]:
    """Images on one open pdfium page. Caller must keep the page alive."""
    found: list[ExtractedImage] = []
    seen: set[str] = set()
    try:
        objects = page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])
    except pdfium.PdfiumError:
        return []
    page_area = max(float(page.get_width()) * float(page.get_height()), 1.0)
    for obj in objects:
        try:
            image = _take_pdf_image(obj, page_number, has_text, page_area)
        except (pdfium.PdfiumError, ValueError, OSError, ArithmeticError):
            continue
        if image is None:
            continue
        if not image.skip_reason:
            if image.sha256 in seen:
                continue
            seen.add(image.sha256)
        found.append(image)
    return found


def image_from_bytes(
    data: bytes,
    *,
    hint: str = "",
    caption_source: str = "placeholder",
    page: int | None = None,
) -> ExtractedImage | None:
    """Turn raster bytes into a filtered work-copy candidate."""
    sniffed = sniff_image(data)
    if sniffed is None:
        return None
    media_type, extension, width, height = sniffed
    return _finish_image(
        data,
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
        page=page,
        hint=hint,
        caption_source=caption_source,
        skip_reason="",
    )


def extract_html_images(html: str) -> list[ExtractedImage]:
    fragment = (html or "").strip()
    if not fragment:
        return []
    try:
        tree = lxml_html.fromstring(fragment)
    except Exception:
        return []
    found: list[ExtractedImage] = []
    seen: set[str] = set()
    for img in tree.xpath("//img"):
        if img.get("data-na-asset") == "missing":
            continue
        src = (img.get("src") or "").strip()
        decoded = decode_data_uri(src)
        if decoded is None:
            continue
        data, media_type = decoded
        sniffed = sniff_image(data)
        if sniffed is None:
            continue
        sniffed_type, extension, width, height = sniffed
        hint, caption_source = _html_hint(img)
        image = _finish_image(
            data,
            media_type=sniffed_type or media_type,
            extension=extension,
            width=width,
            height=height,
            page=None,
            hint=hint,
            caption_source=caption_source,
            skip_reason="",
        )
        if not image.skip_reason:
            if image.sha256 in seen:
                continue
            seen.add(image.sha256)
        found.append(image)
    return found


def apply_source_cap(
    images: list[ExtractedImage],
    *,
    limit: int = MAX_PER_SOURCE,
) -> list[ExtractedImage]:
    kept = 0
    out: list[ExtractedImage] = []
    for image in images:
        if image.skip_reason:
            out.append(image)
            continue
        if kept >= limit:
            out.append(replace(image, skip_reason="cap"))
            continue
        kept += 1
        out.append(image)
    return out


def persist_extracted_images(
    layout: ProjectLayout,
    identity: str,
    images: list[ExtractedImage],
    *,
    store: CaptionStore | None = None,
) -> list[ExtractedImage]:
    """Write work copies under ``.noteagent/media/{identity}/``. Does not touch raw."""
    captions = store if store is not None else CaptionStore.for_layout(layout)
    kept = [image for image in images if not image.skip_reason]
    if not kept:
        return []
    folder = media_work_dir(layout, identity)
    folder.mkdir(parents=True, exist_ok=True)
    for image in kept:
        path = folder / f"{image.sha256}.{image.extension}"
        if not path.is_file():
            if len(image.data) > MAX_IMAGE_BYTES:
                continue
            path.write_bytes(image.data)
        captions.remember(
            image.sha256,
            caption=image.hint or PLACEHOLDER_CAPTION,
            source=image.caption_source if image.hint else "placeholder",
            media_type=image.media_type,
            width=image.width,
            height=image.height,
        )
    captions.save()
    return kept


def persist_html_media(
    layout: ProjectLayout,
    identity: str,
    html: str,
    *,
    store: CaptionStore | None = None,
) -> list[ExtractedImage]:
    if not media_extract_enabled():
        return []
    images = apply_source_cap(extract_html_images(html))
    return persist_extracted_images(layout, identity, images, store=store)


def captions_for_images(
    images: list[ExtractedImage],
    store: CaptionStore,
) -> list[str]:
    texts: list[str] = []
    for image in images:
        if image.skip_reason:
            continue
        record = store.remember(
            image.sha256,
            caption=image.hint or PLACEHOLDER_CAPTION,
            source=image.caption_source if image.hint else "placeholder",
            media_type=image.media_type,
            width=image.width,
            height=image.height,
        )
        texts.append(record.caption)
    return texts


def _take_pdf_image(
    obj: object,
    page_number: int,
    has_text: bool,
    page_area: float,
) -> ExtractedImage | None:
    if not isinstance(obj, pdfium.PdfImage):
        return None
    skip = ""
    width, height = _px_size(obj)
    if _too_small(width, height):
        skip = "too_small"
    elif width * height > MAX_IMAGE_PIXELS:
        skip = "too_large"
    elif has_text and _covers_page(obj, page_area):
        skip = "page_scan"
    if skip:
        return ExtractedImage(
            sha256="",
            data=b"",
            media_type="",
            extension="png",
            width=width,
            height=height,
            page=page_number,
            hint="",
            caption_source="placeholder",
            skip_reason=skip,
        )
    encoded = _encode_pdf_image(obj)
    if encoded is None:
        return None
    data, media_type, extension, bw, bh = encoded
    if _too_small(bw, bh):
        return ExtractedImage(
            sha256="",
            data=b"",
            media_type=media_type,
            extension=extension,
            width=bw,
            height=bh,
            page=page_number,
            hint="",
            caption_source="placeholder",
            skip_reason="too_small",
        )
    return _finish_image(
        data,
        media_type=media_type,
        extension=extension,
        width=bw or width,
        height=bh or height,
        page=page_number,
        hint="",
        caption_source="placeholder",
        skip_reason="",
    )


def _encode_pdf_image(image: pdfium.PdfImage) -> tuple[bytes, str, str, int, int] | None:
    jpeg = _try_jpeg_bytes(image)
    if jpeg is not None:
        sniffed = sniff_image(jpeg)
        if sniffed is None:
            return jpeg, "image/jpeg", "jpg", 0, 0
        media_type, extension, width, height = sniffed
        return jpeg, media_type, extension, width, height
    try:
        bitmap = image.get_bitmap(render=False)
    except pdfium.PdfiumError:
        return None
    try:
        data = png_from_bitmap(bitmap)
        width, height = int(bitmap.width), int(bitmap.height)
    finally:
        bitmap.close()
    if len(data) > MAX_IMAGE_BYTES:
        return None
    return data, "image/png", "png", width, height


def _try_jpeg_bytes(image: pdfium.PdfImage) -> bytes | None:
    try:
        filters = image.get_filters()
    except pdfium.PdfiumError:
        return None
    if filters != ["DCTDecode"]:
        return None
    try:
        raw = bytes(image.get_data(decode_simple=False))
    except pdfium.PdfiumError:
        return None
    if raw.startswith(b"\xff\xd8") and len(raw) <= MAX_IMAGE_BYTES:
        return raw
    return None


def _finish_image(
    data: bytes,
    *,
    media_type: str,
    extension: str,
    width: int,
    height: int,
    page: int | None,
    hint: str,
    caption_source: str = "placeholder",
    skip_reason: str,
) -> ExtractedImage:
    skip = skip_reason
    if not skip and _too_small(width, height):
        skip = "too_small"
    if not skip and len(data) > MAX_IMAGE_BYTES:
        skip = "too_large"
    digest = hashlib.sha256(data).hexdigest() if data and not skip else ""
    return ExtractedImage(
        sha256=digest,
        data=data if not skip else b"",
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
        page=page,
        hint=hint,
        caption_source=caption_source if hint else "placeholder",
        skip_reason=skip,
    )


def _px_size(image: pdfium.PdfImage) -> tuple[int, int]:
    try:
        width, height = image.get_px_size()
    except pdfium.PdfiumError:
        return 0, 0
    return int(width), int(height)


def _too_small(width: int, height: int) -> bool:
    if width < 1 or height < 1:
        return False
    return min(width, height) < MIN_SIDE or width * height < MIN_PIXELS


def _covers_page(image: pdfium.PdfImage, page_area: float) -> bool:
    try:
        left, bottom, right, top = image.get_bounds()
    except (pdfium.PdfiumError, RuntimeError):
        return False
    area = abs(float(right) - float(left)) * abs(float(top) - float(bottom))
    return area / page_area >= PAGE_SCAN_COVER


def _html_hint(img: lxml_html.HtmlElement) -> tuple[str, str]:
    captions = img.xpath("ancestor::figure[1]//figcaption")
    figcaption = (
        " ".join(captions[0].text_content().split()) if captions else ""
    )
    if figcaption:
        return figcaption, "figcaption"
    alt = (img.get("alt") or img.get("title") or "").strip()
    if alt:
        return alt, "alt"
    return "", "placeholder"


def _safe_identity(identity: str) -> str:
    rel = identity.replace("\\", "/").lstrip("/")
    if not rel or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError(f'unsafe media identity: "{identity}"')
    return rel
