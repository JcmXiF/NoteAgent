"""Tiny valid PDFs for tests. Latin-1 text only (Helvetica)."""

from __future__ import annotations


def build_pdf(pages: list[str]) -> bytes:
    """One page object per string. Empty string = no extractable text layer."""
    return build_pdf_with_title(pages, title="")


def build_pdf_with_title(pages: list[str], *, title: str) -> bytes:
    """
    The same, with a document title in the Info dictionary.

    A PDF carries its own name there, which is what a listing shows when the file
    name is an address. Latin-1 only, like the text above.
    """
    n = len(pages)
    kids = " ".join(f"{3 + i} 0 R" for i in range(n))
    objs: list[bytes] = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    font_id = 3 + n + n
    for i in range(n):
        cid = 3 + n + i
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {cid} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
    for text in pages:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        objs.append(
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
        )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    info_id = 0
    if title:
        info_id = len(objs) + 1
        safe = title.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        objs.append(f"<< /Title ({safe}) >>".encode("latin-1"))
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    info = f" /Info {info_id} 0 R" if info_id else ""
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R{info} >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


HELLO_PDF = build_pdf(["Hello PDF"])
BLANK_PDF = build_pdf([""])
TWO_PAGE_PDF = build_pdf(["Page one", "Page two"])


def solid_rgb(
    width: int, height: int, rgb: tuple[int, int, int] = (200, 16, 16)
) -> bytes:
    red, green, blue = rgb
    return bytes((red, green, blue)) * (width * height)


def build_pdf_with_images(
    text: str,
    images: list[tuple[bytes, int, int, tuple[float, float, float, float]]],
) -> bytes:
    """One page: optional Helvetica text plus DeviceRGB Image XObjects."""
    xobject = ""
    if images:
        refs = " ".join(f"/Im{i} {6 + i} 0 R" for i in range(len(images)))
        xobject = f"/XObject << {refs} >> "
    lines: list[str] = []
    if text:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines.append(f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET")
    for index, (_pixels, _width, _height, box) in enumerate(images):
        x, y, w, h = box
        lines.append(f"q {w:g} 0 0 {h:g} {x:g} {y:g} cm /Im{index} Do Q")
    stream = ("\n".join(lines) if lines else "").encode("latin-1")
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> {xobject}>> >>"
        ).encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for pixels, width, height, _box in images:
        if len(pixels) != width * height * 3:
            raise ValueError("rgb buffer size mismatch")
        objs.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Length %d >>\nstream\n"
            % (width, height, len(pixels))
            + pixels
            + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


_RED_80 = solid_rgb(80, 80)
_RED_2 = solid_rgb(2, 2)

IMAGE_PDF = build_pdf_with_images(
    "Hello PDF",
    [(_RED_80, 80, 80, (72.0, 600.0, 80.0, 80.0))],
)
TINY_IMAGE_PDF = build_pdf_with_images(
    "Hello PDF",
    [(_RED_2, 2, 2, (72.0, 600.0, 10.0, 10.0))],
)
MIXED_SIZE_PDF = build_pdf_with_images(
    "Hello PDF",
    [
        (_RED_2, 2, 2, (72.0, 700.0, 8.0, 8.0)),
        (_RED_80, 80, 80, (72.0, 500.0, 80.0, 80.0)),
    ],
)
SCAN_WITH_TEXT_PDF = build_pdf_with_images(
    "Body text",
    [(_RED_80, 80, 80, (0.0, 0.0, 612.0, 792.0))],
)
SCAN_NO_TEXT_PDF = build_pdf_with_images(
    "",
    [(_RED_80, 80, 80, (0.0, 0.0, 612.0, 792.0))],
)
