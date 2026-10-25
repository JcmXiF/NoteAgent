"""Projects that are kept where they already are.

A project has always been a folder under ``projects/``. That is the right place for
one the app creates, but it is not the only place a wiki may already live: someone
with a folder of pages should be able to point the app at it rather than copy it
in. Those folders are named in a small file beside the projects, mapping the name
the interface uses to the folder it really is, so every lookup can ask here after
it has looked in ``projects/``.

The file is a convenience, not the project: deleting it loses the list and nothing
else. An entry whose folder has gone is dropped when the list is read — a name
that resolves to nothing would keep being offered and could not be opened.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from noteagent.io.atomic import write_text_atomic
from noteagent.project.names import ProjectNameError, validate_project_name
from noteagent.util.refusal import Refusal

__all__ = [
    "FOLDERS_FILE",
    "folders_path",
    "forget_folder",
    "free_name",
    "linked_projects",
    "name_for_path",
    "remember_folder",
    "suggest_name",
]

#: Where the list lives. A dot file beside the projects, so it is neither a
#: project nor something a listing would offer.
FOLDERS_FILE = ".noteagent-folders.json"

#: How many names a collision is worth trying before giving up. Three digits is
#: more than a person will ever make by opening the same folder twice.
_MAX_SUFFIX = 100


def folders_path(repo_root: Path | str) -> Path:
    """The file naming the projects kept outside ``projects/``."""
    return Path(repo_root).expanduser().resolve() / FOLDERS_FILE


def linked_projects(repo_root: Path | str) -> dict[str, Path]:
    """Name → folder for every linked project whose folder is still there.

    Read afresh each time rather than cached: a folder can be moved or deleted
    while the app runs, and the answer has to follow. Entries that no longer
    resolve are dropped, and the file is rewritten only when something changed.
    """
    path = folders_path(repo_root)
    stored = _read(path)
    kept: dict[str, Path] = {}
    changed = False
    for raw_name, raw_path in stored.items():
        try:
            name = validate_project_name(raw_name)
        except ProjectNameError:
            changed = True
            continue
        if not isinstance(raw_path, str) or not raw_path.strip():
            changed = True
            continue
        folder = Path(raw_path).expanduser()
        if not folder.is_dir():
            changed = True
            continue
        kept[name] = folder.resolve()
    if changed:
        _write(path, kept)
    return kept


def remember_folder(repo_root: Path | str, name: str, folder: Path | str) -> dict[str, object]:
    """Name *folder* as *name*, replacing any earlier entry under that name."""
    safe = validate_project_name(name)
    target = Path(folder).expanduser().resolve()
    if not target.is_dir():
        raise Refusal(
            "project.folder.missing",
            f"not a folder: {target}",
            params={"path": str(target)},
            status=404,
        )
    path = folders_path(repo_root)
    entries = linked_projects(repo_root)
    entries[safe] = target
    _write(path, entries)
    return {"name": safe, "path": target.as_posix()}


def forget_folder(repo_root: Path | str, name: str) -> bool:
    """Stop naming a folder, and say whether there was anything to forget."""
    entries = linked_projects(repo_root)
    removed = entries.pop(name, None)
    if removed is not None:
        _write(folders_path(repo_root), entries)
    return removed is not None


def name_for_path(repo_root: Path | str, folder: Path | str) -> str | None:
    """The name a folder is listed under, when it is linked."""
    target = Path(folder).expanduser().resolve()
    for name, known in linked_projects(repo_root).items():
        if known == target:
            return name
    return None


def suggest_name(folder: Path | str) -> str:
    """What to call a folder in the list: its own name, when that can be a name.

    Empty when it cannot — a folder called ``.notes`` or ``CON`` still has to be
    openable, so the interface asks for a name instead of refusing the folder.
    """
    try:
        return validate_project_name(Path(folder).name)
    except ProjectNameError:
        return ""


def free_name(desired: str, *, taken: Iterable[str] = ()) -> str:
    """A name like *desired* that nothing in *taken* already uses.

    Case is ignored, because the folder it names is looked up case-insensitively
    on the systems this runs on. A suffix is added inside the length the name is
    allowed, rather than after it.
    """
    base = validate_project_name(desired)
    used = {name.casefold() for name in taken}
    if base.casefold() not in used:
        return base
    for index in range(2, _MAX_SUFFIX):
        suffix = f"-{index}"
        candidate = f"{base[: 64 - len(suffix)]}{suffix}"
        if candidate.casefold() not in used:
            return candidate
    raise Refusal(
        "project.name.taken",
        f"no free name like {base}",
        params={"name": base},
        status=409,
    )


def _read(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    folders = raw.get("folders")
    return dict(folders) if isinstance(folders, dict) else {}


def _write(path: Path, entries: dict[str, Path]) -> None:
    body = {"folders": {name: folder.as_posix() for name, folder in sorted(entries.items())}}
    write_text_atomic(path, json.dumps(body, indent=2, ensure_ascii=False) + "\n")
