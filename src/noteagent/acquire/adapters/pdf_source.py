"""PDF original: immutable bytes + page-level derived text."""

from __future__ import annotations

import json
from pathlib import Path

import pypdfium2 as pdfium

from noteagent.acquire.adapters.base import SourceKind
from noteagent.acquire.derive import DerivedBlock
from noteagent.acquire.media.captions import CaptionStore
from noteagent.acquire.media.extract import (
    apply_source_cap,
    captions_for_images,
    extract_pdf_page_images,
    image_marker,
    join_page_text,
    media_extract_enabled,
    persist_extracted_images,
)
from noteagent.io.atomic import write_text_atomic
from noteagent.project.layout import ProjectLayout

EMPTY_PAGE_TEXT = "（本页无文字层）"
PDF_CAPTURE_METHOD = "pdf-http"


class PdfDeriveError(RuntimeError):
    """pdfium could not open or read this file. Raw bytes are unchanged."""


class PdfSourceAdapter:
    """Persist PDF bytes under raw/pdf/; derive one block per page."""

    kind = SourceKind.PDF

    def derive(
        self,
        path: Path,
        *,
        layout: ProjectLayout | None = None,
        identity: str = "",
    ) -> list[DerivedBlock]:
        try:
            document = pdfium.PdfDocument(str(path))
        except pdfium.PdfiumError as err:
            raise PdfDeriveError(str(err)) from err
        return self._derive_document(document, layout=layout, identity=identity)

    def derive_bytes(
        self,
        data: bytes,
        *,
        layout: ProjectLayout | None = None,
        identity: str = "",
    ) -> list[DerivedBlock]:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as err:
            raise PdfDeriveError(str(err)) from err
        return self._derive_document(document, layout=layout, identity=identity)

    def preview(self, path: Path) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "renderer": "pdfjs",
            "path": Path(path).as_posix(),
        }

    def _derive_document(
        self,
        document: pdfium.PdfDocument,
        *,
        layout: ProjectLayout | None = None,
        identity: str = "",
    ) -> list[DerivedBlock]:
        enabled = media_extract_enabled()
        store = CaptionStore.for_layout(layout) if layout is not None else CaptionStore.memory()
        rows: list[tuple[str, list]] = []
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        raw = textpage.get_text_range() or ""
                    finally:
                        textpage.close()
                    compact = " ".join(str(raw).split())
                    images = []
                    if enabled:
                        images = extract_pdf_page_images(
                            page,
                            page_number=index + 1,
                            has_text=bool(compact),
                        )
                    rows.append((compact, images))
                finally:
                    page.close()
        finally:
            document.close()

        capped = apply_source_cap([image for _, images in rows for image in images])
        if layout is not None and identity:
            persist_extracted_images(layout, identity, capped, store=store)

        by_page: dict[int, list] = {}
        for image in capped:
            if image.page is None or image.skip_reason:
                continue
            by_page.setdefault(image.page, []).append(image)

        blocks: list[DerivedBlock] = []
        for index, (compact, _) in enumerate(rows):
            captions = captions_for_images(by_page.get(index + 1, []), store)
            markers = [image_marker(text) for text in captions]
            blocks.append(
                DerivedBlock(
                    id=f"p{index + 1}",
                    tag="page",
                    text=join_page_text(compact, markers, empty=EMPTY_PAGE_TEXT),
                )
            )
        if layout is not None:
            store.save()
        return blocks


def looks_like_pdf(body: bytes) -> bool:
    """True when the payload starts with a PDF header (optional leading whitespace)."""
    return body.lstrip().startswith(b"%PDF")


def derived_sidecar_path(layout: ProjectLayout, identity: str) -> Path:
    """
    Sidecar next to metadata: ``.noteagent/derived/{identity}.json``.

    Identity is the project-relative raw path (``raw/pdf/file.pdf``),
    so the file is ``file.pdf.json`` — not a directory named ``file.pdf``.
    """
    rel = identity.replace("\\", "/").lstrip("/")
    if not rel or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError(f'unsafe derived identity: "{identity}"')
    return layout.meta / "derived" / f"{rel}.json"


def save_derived_blocks(
    layout: ProjectLayout,
    identity: str,
    blocks: list[DerivedBlock],
    *,
    source_url: str = "",
    captured_at: str = "",
    kind: str = "",
    html: str = "",
    subtype: str = "",
) -> Path:
    path = derived_sidecar_path(layout, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "identity": identity,
        "kind": kind or SourceKind.PDF.value,
        "sourceUrl": source_url,
        "capturedAt": captured_at,
        "pageCount": len(blocks),
        "blocks": [block.to_dict() for block in blocks],
    }
    if html:
        payload["html"] = html
    if subtype:
        payload["subtype"] = subtype
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return path


def load_derived_payload(layout: ProjectLayout, identity: str) -> dict[str, object] | None:
    path = derived_sidecar_path(layout, identity)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def blocks_from_payload(payload: dict[str, object] | None) -> list[DerivedBlock] | None:
    if not payload:
        return None
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        return None
    blocks: list[DerivedBlock] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            continue
        bid = str(item.get("id") or "").strip()
        if not bid:
            continue
        blocks.append(
            DerivedBlock(
                id=bid,
                tag=str(item.get("tag") or "page"),
                text=str(item.get("text") or ""),
            )
        )
    return blocks
