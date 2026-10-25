"""Deterministic PNG encoding. Avoids Pillow (pypdfium2 extract() needs it)."""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_COLOR_RGB = 2
_COLOR_RGBA = 6
_COLOR_GRAY = 0

_BGR_MODES = {"BGR", "BGRX", "BGRA", "BGRa"}
_ALPHA_MODES = {"RGBA", "BGRA", "RGBa", "BGRa"}


def encode_png(width: int, height: int, pixels: bytes, *, color_type: int) -> bytes:
    """Filter-None PNG. *pixels* is packed top-down, no padding."""
    if width < 1 or height < 1:
        raise ValueError("png size must be positive")
    channels = {_COLOR_GRAY: 1, _COLOR_RGB: 3, _COLOR_RGBA: 4}[color_type]
    row = width * channels
    if len(pixels) != row * height:
        raise ValueError("pixel buffer size mismatch")
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * row : (y + 1) * row])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def png_from_bitmap(bitmap: object) -> bytes:
    """Encode a pypdfium2 PdfBitmap (or a duck-typed stand-in) as PNG."""
    return png_from_pixels(
        int(bitmap.width),
        int(bitmap.height),
        int(bitmap.stride),
        int(bitmap.n_channels),
        str(bitmap.mode),
        bytes(bitmap.buffer),
    )


def png_from_pixels(
    width: int,
    height: int,
    stride: int,
    n_channels: int,
    mode: str,
    buffer: bytes,
) -> bytes:
    if width < 1 or height < 1:
        raise ValueError("bitmap size must be positive")
    if n_channels < 1:
        raise ValueError("bitmap has no channels")
    needed = stride * (height - 1) + width * n_channels
    if len(buffer) < needed:
        raise ValueError("bitmap buffer shorter than stride*height")

    if mode == "L" or n_channels == 1:
        pixels = bytearray(width * height)
        for y in range(height):
            start = y * stride
            pixels[y * width : (y + 1) * width] = buffer[start : start + width]
        return encode_png(width, height, bytes(pixels), color_type=_COLOR_GRAY)

    has_alpha = mode in _ALPHA_MODES
    bgr = mode in _BGR_MODES
    out_ch = 4 if has_alpha else 3
    pixels = bytearray(width * height * out_ch)
    for y in range(height):
        row = buffer[y * stride : y * stride + width * n_channels]
        for x in range(width):
            i = x * n_channels
            o = (y * width + x) * out_ch
            c0, c1, c2 = row[i], row[i + 1], row[i + 2]
            if bgr:
                r, g, b = c2, c1, c0
            else:
                r, g, b = c0, c1, c2
            pixels[o] = r
            pixels[o + 1] = g
            pixels[o + 2] = b
            if has_alpha:
                pixels[o + 3] = row[i + 3] if n_channels >= 4 else 255
    color_type = _COLOR_RGBA if has_alpha else _COLOR_RGB
    return encode_png(width, height, bytes(pixels), color_type=color_type)


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        return None
    return width, height


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )
