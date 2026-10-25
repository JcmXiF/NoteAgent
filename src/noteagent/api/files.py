"""Safe path resolution and tree listing for a wiki project."""

from __future__ import annotations

import shutil
from pathlib import Path

from noteagent.acquire.adapters.image_source import (
    ImageDeriveError,
    ImageSourceAdapter,
)
from noteagent.acquire.adapters.markdown_source import (
    markdown_blocks,
    markdown_document,
)
from noteagent.acquire.adapters.office_source import (
    OfficeDeriveError,
    OfficeSourceAdapter,
)
from noteagent.acquire.adapters.pdf_source import (
    PdfDeriveError,
    PdfSourceAdapter,
    blocks_from_payload,
    derived_sidecar_path,
    load_derived_payload,
)
from noteagent.acquire.derive import blocks_payload, format_derived_text
from noteagent.acquire.media.captions import CaptionStore
from noteagent.ingest.clips import (
    drop_collections_for_source,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.revision_inbox import note_source_change
from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.io.document_title import document_title
from noteagent.io.paths import prune_empty_parents
from noteagent.project.identity import source_identity
from noteagent.project.layout import (
    RAW_DIR,
    WIKI_INDEX,
    WIKI_LOG,
    WIKI_OVERVIEW,
    ProjectLayout,
)
from noteagent.project.trash import move_to_trash
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger
from noteagent.wiki.editing import page_backlinks

ALLOWED_ROOTS = ("wiki", "raw")
PDF_MEDIA_TYPE = "application/pdf"
_OFFICE_SUFFIXES = frozenset({".docx", ".pptx"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".avif": "image/avif",
}


def resolve_project_file(layout: ProjectLayout, relative: str) -> Path:
    """Resolve ``relative`` under the project root; must stay inside wiki/ or raw/."""
    normalized = relative.replace("\\", "/").lstrip("./")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError(f"unsafe path: {relative}")
    top = normalized.split("/", 1)[0].lower()
    if top not in ALLOWED_ROOTS:
        raise ValueError(f"path must be under wiki/ or raw/: {relative}")

    candidate = (layout.root / normalized).resolve()
    try:
        candidate.relative_to(layout.root.resolve())
    except ValueError as err:
        raise ValueError(f"path escapes project root: {relative}") from err

    # Re-check prefix after resolve (symlink safety).
    try:
        rel = candidate.relative_to(layout.root.resolve()).as_posix()
    except ValueError as err:
        raise ValueError(f"path escapes project root: {relative}") from err
    if not rel.startswith("wiki/") and not rel.startswith("raw/"):
        if rel not in {"wiki", "raw"}:
            raise ValueError(f"path must be under wiki/ or raw/: {relative}")
    return candidate


def _tree_title(path: Path, *, wiki: bool, on_problem=None) -> str:
    """What to call one file in the tree.

    A wiki page is named by its slug — that is what links and the graph are written
    in terms of — and a markdown source by its file name, which is already a name.
    A stored original of any other kind was filed under the address it came from
    plus a code, so its name has to come from the document itself; when the document
    carries none this is empty, and the caller reads the name off the file name.
    """
    if wiki or path.suffix.lower() == ".md":
        return path.stem
    return document_title(path, on_problem=on_problem)


def list_project_tree(layout: ProjectLayout) -> list[dict[str, str]]:
    """Flat list of wiki/raw files the docs page can open: markdown, HTML originals,
    PDF originals, Office originals, and standalone images. Other binaries stay off
    the tree."""
    allowed = {".md", ".html", ".htm", ".pdf", ".docx", ".pptx", *_IMAGE_MEDIA_TYPES}
    # A title that could not be read is worth knowing about: without this it would
    # look exactly like a document that has no title.
    run_log = RunLogger.for_layout(layout)
    entries: list[dict[str, str]] = []
    roots = [
        ("wiki", layout.wiki),
        ("raw", layout.raw),
    ]
    for label, base in roots:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), key=lambda p: p.as_posix().casefold()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed:
                continue
            try:
                rel = path.relative_to(layout.root).as_posix()
            except ValueError:
                continue
            entries.append(
                {
                    "path": rel,
                    "name": path.name,
                    "title": _tree_title(
                        path,
                        wiki=label == "wiki",
                        on_problem=lambda why, name=rel: run_log.warning(
                            "could not read a document's title",
                            path=name,
                            reason=why,
                        ),
                    ),
                }
            )
    return entries


_PROTECTED_PAGES = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})


def delete_project_file(
    layout: ProjectLayout,
    relative: str,
    *,
    project: str = "",
    trash_base: Path | None = None,
) -> dict[str, object]:
    """Remove one wiki page or raw original, keeping what was removed.

    System pages (index / overview / log) stay. A removal is moved into the trash
    beside the projects rather than unlinked: a stored source is often the only copy
    of what a page was written from. What was derived from it goes with it and is
    read again if the original comes back.

    *trash_base* is where removals are kept. It has to be the projects folder rather
    than the project's own parent, because a project opened where it stands may have
    been anywhere.
    """
    path = resolve_project_file(layout, relative)
    if not path.is_file():
        raise Refusal("file.missing", f"file not found: {relative}", status=404)
    rel = path.relative_to(layout.root).as_posix()
    if rel in _PROTECTED_PAGES:
        raise Refusal(
            "file.protected",
            f"the program maintains this file: {rel}",
        )

    identity = source_identity(path, project_root=layout.root)
    # What deleting a page breaks is what links to it. Counted before the move,
    # because afterwards there is no page to ask about.
    backlinks: dict[str, object] = {"count": 0, "notes": 0, "pages": []}
    if rel.startswith("wiki/"):
        try:
            backlinks = page_backlinks(layout, rel)
        except Refusal:
            backlinks = {"count": 0, "notes": 0, "pages": []}
    kept = move_to_trash(
        Path(trash_base) if trash_base is not None else layout.root.parent,
        path,
        kind="file",
        name=path.name,
        project=project or layout.root.name,
        relative_path=rel,
        removed_by="file.delete",
        project_path=layout.root,
    )
    dropped = 0
    if rel.startswith("raw/"):
        # What was read out of the original is derived, not the original: it is
        # dropped with it and read again if the original comes back. Keeping it
        # would let a different file at the same path inherit it.
        try:
            derived_sidecar_path(layout, identity).unlink(missing_ok=True)
        except ValueError:
            pass
        media_dir = layout.media.joinpath(*identity.split("/"))
        if media_dir.is_dir():
            shutil.rmtree(media_dir)
        store = load_clip_store(layout)
        next_store = drop_collections_for_source(store, identity)
        dropped = len(store.collections) - len(next_store.collections)
        if dropped:
            save_clip_store(layout, next_store)
        prune_empty_parents(path.parent, layout.raw)
    elif rel.startswith("wiki/"):
        rebuild_wiki_index(layout)
        prune_empty_parents(path.parent, layout.wiki)
    note_source_change(layout)
    return {
        "deleted": rel,
        "droppedCollections": dropped,
        "trashId": kept["id"],
        "backlinkCount": int(backlinks.get("count") or 0),
        "backlinkPages": backlinks.get("pages") or [],
        "noteCount": int(backlinks.get("notes") or 0),
    }


def read_project_text(layout: ProjectLayout, relative: str) -> dict[str, object]:
    path = resolve_project_file(layout, relative)
    if not path.is_file():
        raise Refusal("file.missing", f"file not found: {relative}", status=404)
    rel = path.relative_to(layout.root).as_posix()
    if path.suffix.lower() == ".pdf":
        return _read_pdf(layout, path, rel)
    if path.suffix.lower() in _OFFICE_SUFFIXES:
        return _read_office(layout, path, rel)
    if path.suffix.lower() in _IMAGE_MEDIA_TYPES:
        return _read_image(layout, path, rel)
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    payload: dict[str, object] = {
        "path": rel,
        "content": text,
        "kind": "html" if suffix in {".html", ".htm"} else "markdown",
        "binary": False,
    }
    if suffix in {".html", ".htm"}:
        store = CaptionStore.for_layout(layout)
        payload["blocks"] = blocks_payload(text, caption_lookup=store.lookup)
        payload["renderer"] = "html-sandbox"
    elif suffix in _MARKDOWN_SUFFIXES and rel.startswith(f"{RAW_DIR}/"):
        # A stored markdown original is a source like any other, so it is shown as
        # its blocks rather than as one block of text — which is what lets a passage
        # of it be selected. Only what is under raw/: a wiki page has its own reader,
        # and its text is not picked from this screen.
        payload["blocks"] = [block.to_dict() for block in markdown_blocks(text)]
        payload["html"] = markdown_document(text, title=_tree_title(path, wiki=False))
        payload["renderer"] = "markdown-blocks"
    return payload


def read_project_bytes(layout: ProjectLayout, relative: str) -> tuple[Path, str]:
    """Resolve a PDF or standalone-image original for FileResponse."""
    path = resolve_project_file(layout, relative)
    if not path.is_file():
        raise Refusal("file.missing", f"file not found: {relative}", status=404)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return path, PDF_MEDIA_TYPE
    media_type = _IMAGE_MEDIA_TYPES.get(suffix)
    if media_type:
        return path, media_type
    raise Refusal(
        "file.notOriginal", "file-bytes only serves PDF or image originals"
    )


def _read_pdf(layout: ProjectLayout, path: Path, rel: str) -> dict[str, object]:
    identity = source_identity(path, project_root=layout.root)
    sidecar = load_derived_payload(layout, identity)
    blocks = blocks_from_payload(sidecar)
    source_url = ""
    if isinstance(sidecar, dict):
        source_url = str(sidecar.get("sourceUrl") or "")
    if blocks is None:
        try:
            blocks = PdfSourceAdapter().derive(
                path,
                layout=layout,
                identity=identity,
            )
        except PdfDeriveError:
            blocks = []
    preview = PdfSourceAdapter().preview(path)
    return {
        "path": rel,
        "content": format_derived_text(blocks),
        "blocks": [block.to_dict() for block in blocks],
        "kind": "pdf",
        "renderer": preview.get("renderer", "pdfjs"),
        "binary": True,
        "sourceUrl": source_url,
        "pageCount": len(blocks),
    }


def _read_office(layout: ProjectLayout, path: Path, rel: str) -> dict[str, object]:
    identity = source_identity(path, project_root=layout.root)
    sidecar = load_derived_payload(layout, identity)
    source_url = ""
    html = ""
    blocks = None
    if isinstance(sidecar, dict):
        source_url = str(sidecar.get("sourceUrl") or "")
        html = str(sidecar.get("html") or "")
        blocks = blocks_from_payload(sidecar)
    if not html or blocks is None:
        try:
            derived = OfficeSourceAdapter().derive_bytes(
                path.read_bytes(),
                filename=path.name,
                layout=layout,
                identity=identity,
                title=path.stem,
                source_url=source_url,
            )
            html = derived.html
            blocks = derived.blocks
        except (OfficeDeriveError, OSError, ValueError):
            # Nothing readable came back. An empty body lets the screen say so in the
            # reader's language; a sentence written here would not be.
            html = html or ""
            blocks = blocks or []
    preview = OfficeSourceAdapter().preview(path)
    return {
        "path": rel,
        "content": html,
        "blocks": [block.to_dict() for block in blocks],
        "kind": "office",
        "renderer": preview.get("renderer", "html-sandbox"),
        "binary": True,
        "sourceUrl": source_url,
        "pageCount": len(blocks),
    }


def _read_image(layout: ProjectLayout, path: Path, rel: str) -> dict[str, object]:
    identity = source_identity(path, project_root=layout.root)
    sidecar = load_derived_payload(layout, identity)
    source_url = ""
    blocks = None
    if isinstance(sidecar, dict):
        source_url = str(sidecar.get("sourceUrl") or "")
        blocks = blocks_from_payload(sidecar)
    if blocks is None:
        try:
            derived = ImageSourceAdapter().derive_bytes(
                path.read_bytes(),
                filename=path.name,
                layout=layout,
                title=path.stem,
                source_url=source_url,
            )
            blocks = derived.blocks
        except (ImageDeriveError, OSError, ValueError):
            blocks = []
    preview = ImageSourceAdapter().preview(path)
    return {
        "path": rel,
        "content": format_derived_text(blocks),
        "blocks": [block.to_dict() for block in blocks],
        "kind": "image",
        "renderer": preview.get("renderer", "image"),
        "binary": True,
        "sourceUrl": source_url,
        "pageCount": len(blocks),
    }
