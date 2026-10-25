"""HTTP helpers for acquire fetch (no write to raw/)."""

from __future__ import annotations

import base64
import binascii

from noteagent.acquire.adapters.image_source import (
    ImageDeriveError,
    ImageSourceAdapter,
)
from noteagent.acquire.adapters.office_source import (
    OfficeDeriveError,
    OfficeSourceAdapter,
)
from noteagent.acquire.adapters.pdf_source import (
    PdfDeriveError,
    PdfSourceAdapter,
)
from noteagent.acquire.derive import blocks_payload
from noteagent.acquire.intake.channels import infer_intake
from noteagent.acquire.intake.local_file import capture_from_local_file
from noteagent.acquire.run_fetch import run_rendered_html, run_web_fetch_many
from noteagent.acquire.types import Capture
from noteagent.api.projects import logger_for_project


def capture_to_dict(capture: Capture) -> dict[str, object]:
    kind = capture.kind or (
        "pdf"
        if capture.pdf_bytes
        else "office"
        if capture.office_bytes
        else "image"
        if capture.image_bytes
        else "html"
    )
    payload: dict[str, object] = {
        "url": capture.url,
        "finalUrl": capture.final_url or capture.url,
        "title": capture.title,
        "markdown": capture.markdown,
        "html": capture.html,
        "captureMethod": capture.capture_method,
        "fidelity": capture.fidelity,
        "reasons": list(capture.reasons),
        "httpStatus": capture.http_status,
        "charCount": capture.char_count,
        "imageCount": capture.image_count,
        "imagesFailed": capture.images_failed,
        "kind": kind,
        "pageCount": capture.page_count,
        "intake": infer_intake(url=capture.url, capture_method=capture.capture_method),
    }
    if kind == "pdf":
        blocks: list[dict[str, str]] = []
        if capture.pdf_bytes:
            try:
                blocks = [
                    block.to_dict()
                    for block in PdfSourceAdapter().derive_bytes(capture.pdf_bytes)
                ]
            except PdfDeriveError:
                blocks = []
            payload["pdfBase64"] = base64.b64encode(capture.pdf_bytes).decode("ascii")
        payload["blocks"] = blocks
    elif kind == "office":
        if capture.office_bytes:
            payload["officeBase64"] = base64.b64encode(capture.office_bytes).decode(
                "ascii"
            )
        if capture.html:
            payload["blocks"] = blocks_payload(capture.html)
        elif capture.office_bytes:
            try:
                payload["blocks"] = [
                    block.to_dict()
                    for block in OfficeSourceAdapter().derive_bytes(capture.office_bytes).blocks
                ]
            except OfficeDeriveError:
                payload["blocks"] = []
        else:
            payload["blocks"] = []
    elif kind == "image":
        if capture.image_bytes:
            payload["imageBase64"] = base64.b64encode(capture.image_bytes).decode(
                "ascii"
            )
            classified = None
            try:
                derived = ImageSourceAdapter().derive_bytes(capture.image_bytes)
                payload["imageMediaType"] = derived.media_type
                classified = derived
            except ImageDeriveError:
                classified = None
            if capture.html:
                payload["blocks"] = blocks_payload(capture.html)
            elif classified is not None:
                payload["blocks"] = [block.to_dict() for block in classified.blocks]
            else:
                payload["blocks"] = []
        elif capture.html:
            payload["blocks"] = blocks_payload(capture.html)
        else:
            payload["blocks"] = []
    else:
        payload["blocks"] = blocks_payload(capture.html)
    return payload


def run_fetch_payload(
    urls: list[str],
    *,
    query: str = "",
    project: str,
) -> dict[str, object]:
    captures = run_web_fetch_many(
        urls,
        query=query,
        run_logger=logger_for_project(project),
        caller="api",
    )
    return {
        "query": " ".join(query.split()),
        "captures": [capture_to_dict(item) for item in captures],
    }


def run_rendered_payload(
    url: str,
    html: str,
    *,
    project: str,
) -> dict[str, object]:
    capture = run_rendered_html(
        url,
        html,
        run_logger=logger_for_project(project),
        caller="api",
    )
    return {
        "url": capture.url,
        "captures": [capture_to_dict(capture)],
    }


def run_local_payload(
    files: list[dict[str, str]],
    *,
    project: str,
) -> dict[str, object]:
    """Preview local files as captures. Does not write raw/."""
    logger = logger_for_project(project)
    captures: list[Capture] = []
    for item in files:
        name = (item.get("name") or "file").strip() or "file"
        media_type = (item.get("mediaType") or "").strip()
        blob = (item.get("contentBase64") or "").strip()
        data = _decode_local_b64(blob)
        if data is None:
            capture = Capture(
                url="local:file:invalid",
                title=name,
                markdown="",
                html="",
                capture_method="local-file",
                fidelity="failed",
                reasons=("invalid_payload",),
                final_url="local:file:invalid",
                kind="html",
            )
        else:
            capture = capture_from_local_file(
                filename=name,
                data=data,
                media_type=media_type,
            )
        captures.append(capture)
        logger.append(
            "intake.local",
            component="acquire",
            filename=name,
            kind=capture.kind,
            fidelity=capture.fidelity,
            chars=capture.char_count or None,
            pages=capture.page_count or None,
        )
    return {
        "captures": [capture_to_dict(item) for item in captures],
    }


def _decode_local_b64(blob: str) -> bytes | None:
    if not blob:
        return None
    try:
        return base64.b64decode(blob, validate=False)
    except (ValueError, binascii.Error):
        return None


__all__ = [
    "capture_to_dict",
    "run_fetch_payload",
    "run_rendered_payload",
    "run_local_payload",
]
