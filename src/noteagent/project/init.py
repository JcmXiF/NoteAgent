"""Create a wiki project skeleton from the packaged default templates."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from noteagent.project.layout import (
    WIKI_INDEX,
    WIKI_LOG,
    ProjectLayout,
    default_layout,
)

TEMPLATE_PACKAGE = "noteagent.templates.default"


def template_root() -> Path:
    """Where the default project templates live in the installed package."""
    return Path(resources.files(TEMPLATE_PACKAGE))


def is_valid_project(project_root: Path | str) -> bool:
    """Whether *project_root* has the one folder every project has."""
    layout = default_layout(project_root)
    return layout.raw.is_dir()


def init_project(
    project_path: Path | str,
    *,
    overwrite: bool = False,
) -> ProjectLayout:
    """Create a wiki project skeleton at *project_path*."""
    layout = default_layout(project_path)
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.raw.mkdir(parents=True, exist_ok=True)
    layout.meta.mkdir(parents=True, exist_ok=True)
    layout.wiki.mkdir(parents=True, exist_ok=True)

    template = template_root()
    copies = [
        (template / WIKI_INDEX, layout.wiki_index),
        (template / WIKI_LOG, layout.wiki_log),
    ]
    for src, dest in copies:
        if dest.exists() and not overwrite:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    return layout
