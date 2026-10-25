"""Decode data: image URIs and sniff raster size. Does not fetch http(s)."""

from __future__ import annotations

import base64
import binascii
import struct
from urllib.parse import unquote_to_bytes

from noteagent.acquire.media.png import png_size

_JPEG_SOI = b"\xff\xd8"
_GIF87 = b"GIF87a"
_GIF89 = b"GIF89a"


def decode_data_uri(src: str) -> tuple[bytes, str] | None:
    raw = (src or "").strip()
    if not raw.lower().startswith("data:"):
        return None
    header, sep, payload = raw.partition(",")
    if not sep:
        return None
    parts = [p.strip() for p in header[5:].split(";")]
    media_type = "application/octet-stream"
    if parts and "/" in parts[0]:
        media_type = parts[0].lower()
    is_base64 = any(p.lower() == "base64" for p in parts[1:])
    if is_base64:
        blob = payload.encode("ascii", errors="ignore")
        try:
            data = base64.b64decode(blob, validate=True)
        except (binascii.Error, ValueError):
            try:
                data = base64.b64decode(blob + b"===")
            except (binascii.Error, ValueError):
                return None
    else:
        data = unquote_to_bytes(payload)
    if not data:
        return None
    return data, media_type


def sniff_image(data: bytes) -> tuple[str, str, int, int] | None:
    """
    Return ``(media_type, extension, width, height)`` when *data* looks like
    PNG / JPEG / GIF / WebP / AVIF / SVG. Width/height may be 0 if the header
    is incomplete. HTML that merely contains ``<svg`` is not an SVG original.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        size = png_size(data)
        width, height = size if size else (0, 0)
        return "image/png", "png", width, height
    if data.startswith(_JPEG_SOI):
        size = jpeg_size(data)
        width, height = size if size else (0, 0)
        return "image/jpeg", "jpg", width, height
    if data.startswith(_GIF87) or data.startswith(_GIF89):
        if len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
        else:
            width, height = 0, 0
        return "image/gif", "gif", width, height
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", "webp", 0, 0
    if len(data) >= 12 and data[4:8] == b"ftyp" and b"avif" in data[8:16]:
        return "image/avif", "avif", 0, 0
    if _looks_like_svg(data):
        return "image/svg+xml", "svg", 0, 0
    return None


def _looks_like_svg(data: bytes) -> bool:
    head = data.lstrip()[:8192].lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return False
    if head.startswith(b"<svg"):
        return True
    if head.startswith(b"<?xml"):
        rest = head.split(b">", 1)[-1].lstrip()
        return rest.startswith(b"<svg")
    return False


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(_JPEG_SOI):
        return None
    i = 2
    end = len(data)
    while i + 9 <= end:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > end:
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2:
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if i + 7 <= end:
                height, width = struct.unpack(">HH", data[i + 3 : i + 7])
                if width >= 1 and height >= 1:
                    return width, height
            break
        i += seglen
    return None
