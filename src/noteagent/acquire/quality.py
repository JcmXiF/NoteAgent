"""Quality gates for a fetched page. Judge this capture, not the whole host."""

from __future__ import annotations

FIDELITY_OK = "ok"
FIDELITY_LOW = "low"
FIDELITY_FAILED = "failed"

MIN_CHARS = 280


def _visible_text(markdown: str) -> str:
    return " ".join(markdown.split())


def assess_capture(
    markdown: str,
    *,
    truncated: bool = False,
    skip_reason: str | None = None,
    images_failed: int = 0,
) -> tuple[str, tuple[str, ...]]:
    """
    Return (fidelity, reason codes) for an already-fetched body.

    HTTP status is decided before extraction; this gate looks at
    skip codes, emptiness, length, truncation, and whether images
    failed to localize.
    """
    if skip_reason:
        return FIDELITY_FAILED, (skip_reason,)

    text = _visible_text(markdown)
    if not text:
        return FIDELITY_FAILED, ("empty",)

    if len(text) < MIN_CHARS:
        return FIDELITY_FAILED, ("too_short",)

    low: list[str] = []
    if truncated:
        low.append("truncated")
    if images_failed > 0:
        low.append("missing_images")
    if low:
        return FIDELITY_LOW, tuple(low)
    return FIDELITY_OK, ()


def assess_local_html(
    markdown: str,
    *,
    truncated: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Local HTML: empty fails; short text is low, not failed."""
    if truncated:
        return FIDELITY_FAILED, ("truncated",)
    text = _visible_text(markdown)
    if not text:
        return FIDELITY_FAILED, ("empty",)
    if len(text) < MIN_CHARS:
        return FIDELITY_LOW, ("too_short",)
    return FIDELITY_OK, ()


def assess_pdf_capture(
    *,
    page_count: int,
    text_chars: int,
    truncated: bool = False,
    invalid: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Quality for a PDF original. Do not apply the HTML MIN_CHARS gate."""
    if invalid:
        return FIDELITY_FAILED, ("invalid_pdf",)
    if truncated:
        return FIDELITY_FAILED, ("truncated",)
    if page_count < 1:
        return FIDELITY_FAILED, ("empty",)
    if text_chars <= 0:
        return FIDELITY_LOW, ("no_text_layer",)
    return FIDELITY_OK, ()


def assess_office_capture(
    *,
    block_count: int,
    text_chars: int,
    truncated: bool = False,
    invalid: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Quality for a docx/pptx original. Short text is low, still saveable."""
    if invalid:
        return FIDELITY_FAILED, ("invalid_office",)
    if truncated:
        return FIDELITY_FAILED, ("truncated",)
    if block_count < 1:
        return FIDELITY_FAILED, ("empty",)
    if text_chars <= 0:
        return FIDELITY_LOW, ("empty_text",)
    if text_chars < MIN_CHARS:
        return FIDELITY_LOW, ("too_short",)
    return FIDELITY_OK, ()


def assess_image_capture(
    *,
    truncated: bool = False,
    invalid: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Quality for a standalone image original. Do not apply the HTML length gate."""
    if invalid:
        return FIDELITY_FAILED, ("invalid_image",)
    if truncated:
        return FIDELITY_FAILED, ("truncated",)
    return FIDELITY_OK, ()
