"""Write parsed FILE blocks into the project's wiki.

Ordinary pages are created, or merged with the page already on disk; clip ledgers
union deterministically; ``wiki/log.md`` appends. Every real write keeps the
version it replaced in the page history. Warnings carry a code, so callers can
tell a hard failure (``skipped`` / ``failed`` / ``repair_failed``) from a soft
merge fallback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from noteagent.ingest.clip_ledger import (
    is_clip_ledger_path,
    merge_clip_ledger,
)
from noteagent.ingest.frontmatter import (
    ensure_wiki_frontmatter,
    sanitize_log_entry,
)
from noteagent.ingest.page_history import keep_before, note_agent_write
from noteagent.ingest.page_merge import (
    MergeFn,
    PageMergeResult,
    RepairFn,
    merge_page_content,
)
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.wiki_paths import resolve_wiki_write_path
from noteagent.io.text_file import to_lf, write_text
from noteagent.project.layout import WIKI_LOG, ProjectLayout
from noteagent.util.run_log import RunLogger

__all__ = [
    "HARD_WRITE_CODES",
    "WriteFileBlocksResult",
    "WriteWarning",
    "WrittenFile",
    "format_write_result",
    "write_file_blocks",
]


HARD_WRITE_CODES = frozenset({"skipped", "failed", "repair_failed"})


@dataclass(frozen=True, slots=True)
class WriteWarning:
    """Structured write warning. ``message`` is for humans; logic uses ``code``."""

    code: str
    message: str
    path: str = ""


@dataclass(frozen=True, slots=True)
class WrittenFile:
    """
    One successfully written wiki file.
    """

    relative_path: str
    absolute_path: Path
    bytes_written: int
    mode: str  # create | overwrite | append | merge | replace | repair | merge-fallback | …


@dataclass(frozen=True, slots=True)
class WriteFileBlocksResult:
    """
    Result of writing parsed FILE blocks to disk.
    """

    written: tuple[WrittenFile, ...]
    warnings: tuple[WriteWarning, ...]


def _is_log_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").lower()
    return normalized == WIKI_LOG


def write_file_blocks(
    layout: ProjectLayout,
    blocks: list[FileBlock] | tuple[FileBlock, ...],
    *,
    dry_run: bool = False,
    source_identity: str = "",
    material: Mapping[str, Iterable[str]] | None = None,
    merger: MergeFn | None = None,
    repair: RepairFn | None = None,
    rewrite_drafts: Mapping[str, str] | None = None,
    today: Callable[[], str] | None = None,
    run_logger: RunLogger | None = None,
) -> WriteFileBlocksResult:
    """
    Write parsed FILE blocks into the active wiki project.

    - Ordinary pages: create, or merge via ``merge_page_content`` when the
      path already exists (LLM body merge / replace / fallback).
    - ``wiki/sources/*-clips.md`` ledgers: create, or **union** via
      ``merge_clip_ledger`` — the ledger is a source list, so the core combines
      it without asking a model to write about the merge.
    - ``wiki/log.md``: append-only (create if missing).
    - Index updates are handled separately by the caller.

    ``material`` maps a written path to the sources that page was made from, so
    each page decides on its own whether it can be rewritten instead of merged.
    A path with no entry is treated as written from nothing: its existing sources
    cannot all be accounted for, so it is merged. ``source_identity`` stays the
    whole-write label used in prompts and the run log; it also stands in as the
    material when no mapping is given, which is a source read end to end.

    ``repair`` is one second attempt at a page the merge refused, told what it was
    refused for (rule R7b in ``page_merge``). A page refused twice is reported
    with the code ``repair_failed``, which is hard: the page was not written, so
    the compile is not a success the cache may remember.

    ``rewrite_drafts`` maps a written path to the draft its rewrite replaced, for
    the pages the rewrite pass (R0-1) wrote again. R4b needs both texts: the page
    on disk and the draft the reply was written from.
    """

    logger = run_logger or RunLogger.disabled()
    written: list[WrittenFile] = []
    warnings: list[WriteWarning] = []
    drafts = (
        {path.replace("\\", "/"): body for path, body in rewrite_drafts.items()}
        if rewrite_drafts
        else None
    )

    for block in blocks:
        try:
            absolute = resolve_wiki_write_path(layout, block.path)
        except ValueError as err:
            msg = f"skipped {block.path}: {err}"
            warnings.append(WriteWarning("skipped", msg, block.path.replace("\\", "/")))
            logger.warning(msg, path=block.path.replace("\\", "/"))
            continue

        content = to_lf(block.content)
        relative = block.path.replace("\\", "/")
        page_material = (
            material.get(relative, ()) if material is not None else None
        )
        is_log = _is_log_path(block.path)
        base_mode = "create"

        if is_log:
            content = sanitize_log_entry(content)
            if not content.strip():
                msg = f"skipped {block.path}: empty log entry after sanitize"
                warnings.append(WriteWarning("skipped", msg, relative))
                logger.warning(msg, path=relative)
                continue
        else:
            fm = ensure_wiki_frontmatter(content)
            content = fm.content
            if fm.code:
                warnings.append(
                    WriteWarning(fm.code, f"{relative}: {fm.message}", relative)
                )
                logger.warning(fm.message or fm.code, path=relative)

        if not is_log and absolute.exists():
            try:
                existing = absolute.read_text(encoding="utf-8")
            except OSError as err:
                msg = f'failed to read existing "{block.path}": {err}'
                warnings.append(WriteWarning("failed", msg, relative))
                logger.warning(msg, path=relative)
                continue
            existing_fm = ensure_wiki_frontmatter(existing)
            existing = existing_fm.content
            if existing_fm.code == "repaired":
                warnings.append(
                    WriteWarning(
                        "repaired",
                        f"{relative} (on disk): {existing_fm.message}",
                        relative,
                    )
                )
                logger.warning(existing_fm.message or "repaired", path=relative)
            if is_clip_ledger_path(relative):
                merge_result: PageMergeResult = merge_clip_ledger(
                    existing,
                    content,
                    today=today,
                )
            else:
                merge_result = merge_page_content(
                    content,
                    existing,
                    merger,
                    source_identity=source_identity,
                    material=page_material,
                    page_path=relative,
                    rewrite_draft=drafts.get(relative) if drafts else None,
                    repair=repair,
                    today=today,
                )
            content = merge_result.content
            base_mode = merge_result.mode
            if merge_result.warning:
                code = (
                    "repair_failed"
                    if merge_result.mode == "repair-fallback"
                    else "merge_fallback"
                )
                warnings.append(
                    WriteWarning(code, merge_result.warning, relative)
                )
                logger.warning(
                    merge_result.warning,
                    path=relative,
                    mode=merge_result.mode,
                )
        elif is_log:
            base_mode = "append" if absolute.exists() else "create"
        else:
            base_mode = "create"

        if not is_log and content and not content.endswith("\n"):
            content = content + "\n"

        if dry_run:
            mode = f"dry-run:{base_mode}"
            item = WrittenFile(
                relative_path=relative,
                absolute_path=absolute,
                bytes_written=len(content.encode("utf-8")),
                mode=mode,
            )
            written.append(item)
            logger.page_write(
                path=relative,
                mode=mode,
                bytes_written=item.bytes_written,
                source_identity=source_identity or None,
            )
            continue

        try:
            absolute.parent.mkdir(parents=True, exist_ok=True)

            if is_log:
                mode = "append" if absolute.exists() else "create"
                existing = (
                    absolute.read_text(encoding="utf-8") if absolute.exists() else ""
                )
                entry = content.strip()
                if existing.strip():
                    next_text = existing.rstrip() + "\n\n" + entry + "\n"
                else:
                    next_text = entry + "\n"
                write_text(absolute, next_text)
                # An append reports the entry it added; the page writers report the
                # whole file, which is what `bytes_written` means for them.
                bytes_written = len(entry.encode("utf-8"))
            else:
                mode = base_mode
                # The file's own bytes, read again for the store: what is kept is
                # the version this write replaces, not the merged or normalised
                # text the new content was worked out from.
                replaced = (
                    keep_before(layout, absolute.read_text(encoding="utf-8"))
                    if absolute.exists()
                    else ""
                )
                bytes_written = write_text(absolute, content)
                note_agent_write(
                    layout,
                    relative,
                    before=replaced,
                    after_text=content,
                    mode=mode,
                )

            written.append(
                WrittenFile(
                    relative_path=relative,
                    absolute_path=absolute,
                    bytes_written=bytes_written,
                    mode=mode,
                )
            )
            logger.page_write(
                path=relative,
                mode=mode,
                bytes_written=bytes_written,
                source_identity=source_identity or None,
            )
        except OSError as err:
            msg = f'failed to write "{block.path}": {err}'
            warnings.append(WriteWarning("failed", msg, relative))
            logger.warning(msg, path=relative)

    return WriteFileBlocksResult(
        written=tuple(written),
        warnings=tuple(warnings),
    )


def format_write_result(result: WriteFileBlocksResult) -> str:
    """
    Human-readable summary for CLI output.
    """
    parts: list[str] = [
        f"written: {len(result.written)}",
        f"warnings: {len(result.warnings)}",
    ]
    for item in result.written:
        parts.append(
            f"- [{item.mode}] {item.relative_path} ({item.bytes_written} bytes)"
        )
    if result.warnings:
        parts.append("")
        parts.append("--- warnings ---")
        for warning in result.warnings:
            parts.append(f"- {warning.message}")
    return "\n".join(parts)
