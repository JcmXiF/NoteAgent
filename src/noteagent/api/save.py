"""HTTP helpers for writing captures into raw/."""

from __future__ import annotations

import base64
import binascii

from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import WriteRawResult, write_raw_captures
from noteagent.api.projects import require_project_layout
from noteagent.ingest.revision_inbox import note_source_change
from noteagent.util.run_log import RunLogger


def capture_from_api(
    *,
    url: str,
    markdown: str,
    title: str = "",
    capture_method: str = "trafilatura-html",
    fidelity: str = "",
    reasons: list[str] | None = None,
    http_status: int | None = None,
    final_url: str = "",
    char_count: int = 0,
    html: str = "",
    image_count: int = 0,
    images_failed: int = 0,
    kind: str = "",
    pdf_base64: str = "",
    office_base64: str = "",
    image_base64: str = "",
    page_count: int = 0,
) -> Capture:
    body = markdown or ""
    document = html or ""
    pdf_bytes = b""
    blob = (pdf_base64 or "").strip()
    if blob:
        try:
            pdf_bytes = base64.b64decode(blob, validate=False)
        except (binascii.Error, ValueError):
            pdf_bytes = b""
    office_bytes = b""
    office_blob = (office_base64 or "").strip()
    if office_blob:
        try:
            office_bytes = base64.b64decode(office_blob, validate=False)
        except (binascii.Error, ValueError):
            office_bytes = b""
    image_bytes = b""
    image_blob = (image_base64 or "").strip()
    if image_blob:
        try:
            image_bytes = base64.b64decode(image_blob, validate=False)
        except (binascii.Error, ValueError):
            image_bytes = b""
    resolved_kind = (kind or "").strip() or (
        "pdf"
        if pdf_bytes
        else "office"
        if office_bytes
        else "image"
        if image_bytes
        else "html"
    )
    return Capture(
        url=url.strip(),
        title=title,
        markdown=body,
        html=document,
        capture_method=capture_method or "trafilatura-html",
        fidelity=fidelity or "failed",
        reasons=tuple(reasons or ()),
        http_status=http_status,
        final_url=final_url or url.strip(),
        char_count=char_count or len(body),
        image_count=image_count,
        images_failed=images_failed,
        kind=resolved_kind,
        pdf_bytes=pdf_bytes,
        office_bytes=office_bytes,
        image_bytes=image_bytes,
        page_count=page_count,
    )


def write_item_to_dict(item) -> dict[str, str]:
    return {
        "code": item.code,
        "path": item.path,
        "url": item.url,
        "message": item.message,
    }


def run_save_payload(captures: list[Capture], *, project: str) -> dict[str, object]:
    layout = require_project_layout(project)
    logger = RunLogger.for_layout(layout)
    result = write_raw_captures(layout, captures, run_logger=logger)
    note_source_change(layout)
    return _result_payload(result)


def _result_payload(result: WriteRawResult) -> dict[str, object]:
    return {
        "written": list(result.written_paths),
        "items": [write_item_to_dict(item) for item in result.items],
    }


__all__ = [
    "capture_from_api",
    "run_save_payload",
]
