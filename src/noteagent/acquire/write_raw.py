"""Write quality-gated captures under raw/. LLM never writes here."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

from noteagent.acquire.adapters.image_source import (
    ImageDeriveError,
    ImageSourceAdapter,
    classify_standalone_image,
)
from noteagent.acquire.adapters.office_source import (
    OfficeDeriveError,
    OfficeSourceAdapter,
    sniff_office_package,
)
from noteagent.acquire.adapters.pdf_source import (
    PdfDeriveError,
    PdfSourceAdapter,
    save_derived_blocks,
)
from noteagent.acquire.media.extract import persist_html_media
from noteagent.acquire.quality import assess_capture
from noteagent.acquire.tools.fetchers.article import visible_text_from_html
from noteagent.acquire.types import Capture
from noteagent.io.atomic import write_text_atomic
from noteagent.project.identity import source_identity
from noteagent.project.layout import RAW_DIR, ProjectLayout
from noteagent.project.raw_kinds import (
    MEDIA_KIND,
    OFFICE_KIND,
    PDF_KIND,
    WEB_KIND,
)
from noteagent.util.run_log import RunLogger

WEB_RAW_SUBDIR = WEB_KIND
PDF_RAW_SUBDIR = PDF_KIND
OFFICE_RAW_SUBDIR = OFFICE_KIND
IMAGE_RAW_SUBDIR = MEDIA_KIND
LOCAL_RAW_HOST = "local"

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


@dataclass(frozen=True, slots=True)
class RawWriteItem:
    """Outcome of attempting to save one capture."""

    code: str  # written | replaced | skipped
    path: str
    url: str
    message: str


@dataclass(frozen=True, slots=True)
class WriteRawResult:
    """Batch result for saving captures into raw/."""

    items: tuple[RawWriteItem, ...]

    @property
    def written_paths(self) -> tuple[str, ...]:
        return tuple(
            item.path for item in self.items if item.code in {"written", "replaced"}
        )


def raw_relative_path_for_url(url: str, *, title: str = "", kind: str = "html") -> str:
    """
    Stable project-relative path for a capture URL under raw/.

    *title* is ignored so the same URL always maps to the same file.
    HTML goes to ``web/`` with ``.html``; PDF goes to ``pdf/`` with ``.pdf``;
    Office goes to ``office/`` with ``.docx`` / ``.pptx``; standalone images
    go to ``media/``. Files sit directly in the kind folder (not per host).
    """
    del title
    cleaned = url.strip()
    parsed = urlparse(cleaned)
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]
    stem = _slug_from_path(parsed.path)
    if kind == "pdf":
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4] or "document"
        filename = f"{stem}-{digest}.pdf"
        return f"{RAW_DIR}/{PDF_RAW_SUBDIR}/{filename}"
    if kind == "office":
        suffix = _office_suffix(stem, url=cleaned)
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)] or "document"
        filename = f"{stem}-{digest}{suffix}"
        return f"{RAW_DIR}/{OFFICE_RAW_SUBDIR}/{filename}"
    if kind == "image":
        suffix = _image_suffix(stem, url=cleaned)
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)] or "image"
        filename = f"{stem}-{digest}{suffix}"
        return f"{RAW_DIR}/{IMAGE_RAW_SUBDIR}/{filename}"
    filename = f"{stem}-{digest}.html"
    return f"{RAW_DIR}/{WEB_RAW_SUBDIR}/{filename}"


def is_local_capture_url(url: str) -> bool:
    return url.strip().startswith("local:")


def parse_local_capture_url(url: str) -> tuple[str, str] | None:
    cleaned = url.strip()
    if not cleaned.startswith("local:"):
        return None
    rest = cleaned[len("local:") :]
    kind, sep, digest = rest.partition(":")
    if not sep or not kind or not digest:
        return None
    return kind, digest


def raw_relative_path_for_local(
    url: str,
    *,
    filename: str,
    kind: str,
) -> str:
    """Content-hash path under raw/{pdf|web|office|media}/."""
    parsed = parse_local_capture_url(url)
    digest = parsed[1] if parsed else hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    stem = _safe_segment(Path(filename).stem or "file", fallback="file")
    if kind == "pdf":
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4] or "document"
        filename = f"{stem}-{digest}.pdf"
        return f"{RAW_DIR}/{PDF_RAW_SUBDIR}/{filename}"
    if kind == "office":
        suffix = _office_suffix(filename, url=url)
        filename = f"{stem}-{digest}{suffix}"
        return f"{RAW_DIR}/{OFFICE_RAW_SUBDIR}/{filename}"
    if kind == "image":
        suffix = _image_suffix(filename, url=url)
        filename = f"{stem}-{digest}{suffix}"
        return f"{RAW_DIR}/{IMAGE_RAW_SUBDIR}/{filename}"
    filename = f"{stem}-{digest}.html"
    return f"{RAW_DIR}/{WEB_RAW_SUBDIR}/{filename}"


def resolve_raw_write_path(layout: ProjectLayout, relative_path: str) -> Path:
    """
    Resolve a raw path and require it stay inside that directory.
    """
    normalized = relative_path.replace("\\", "/").lstrip("./")
    if not normalized.startswith(f"{RAW_DIR}/"):
        raise ValueError(f'not a raw path: "{relative_path}"')
    if any(ord(ch) < 32 for ch in normalized):
        raise ValueError(f'unsafe raw path: "{relative_path}"')
    segments = normalized.split("/")
    if any(seg in {"", ".", ".."} for seg in segments):
        raise ValueError(f'unsafe raw path: "{relative_path}"')
    candidate = (layout.root / normalized).resolve()
    raw_root = layout.raw.resolve()
    try:
        candidate.relative_to(raw_root)
    except ValueError as err:
        raise ValueError(
            f'resolved path escapes raw/: "{relative_path}" -> {candidate}'
        ) from err
    return candidate


def render_capture_source(capture: Capture, *, captured_at: str | None = None) -> str:
    """Self-contained reader HTML. *captured_at* is recorded as a meta tag."""
    stamp = captured_at or date.today().isoformat()
    document = (capture.html or "").strip()
    if not document:
        raise ValueError("capture has no HTML original")
    if 'name="noteagent:captured_at"' in document:
        return document if document.endswith("\n") else document + "\n"
    meta = f'  <meta name="noteagent:captured_at" content="{_html_attr(stamp)}">\n'
    head_close = document.lower().find("</head>")
    if head_close == -1:
        return document + "\n"
    return document[:head_close] + meta + document[head_close:]


def _html_attr(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )


def write_raw_captures(
    layout: ProjectLayout,
    captures: list[Capture] | tuple[Capture, ...],
    *,
    dry_run: bool = False,
    captured_at: str | None = None,
    run_logger: RunLogger | None = None,
) -> WriteRawResult:
    """
    Save ok/low captures. Failed or empty extracts are skipped.
    Same URL overwrites the same file (acquire may refresh; LLM still cannot).
    """
    logger = run_logger or RunLogger.disabled()
    items: list[RawWriteItem] = []
    for capture in captures:
        item = _write_one(
            layout,
            capture,
            dry_run=dry_run,
            captured_at=captured_at,
            logger=logger,
        )
        items.append(item)
    return WriteRawResult(items=tuple(items))


def format_write_raw_result(result: WriteRawResult) -> str:
    if not result.items:
        return "raw: (nothing to write)\n"
    lines: list[str] = []
    for item in result.items:
        lines.append(f"{item.code}: {item.path or item.url}")
        if item.message and item.code == "skipped":
            lines.append(f"  {item.message}")
    return "\n".join(lines) + "\n"


def _write_one(
    layout: ProjectLayout,
    capture: Capture,
    *,
    dry_run: bool,
    captured_at: str | None,
    logger: RunLogger,
) -> RawWriteItem:
    url = capture.url.strip()
    if capture.fidelity == "failed":
        return RawWriteItem("skipped", "", url, "failed quality gate")
    if _is_pdf_capture(capture):
        return _write_pdf(
            layout,
            capture,
            dry_run=dry_run,
            captured_at=captured_at,
            logger=logger,
        )
    if _is_office_capture(capture):
        return _write_office(
            layout,
            capture,
            dry_run=dry_run,
            captured_at=captured_at,
            logger=logger,
        )
    if _is_image_capture(capture):
        return _write_image(
            layout,
            capture,
            dry_run=dry_run,
            captured_at=captured_at,
            logger=logger,
        )

    if not is_local_capture_url(url):
        fidelity, reasons = assess_capture(
            _capture_text(capture),
            images_failed=capture.images_failed,
        )
        if fidelity == "failed":
            reason = reasons[0] if reasons else "empty"
            return RawWriteItem("skipped", "", url, f"quality gate: {reason}")
    if not (capture.html or "").strip():
        return RawWriteItem("skipped", "", url, "no html original")

    relative = _raw_relative_for_capture(capture, kind="html")
    try:
        absolute = resolve_raw_write_path(layout, relative)
    except ValueError as err:
        return RawWriteItem("skipped", relative, url, str(err))

    existed = absolute.is_file()
    code = "replaced" if existed else "written"
    try:
        text = render_capture_source(capture, captured_at=captured_at)
    except ValueError as err:
        return RawWriteItem("skipped", relative, url, str(err))
    identity = source_identity(absolute, project_root=layout.root)
    if not dry_run:
        write_text_atomic(absolute, text)
        try:
            persist_html_media(layout, identity, text)
        except (OSError, ValueError):
            pass
    logger.append(
        "raw.write",
        component="acquire",
        url=url,
        path=identity,
        mode=code,
        fidelity=capture.fidelity,
        chars=len(_capture_text(capture)),
        images=capture.image_count or None,
        images_failed=capture.images_failed or None,
        dry_run=dry_run or None,
    )
    return RawWriteItem(code, identity, url, "")


def _raw_relative_for_capture(capture: Capture, *, kind: str) -> str:
    url = capture.url.strip()
    if is_local_capture_url(url):
        filename = capture.title or (
            "document.pdf"
            if kind == "pdf"
            else "document.docx"
            if kind == "office"
            else "image.png"
            if kind == "image"
            else "page.html"
        )
        if kind == "office" and capture.office_bytes:
            package = sniff_office_package(capture.office_bytes)
            if package == "pptx" and not filename.lower().endswith(".pptx"):
                filename = f"{Path(filename).stem}.pptx"
            elif package == "docx" and not filename.lower().endswith(".docx"):
                filename = f"{Path(filename).stem}.docx"
        if kind == "image" and capture.image_bytes:
            classified = classify_standalone_image(capture.image_bytes)
            if classified is not None:
                suffix = f".{classified[1]}"
                if not filename.lower().endswith(suffix):
                    filename = f"{Path(filename).stem}{suffix}"
        return raw_relative_path_for_local(url, filename=filename, kind=kind)
    relative = raw_relative_path_for_url(capture.final_url or url, kind=kind)
    if kind == "office" and capture.office_bytes:
        package = sniff_office_package(capture.office_bytes)
        if package == "pptx" and not relative.lower().endswith(".pptx"):
            relative = relative.rsplit(".", 1)[0] + ".pptx"
        elif package == "docx" and not relative.lower().endswith(".docx"):
            relative = relative.rsplit(".", 1)[0] + ".docx"
    if kind == "image" and capture.image_bytes:
        classified = classify_standalone_image(capture.image_bytes)
        if classified is not None:
            suffix = f".{classified[1]}"
            if not relative.lower().endswith(suffix):
                relative = relative.rsplit(".", 1)[0] + suffix
    return relative


def _is_pdf_capture(capture: Capture) -> bool:
    if capture.pdf_bytes:
        return True
    return (capture.kind or "").lower() == "pdf"


def _is_office_capture(capture: Capture) -> bool:
    if capture.office_bytes:
        return True
    return (capture.kind or "").lower() == "office"


def _is_image_capture(capture: Capture) -> bool:
    if capture.image_bytes:
        return True
    return (capture.kind or "").lower() == "image"


def _office_suffix(name: str, *, url: str = "") -> str:
    path = urlparse(url).path if url else ""
    for token in (name, path):
        if str(token).lower().endswith(".pptx"):
            return ".pptx"
    return ".docx"


def _image_suffix(name: str, *, url: str = "") -> str:
    path = urlparse(url).path if url else ""
    for token in (name, path):
        lowered = str(token).lower()
        for suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"):
            if lowered.endswith(suffix):
                return ".jpg" if suffix == ".jpeg" else suffix
    return ".png"


def _write_image(
    layout: ProjectLayout,
    capture: Capture,
    *,
    dry_run: bool,
    captured_at: str | None,
    logger: RunLogger,
) -> RawWriteItem:
    url = capture.url.strip()
    if not capture.image_bytes:
        return RawWriteItem("skipped", "", url, "no image original")
    relative = _raw_relative_for_capture(capture, kind="image")
    try:
        absolute = resolve_raw_write_path(layout, relative)
    except ValueError as err:
        return RawWriteItem("skipped", relative, url, str(err))

    existed = absolute.is_file()
    code = "replaced" if existed else "written"
    identity = source_identity(absolute, project_root=layout.root)
    stamp = captured_at or date.today().isoformat()
    if not dry_run:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(capture.image_bytes)
        try:
            derived = ImageSourceAdapter().derive_bytes(
                capture.image_bytes,
                filename=capture.title or absolute.name,
                layout=layout,
                title=capture.title or absolute.stem,
                source_url=capture.final_url or url,
                capture_method=capture.capture_method or "image-bytes",
                fidelity=capture.fidelity,
            )
            save_derived_blocks(
                layout,
                identity,
                derived.blocks,
                source_url=capture.final_url or url,
                captured_at=stamp,
                kind="image",
                html=derived.html,
                subtype=derived.extension,
            )
        except (ImageDeriveError, ValueError, OSError):
            pass
    logger.append(
        "raw.write",
        component="acquire",
        url=url,
        path=identity,
        mode=code,
        fidelity=capture.fidelity,
        chars=capture.char_count,
        pages=capture.page_count or None,
        kind="image",
        dry_run=dry_run or None,
    )
    return RawWriteItem(code, identity, url, "")


def _write_office(
    layout: ProjectLayout,
    capture: Capture,
    *,
    dry_run: bool,
    captured_at: str | None,
    logger: RunLogger,
) -> RawWriteItem:
    url = capture.url.strip()
    if not capture.office_bytes:
        return RawWriteItem("skipped", "", url, "no office original")
    relative = _raw_relative_for_capture(capture, kind="office")
    try:
        absolute = resolve_raw_write_path(layout, relative)
    except ValueError as err:
        return RawWriteItem("skipped", relative, url, str(err))

    existed = absolute.is_file()
    code = "replaced" if existed else "written"
    identity = source_identity(absolute, project_root=layout.root)
    stamp = captured_at or date.today().isoformat()
    if not dry_run:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(capture.office_bytes)
        try:
            derived = OfficeSourceAdapter().derive_bytes(
                capture.office_bytes,
                filename=absolute.name,
                layout=layout,
                identity=identity,
                title=capture.title or absolute.stem,
                source_url=capture.final_url or url,
                capture_method=capture.capture_method or "office-ooxml",
                fidelity=capture.fidelity,
            )
            save_derived_blocks(
                layout,
                identity,
                derived.blocks,
                source_url=capture.final_url or url,
                captured_at=stamp,
                kind="office",
                html=derived.html,
                subtype=derived.subtype,
            )
        except (OfficeDeriveError, ValueError, OSError):
            pass
    logger.append(
        "raw.write",
        component="acquire",
        url=url,
        path=identity,
        mode=code,
        fidelity=capture.fidelity,
        chars=capture.char_count,
        pages=capture.page_count or None,
        kind="office",
        dry_run=dry_run or None,
    )
    return RawWriteItem(code, identity, url, "")


def _write_pdf(
    layout: ProjectLayout,
    capture: Capture,
    *,
    dry_run: bool,
    captured_at: str | None,
    logger: RunLogger,
) -> RawWriteItem:
    url = capture.url.strip()
    if not capture.pdf_bytes:
        return RawWriteItem("skipped", "", url, "no pdf original")
    relative = _raw_relative_for_capture(capture, kind="pdf")
    try:
        absolute = resolve_raw_write_path(layout, relative)
    except ValueError as err:
        return RawWriteItem("skipped", relative, url, str(err))

    existed = absolute.is_file()
    code = "replaced" if existed else "written"
    identity = source_identity(absolute, project_root=layout.root)
    stamp = captured_at or date.today().isoformat()
    if not dry_run:
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(capture.pdf_bytes)
        try:
            blocks = PdfSourceAdapter().derive(
                absolute,
                layout=layout,
                identity=identity,
            )
            save_derived_blocks(
                layout,
                identity,
                blocks,
                source_url=capture.final_url or url,
                captured_at=stamp,
            )
        except (PdfDeriveError, ValueError, OSError):
            pass
    logger.append(
        "raw.write",
        component="acquire",
        url=url,
        path=identity,
        mode=code,
        fidelity=capture.fidelity,
        chars=capture.char_count,
        pages=capture.page_count or None,
        kind="pdf",
        dry_run=dry_run or None,
    )
    return RawWriteItem(code, identity, url, "")


def _capture_text(capture: Capture) -> str:
    html_text = visible_text_from_html(capture.html)
    if html_text:
        return html_text
    return capture.markdown


def _slug_from_path(path: str) -> str:
    decoded = unquote(path or "")
    leaf = decoded.rstrip("/").rsplit("/", 1)[-1]
    return _safe_segment(leaf, fallback="page")


def _safe_segment(text: str, *, fallback: str) -> str:
    cleaned = _UNSAFE_CHARS.sub("-", text)
    cleaned = re.sub(r"\s+", "-", cleaned).strip(" .-")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip(" .-") or fallback
    return cleaned
