"""Raw sources as the pipeline reads them: UTF-8 text and a stable order."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from noteagent.project.identity import source_identity
from noteagent.project.layout import ProjectLayout, default_layout


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """A raw source file loaded from disk."""

    project_root: Path
    identity: str
    path: Path
    content: str

    @property
    def char_count(self) -> int:
        """Characters in the file, as a reader would count them."""
        return len(self.content)

    @property
    def line_count(self) -> int:
        """Lines in the file; an empty file has none."""
        if not self.content:
            return 0
        return len(self.content.splitlines())


_NATURAL_CHUNK = re.compile(r"(\d+)")


def natural_sort_key(path: Path | str) -> tuple:
    """Sort key so ``2.md`` precedes ``10.md`` (case-insensitive path)."""
    text = Path(path).as_posix().casefold()
    parts = _NATURAL_CHUNK.split(text)
    key: list[int | str] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part)
    return tuple(key)


def read_source(
    path: Path | str,
    *,
    project_root: Path | str,
) -> SourceDocument:
    """Read a single source file as UTF-8 text.

    A relative *path* is taken from the project root, not the process's.
    """
    layout = default_layout(project_root)
    source_path = Path(path).expanduser()
    if not source_path.is_absolute():
        source_path = (layout.root / source_path).resolve()
    else:
        source_path = source_path.resolve()

    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    content = source_path.read_text(encoding="utf-8")
    return SourceDocument(
        project_root=layout.root,
        identity=source_identity(source_path, project_root=layout.root),
        path=source_path,
        content=content,
    )


def list_markdown_sources(
    directory: Path | str,
    *,
    recursive: bool = True,
) -> list[Path]:
    """List ``*.md`` files under a directory, sorted with natural order."""
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    pattern = "**/*.md" if recursive else "*.md"
    return sorted(root.glob(pattern), key=natural_sort_key)


def list_project_sources(
    layout: ProjectLayout,
    *,
    recursive: bool = True,
) -> list[Path]:
    """The project's raw sources; an untouched project has none."""
    if not layout.raw.is_dir():
        return []
    return list_markdown_sources(layout.raw, recursive=recursive)


def format_source_summary(doc: SourceDocument) -> str:
    """A source's identity and size, as a terminal or a log shows it."""
    return (
        f"identity: {doc.identity}\n"
        f"chars: {doc.char_count}\n"
        f"lines: {doc.line_count}"
    )
