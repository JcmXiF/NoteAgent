"""Opening a folder of pages that already exists somewhere else.

Two things are being answered here. The first is "is this folder something this
program can use" — how many pages are in it, what else is in there, and whether it
is already a project rather than a plain folder of Markdown. The second is the
opening itself, in whichever of the two honest ways fits:

* **In place**, when the folder is already a project. Nothing is copied: the list
  remembers where it is and the app reads it there.
* **Copied in**, when it is a plain folder of Markdown. A managed project is made
  and the pages are copied into it, so the app never writes into a folder it does
  not own.

A plain folder is never rearranged in place. Moving someone's pages into a
``wiki/`` it did not have is a change to their folder that no amount of undo makes
obvious, and a copy can be thrown away.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.project.config import save_active_project
from noteagent.project.folders import (
    free_name,
    linked_projects,
    name_for_path,
    remember_folder,
    suggest_name,
)
from noteagent.project.init import init_project, is_valid_project
from noteagent.project.layout import ProjectLayout, default_layout
from noteagent.project.lifecycle import projects_dir
from noteagent.util.refusal import Refusal

__all__ = ["inspect_folder", "open_folder"]

#: Names the program maintains at the top of a wiki. A page the reader brings in
#: that happens to be called one of these cannot keep its name, so it is left out
#: and counted rather than allowed to replace the index.
_RESERVED_PAGES = frozenset({"index.md", "log.md", "overview.md"})

#: Folders that hold bookkeeping rather than documents, wherever they appear.
_IGNORED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".obsidian", ".trash"})

#: How many entries an inspection will walk before it stops counting exactly.
#: A number that is approximately right is more use than a dialog that hangs.
_MAX_WALK = 20_000


#: What "this folder already is a project" means for the one decision that depends
#: on it. Meeting both folders is also what a linked folder is offered on: linking
#: writes into the folder, so it has to be one the program could have made. A
#: folder that merely happens to hold a ``wiki`` folder may belong to something
#: else, and it is copied instead — the copy is thrown away, the folder is not.
def _is_project_folder(target: Path) -> bool:
    layout = default_layout(target)
    return layout.raw.is_dir() and layout.wiki.is_dir()


def inspect_folder(folder: Path | str, *, repo_root: Path | str) -> dict[str, object]:
    """What is in *folder*, for the dialog that is about to open it."""
    target = _existing_folder(folder)
    markdown, others, total, truncated = _walk(target)
    managed = _is_managed(target, repo_root)
    linked_name = name_for_path(repo_root, target)
    # A folder that already has a project's shape is read where it is; anything
    # else is pages to be copied in.
    can_link = _is_project_folder(target) and not managed
    return {
        "path": target.as_posix(),
        "name": linked_name or suggest_name(target),
        "canLink": can_link,
        "alreadyLinked": linked_name is not None,
        "alreadyManaged": managed,
        "markdownCount": markdown,
        "otherCount": others,
        "bytes": total,
        "truncated": truncated,
    }


def open_folder(
    folder: Path | str,
    *,
    repo_root: Path | str,
    name: str = "",
    mode: str = "link",
) -> dict[str, object]:
    """Open *folder* in place (``link``) or as a copy in the managed folder."""
    if mode not in {"link", "copy"}:
        raise Refusal(
            "project.folder.badMode",
            f"unknown way of opening a folder: {mode}",
            params={"mode": mode},
            status=400,
        )
    target = _existing_folder(folder)
    root = Path(repo_root).expanduser().resolve()
    managed = projects_dir(root)
    if _is_managed(target, root):
        raise Refusal(
            "project.folder.alreadyManaged",
            f"already a managed project: {target}",
            params={"path": target.as_posix()},
            status=409,
        )
    if mode == "link":
        return _link(target, repo_root=root, name=name)
    return _copy(target, repo_root=root, managed=managed, name=name)


def _link(target: Path, *, repo_root: Path, name: str) -> dict[str, object]:
    """Remember *target* where it is, and make it complete enough to write to."""
    existing = name_for_path(repo_root, target)
    if existing is not None:
        # Opening the same folder twice is the same request, not a second entry.
        layout = default_layout(target)
        save_active_project(layout.root, config_dir=repo_root)
        return {
            "name": existing,
            "path": target.as_posix(),
            "mode": "link",
            "imported": 0,
            "attachments": 0,
            "skipped": [],
            "alreadyLinked": True,
        }

    valid = is_valid_project(target)
    if not valid and not (target / "wiki").is_dir():
        raise Refusal(
            "project.folder.notProject",
            f"not a project and has no wiki/: {target}",
            params={"path": target.as_posix()},
            status=400,
        )
    chosen = name.strip() or suggest_name(target)
    if not chosen:
        raise Refusal(
            "project.folder.needsName",
            "the folder needs a name to be listed under",
            params={"path": target.as_posix()},
            status=400,
        )
    wanted = free_name(chosen, taken=_taken_names(repo_root))
    # A folder with pages but no ``raw/`` is completed rather than refused: the
    # program only adds what it needs and copies neither the pages nor anything else.
    layout = init_project(target, overwrite=False)
    if not valid:
        rebuild_wiki_index(layout)
    remember_folder(repo_root, wanted, target)
    save_active_project(layout.root, config_dir=repo_root)
    return {
        "name": wanted,
        "path": target.as_posix(),
        "mode": "link",
        "imported": 0,
        "attachments": 0,
        "skipped": [],
        "alreadyLinked": False,
    }


def _copy(target: Path, *, repo_root: Path, managed: Path, name: str) -> dict[str, object]:
    """Copy *target* into a managed project, preserving what it holds."""
    chosen = name.strip() or suggest_name(target) or "imported"
    wanted = free_name(chosen, taken=_taken_names(repo_root))
    dest = managed / wanted
    if dest.exists():
        raise Refusal(
            "project.exists",
            f"project already exists: {wanted}",
            params={"name": wanted},
            status=409,
        )
    managed.mkdir(parents=True, exist_ok=True)

    imported = 0
    attachments = 0
    skipped: list[str] = []
    if is_valid_project(target) or (target / "wiki").is_dir():
        # A complete project is copied whole: reshaping one would lose what its
        # own paths and links depend on.
        shutil.copytree(target, dest, ignore=_ignore_bookkeeping)
        layout = init_project(dest, overwrite=False)
        rebuild_wiki_index(layout)
        for path in (dest / "wiki").rglob("*"):
            if path.is_file() and path.suffix.lower() == ".md":
                imported += 1
    else:
        layout = init_project(dest)
        imported, attachments, skipped = _copy_pages(target, layout)
        rebuild_wiki_index(layout)

    save_active_project(layout.root, config_dir=repo_root)
    return {
        "name": wanted,
        "path": dest.as_posix(),
        "mode": "copy",
        "imported": imported,
        "attachments": attachments,
        "skipped": skipped,
        "alreadyLinked": False,
    }


def _copy_pages(source: Path, layout: ProjectLayout) -> tuple[int, int, list[str]]:
    """Copy a plain folder of Markdown into a project's own two folders.

    Pages keep their relative place under ``wiki/``, so a folder of folders stays
    a folder of folders. Everything that is not a page is an attachment the pages
    may refer to, and goes under ``raw/imported/`` with the same shape.
    """
    imported = 0
    attachments = 0
    skipped: list[str] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if any(part in _IGNORED_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        if path.suffix.lower() == ".md":
            if len(rel.parts) == 1 and rel.name.lower() in _RESERVED_PAGES:
                skipped.append(rel.name)
                continue
            destination = layout.wiki / rel
            imported += 1
        else:
            destination = layout.raw / "imported" / rel
            attachments += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    return imported, attachments, sorted(skipped)


def _existing_folder(folder: Path | str) -> Path:
    """The folder named, checked, and resolved; refused when it is not one."""
    raw = str(folder or "").strip()
    if not raw:
        raise Refusal("project.folder.empty", "no folder named", status=400)
    target = Path(raw).expanduser()
    if not target.exists():
        raise Refusal(
            "project.folder.missing",
            f"no such folder: {target}",
            params={"path": target.as_posix()},
            status=404,
        )
    if not target.is_dir():
        raise Refusal(
            "project.folder.notAFolder",
            f"not a folder: {target}",
            params={"path": target.as_posix()},
            status=400,
        )
    return target.resolve()


def _is_managed(target: Path, repo_root: Path | str) -> bool:
    """Whether *target* is already one of the folders under ``projects/``."""
    base = projects_dir(repo_root)
    try:
        target.relative_to(base)
    except ValueError:
        return False
    return target != base


def _taken_names(repo_root: Path) -> list[str]:
    """Every name the list already answers to, managed and linked alike."""
    base = projects_dir(repo_root)
    managed = [child.name for child in base.iterdir() if child.is_dir()] if base.is_dir() else []
    return managed + list(linked_projects(repo_root))


def _walk(target: Path) -> tuple[int, int, int, bool]:
    """Count pages, other files and bytes, stopping at a reasoned limit."""
    markdown = 0
    others = 0
    total = 0
    seen = 0
    for path in target.rglob("*"):
        if not path.is_file():
            continue
        seen += 1
        if seen > _MAX_WALK:
            return markdown, others, total, True
        if any(part in _IGNORED_DIRS for part in path.relative_to(target).parts[:-1]):
            continue
        if path.suffix.lower() == ".md":
            markdown += 1
        else:
            others += 1
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return markdown, others, total, False


def _ignore_bookkeeping(directory: str, names: list[str]) -> set[str]:
    """What a project copy leaves behind: caches and editor folders."""
    base = Path(directory).name
    dropped = {name for name in names if name in _IGNORED_DIRS or name == ".DS_Store"}
    if base == ".noteagent" and "cache" in names:
        dropped.add("cache")
    return dropped
