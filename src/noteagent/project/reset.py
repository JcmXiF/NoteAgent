"""Reset generated wiki content for a project (keep raw sources)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from noteagent.project.init import template_root
from noteagent.project.layout import (
    WIKI_INDEX,
    WIKI_LOG,
    ProjectLayout,
)
from noteagent.util.run_log import run_log_path


@dataclass(frozen=True, slots=True)
class ResetResult:
    """What a wiki reset deleted and restored."""

    deleted_files: tuple[str, ...]
    restored: tuple[str, ...]
    cleared_cache: bool
    cleared_run_log: bool
    dry_run: bool


def _rel(layout: ProjectLayout, path: Path) -> str:
    """A path as the report shows it: relative to the project when it can be."""
    try:
        return path.relative_to(layout.root).as_posix()
    except ValueError:
        return path.as_posix()


def list_generated_wiki_files(layout: ProjectLayout) -> list[Path]:
    """All files under ``wiki/`` that reset would remove, before restoring aggregates."""
    wiki = layout.wiki
    if not wiki.is_dir():
        return []
    files = [p for p in wiki.rglob("*") if p.is_file()]
    return sorted(files, key=lambda p: p.as_posix().lower())


def reset_wiki_content(
    layout: ProjectLayout,
    *,
    clear_cache: bool = True,
    clear_run_log: bool = False,
    dry_run: bool = False,
) -> ResetResult:
    """Wipe generated wiki pages and restore the empty index and log.

    Keeps ``raw/``, ``config/`` and ``.noteagent/`` (except optional
    cache / run-log clearing).
    """
    deleted: list[str] = []
    for path in list_generated_wiki_files(layout):
        deleted.append(_rel(layout, path))
        if not dry_run:
            path.unlink(missing_ok=True)

    if not dry_run:
        # Remove now-empty directories under wiki/ (keep wiki root).
        if layout.wiki.is_dir():
            for child in sorted(layout.wiki.rglob("*"), reverse=True):
                if child.is_dir():
                    try:
                        child.rmdir()
                    except OSError:
                        pass

    restored: list[str] = []
    template = template_root()
    copies = [
        (template / WIKI_INDEX, layout.wiki_index),
        (template / WIKI_LOG, layout.wiki_log),
    ]
    for src, dest in copies:
        restored.append(_rel(layout, dest))
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    cleared_cache = False
    cache_path = layout.compile_cache
    if clear_cache and cache_path.is_file():
        cleared_cache = True
        if not dry_run:
            cache_path.unlink(missing_ok=True)

    cleared_run_log = False
    log_path = run_log_path(layout)
    if clear_run_log and log_path.is_file():
        cleared_run_log = True
        if not dry_run:
            log_path.unlink(missing_ok=True)

    return ResetResult(
        deleted_files=tuple(deleted),
        restored=tuple(restored),
        cleared_cache=cleared_cache,
        cleared_run_log=cleared_run_log,
        dry_run=dry_run,
    )


def format_reset_result(result: ResetResult) -> str:
    """A reset summary for a terminal or a log."""
    verb = "would delete" if result.dry_run else "deleted"
    parts = [
        f"dry_run: {result.dry_run}",
        f"{verb}: {len(result.deleted_files)} file(s)",
        f"restored: {', '.join(result.restored)}",
        f"cleared_cache: {result.cleared_cache}",
        f"cleared_run_log: {result.cleared_run_log}",
    ]
    for rel in result.deleted_files[:30]:
        parts.append(f"- [{verb}] {rel}")
    if len(result.deleted_files) > 30:
        parts.append(f"- … {len(result.deleted_files) - 30} more")
    return "\n".join(parts)
