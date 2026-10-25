"""What a raw source is called inside its project, wherever it was read from."""

from __future__ import annotations

from pathlib import Path

from noteagent.project.layout import RAW_DIR, default_layout


def source_identity(
    source_path: Path | str,
    *,
    project_root: Path | str,
) -> str:
    """Project-relative identity for a raw source file.

    Inside the project the identity is the path from the project root, so a
    source is ``raw/web/intro-1a2b3c.html`` and its locators are that path plus
    a block id. A file outside the project keeps the part of its own path from
    ``raw/`` on; one with no such part is known by its file name alone.
    """
    layout = default_layout(project_root)
    absolute = Path(source_path).expanduser().resolve()
    root = layout.root.resolve()

    try:
        return absolute.relative_to(root).as_posix()
    except ValueError:
        pass

    marker = f"/{RAW_DIR}/"
    lowered = absolute.as_posix().lower()
    index = lowered.find(marker)
    if index >= 0:
        return absolute.as_posix()[index + 1 :]

    return absolute.name
