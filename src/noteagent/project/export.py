"""Taking a whole project away in one file.

A project is a folder, so "export" is a zip of it: the pages, the originals they
were written from, the remarks between them, and the settings a person chose. It is
built in the system's temporary folder and handed to the request that asked for
it, which is also what deletes it — nothing is kept on disk for a download that
already happened.

Caches are left out. They describe how this build read a file, not what the file
says, and they are rebuilt the first time they are needed.
"""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from noteagent.util.refusal import Refusal

__all__ = ["export_project"]

#: Folders that are bookkeeping or rebuildable, at any depth.
_SKIPPED_DIRS = frozenset({"__pycache__", ".trash"})
_SKIPPED_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def export_project(
    name: str,
    *,
    folder: Path | str,
    now: datetime | None = None,
) -> tuple[Path, str, int]:
    """Zip *folder* and answer with the file, the name to save it as, and the count.

    The archive holds one top-level folder named after the project, so unpacking it
    gives back something that can be opened rather than a heap of loose files.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise Refusal(
            "project.unknown",
            f"unknown project: {name}",
            params={"name": name},
            status=404,
        )
    moment = now or datetime.now()
    stem = _safe_stem(name)
    filename = f"{stem}-{moment.strftime('%Y%m%d-%H%M')}.zip"
    handle = tempfile.NamedTemporaryFile(prefix="noteagent-", suffix=".zip", delete=False)
    handle.close()
    target = Path(handle.name)
    count = 0
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(root)
                if _skipped(relative):
                    continue
                # Named after the project rather than the folder it happens to be
                # in: a linked project's folder can be called anything at all.
                archive.write(path, Path(stem) / relative)
                count += 1
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target, filename, count


def _skipped(relative: Path) -> bool:
    """Whether a path inside a project is bookkeeping rather than content."""
    if relative.name in _SKIPPED_NAMES:
        return True
    parts = relative.parts[:-1]
    if any(part in _SKIPPED_DIRS for part in parts):
        return True
    # The compile cache describes how a source was read; it is rebuilt when needed
    # and can be large.
    return len(parts) >= 2 and parts[0] == ".noteagent" and parts[1] == "cache"


def _safe_stem(name: str) -> str:
    """A file name for the archive: the project's name with nothing path-like."""
    cleaned = "".join(
        "_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in (name or "project")
    ).strip(" .")
    return cleaned[:64] or "project"
