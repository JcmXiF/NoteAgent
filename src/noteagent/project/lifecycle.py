"""Create, initialize, reset, and delete named projects under ``projects/``."""

from __future__ import annotations

from pathlib import Path

from noteagent.project.config import (
    ACTIVE_PROJECT_FILE,
    find_config_file,
    home_dir,
    load_active_project,
    save_active_project,
)
from noteagent.project.folders import forget_folder, linked_projects
from noteagent.project.init import init_project, is_valid_project
from noteagent.project.layout import WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW, default_layout
from noteagent.project.names import ProjectNameError, validate_project_name
from noteagent.project.reset import (
    ResetResult,
    list_generated_wiki_files,
    reset_wiki_content,
)
from noteagent.project.trash import move_to_trash
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger

#: Files the program writes itself. They are pages in the tree but not the
#: reader's work, so a count of what a delete would take leaves them out.
_SYSTEM_PAGES = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})


class ProjectConfirmError(Refusal):
    """Raised when a destructive action is missing a matching confirm name."""


def projects_dir(repo_root: Path | str) -> Path:
    """Where project folders live.

    A configured home is itself the container, so a user sees
    ``%USERPROFILE%\\NoteAgent\\<name>`` rather than an extra level they have to
    remember. A checkout keeps the ``projects/`` folder it has always had.
    """
    root = Path(repo_root).expanduser().resolve()
    home = home_dir()
    if home is not None and root == home:
        return root
    return root / "projects"


def _rel_payload(repo_root: Path, layout_root: Path) -> str:
    """The project path as the interface shows it: relative when it can be."""
    try:
        return layout_root.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return layout_root.as_posix()


def resolve_named_project_dir(name: str, *, repo_root: Path | str) -> Path:
    """Resolve ``projects/{name}`` inside *repo_root*.

    Rejects names that escape the projects directory after resolve, and names a
    linked folder already answers to: a project the app created must not take a
    name the reader gave to a folder somewhere else.
    """
    safe = validate_project_name(name)
    root = Path(repo_root).expanduser().resolve()
    base = projects_dir(root).resolve()
    candidate = (base / safe).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as err:
        raise ProjectNameError(
            "project.name.notAllowed", "project name is not allowed"
        ) from err
    if candidate.parent != base:
        raise ProjectNameError("project.name.notAllowed", "project name is not allowed")
    if safe in linked_projects(root):
        raise ProjectNameError(
            "project.name.linked",
            f"a linked folder is listed under this name: {safe}",
            params={"name": safe},
        )
    return candidate


def named_project_dirs(repo_root: Path | str) -> list[tuple[str, Path]]:
    """Every project the list knows, as name and folder: managed first, then linked.

    The order is the order a listing shows them, and a name appears at most once:
    a linked folder whose name a managed project already took is not offered,
    because two answers to the same name would make every lookup a guess.
    """
    root = Path(repo_root).expanduser().resolve()
    base = projects_dir(root)
    pairs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    if base.is_dir():
        for child in sorted(base.iterdir(), key=lambda p: p.name.casefold()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            seen.add(child.name.casefold())
            pairs.append((child.name, child.resolve()))
    for name, folder in sorted(linked_projects(root).items(), key=lambda item: item[0].casefold()):
        if name.casefold() in seen:
            continue
        pairs.append((name, folder))
    return pairs


def resolve_project_dir(name: str, *, repo_root: Path | str) -> Path:
    """The folder a project name answers to, managed or linked."""
    root = Path(repo_root).expanduser().resolve()
    safe = validate_project_name(name)
    linked = linked_projects(root)
    if safe in linked:
        return linked[safe]
    return resolve_named_project_dir(safe, repo_root=root)


def _require_confirm(name: str, confirm_name: str) -> None:
    """Make a destructive call say the name it is about to destroy."""
    if confirm_name != name:
        raise ProjectConfirmError("project.confirm", "type the project name to confirm")


def _active_name(repo_root: Path) -> str | None:
    """The name of the active project under *repo_root*, if it has one."""
    config_path = find_config_file(repo_root)
    if config_path is None:
        return None
    try:
        active = load_active_project(config_path)
    except (OSError, ValueError):
        return None
    target = active.project_path.resolve()
    for name, folder in named_project_dirs(repo_root):
        if folder == target:
            return name
    return None


def _list_peer_names(repo_root: Path) -> list[str]:
    """Sibling projects, in the order a listing shows them."""
    return [
        name
        for name, folder in named_project_dirs(repo_root)
        if is_valid_project(folder) or (folder / "wiki").is_dir()
    ]


def _retarget_after_delete(repo_root: Path, deleted: str) -> str | None:
    """Point the pointer at another project, or remove it when none is left."""
    remaining = [
        name for name in _list_peer_names(repo_root) if name.casefold() != deleted.casefold()
    ]
    config_dir = repo_root
    if remaining:
        nxt = remaining[0]
        layout = default_layout(resolve_project_dir(nxt, repo_root=repo_root))
        save_active_project(layout.root, config_dir=config_dir)
        return nxt
    pointer = repo_root / ACTIVE_PROJECT_FILE
    pointer.unlink(missing_ok=True)
    return None


def create_named_project(name: str, *, repo_root: Path | str) -> dict[str, object]:
    """Create ``projects/{name}`` with the default skeleton and make it active."""
    safe = validate_project_name(name)
    root = Path(repo_root).expanduser().resolve()
    dest = resolve_named_project_dir(safe, repo_root=root)
    if dest.exists():
        if dest.is_file():
            raise Refusal(
                "project.collidesWithFile",
                f"project name collides with a file: {safe}",
                params={"name": safe},
                status=409,
            )
        if any(dest.iterdir()) or is_valid_project(dest):
            raise Refusal(
                "project.exists",
                f"project already exists: {safe}",
                params={"name": safe},
                status=409,
            )
    dest.parent.mkdir(parents=True, exist_ok=True)
    layout = init_project(dest)
    save_active_project(layout.root, config_dir=root)
    RunLogger.for_layout(layout).append("project.create", name=safe)
    return {
        "name": safe,
        "path": _rel_payload(root, layout.root),
        "active": True,
    }


def init_named_project(name: str, *, repo_root: Path | str) -> dict[str, object]:
    """Fill missing skeleton files for an existing ``projects/{name}`` directory.

    Does not overwrite existing starter pages, and does not create a new folder
    (use ``create_named_project``).
    """
    safe = validate_project_name(name)
    root = Path(repo_root).expanduser().resolve()
    dest = resolve_project_dir(safe, repo_root=root)
    if not dest.exists():
        raise Refusal(
            "project.unknown",
            f"unknown project: {safe}",
            params={"name": safe},
            status=404,
        )
    if dest.is_file():
        raise Refusal("project.notADirectory", f"not a project directory: {safe}")
    layout = init_project(dest, overwrite=False)
    RunLogger.for_layout(layout).append("project.init", name=safe)
    return {
        "name": safe,
        "path": _rel_payload(root, layout.root),
        "valid": is_valid_project(layout.root),
    }


def reset_named_project(
    name: str,
    *,
    repo_root: Path | str,
    confirm_name: str = "",
    dry_run: bool = False,
) -> dict[str, object]:
    """Wipe generated wiki pages; keep raw sources and config.

    Live reset requires ``confirm_name`` to equal *name*. Dry-run does not.
    """
    safe = validate_project_name(name)
    if not dry_run:
        _require_confirm(safe, confirm_name)
    root = Path(repo_root).expanduser().resolve()
    dest = resolve_project_dir(safe, repo_root=root)
    if not dest.is_dir() or not is_valid_project(dest):
        raise Refusal(
            "project.notValid",
            f"not a valid wiki project: {safe}",
            params={"name": safe},
            status=404,
        )
    layout = default_layout(dest)
    # The wiki is kept before it is wiped: a reset throws away every page, and the
    # only copy of what they said is the one about to go. Dry runs write nothing.
    snapshot = None
    generated = list_generated_wiki_files(layout) if layout.wiki.is_dir() else []
    if not dry_run and generated:
        try:
            snapshot = move_to_trash(
                projects_dir(root),
                layout.wiki,
                kind="wiki",
                name=safe,
                project=safe,
                project_path=layout.root,
                removed_by="project.reset",
            )
        except OSError as err:
            raise Refusal(
                "project.snapshotFailed",
                f"could not keep a copy of the wiki before resetting: {err}",
                params={"name": safe},
                status=500,
            ) from err

    result: ResetResult = reset_wiki_content(
        layout,
        clear_cache=True,
        clear_run_log=False,
        dry_run=dry_run,
    )
    if snapshot is not None:
        # The wiki moved into the trash, so the reset itself found nothing to
        # delete. What it removed is still what was there a moment ago.
        result = ResetResult(
            deleted_files=tuple(
                path.relative_to(layout.root).as_posix() for path in generated
            ),
            restored=result.restored,
            cleared_cache=result.cleared_cache,
            cleared_run_log=result.cleared_run_log,
            dry_run=result.dry_run,
        )
    if not dry_run:
        RunLogger.for_layout(layout).append(
            "project.reset",
            name=safe,
            deleted=len(result.deleted_files),
        )
    return {
        "name": safe,
        "dryRun": result.dry_run,
        "deletedFiles": list(result.deleted_files),
        "restored": list(result.restored),
        "clearedCache": result.cleared_cache,
        "clearedRunLog": result.cleared_run_log,
        "trashId": snapshot["id"] if snapshot else None,
    }


def project_contents(name: str, *, repo_root: Path | str) -> dict[str, int]:
    """What deleting a project would take: pages, sources, notes, bytes.

    Counted from the folder rather than read from metadata, because the folder is
    the only record of what is really there: a file put in by hand counts like one
    the program wrote.
    """
    safe = validate_project_name(name)
    dest = resolve_project_dir(safe, repo_root=repo_root)
    if not dest.is_dir():
        raise Refusal(
            "project.unknown",
            f"unknown project: {safe}",
            params={"name": safe},
            status=404,
        )
    layout = default_layout(dest)
    pages = [
        path
        for path in layout.wiki.rglob("*.md")
        if path.is_file()
        and path.relative_to(dest).as_posix() not in _SYSTEM_PAGES
    ]
    sources = [path for path in layout.raw.rglob("*") if path.is_file()]
    notes = [path for path in layout.notes.rglob("*.md") if path.is_file()]
    total = 0
    for path in dest.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return {
        "pages": len(pages),
        "sources": len(sources),
        "notes": len(notes),
        "bytes": total,
    }


def delete_named_project(
    name: str,
    *,
    repo_root: Path | str,
    confirm_name: str,
) -> dict[str, object]:
    """Remove a project from the list.

    A project the app created is moved into the trash, whole: *confirm_name* must
    equal *name*, because typing it is the one moment the reader is asked to look
    at what they are removing. A project that was opened where it stands is only
    forgotten — its folder was never the app's, so removing it from the list must
    not touch the files.

    If this was the active project, the pointer moves to another remaining project
    or is removed.
    """
    safe = validate_project_name(name)
    _require_confirm(safe, confirm_name)
    root = Path(repo_root).expanduser().resolve()
    dest = resolve_project_dir(safe, repo_root=root)
    if not dest.exists():
        raise Refusal(
            "project.unknown",
            f"unknown project: {safe}",
            params={"name": safe},
            status=404,
        )
    if not dest.is_dir():
        raise Refusal("project.notADirectory", f"not a project directory: {safe}")
    was_active = False
    active_before = _active_name(root)
    if active_before is not None and active_before.casefold() == safe.casefold():
        was_active = True

    if linked_projects(root).get(safe) == dest.resolve():
        # Opened in place: the folder stays exactly as it is and only the name
        # goes. There is nothing to undo, which is why there is no trash entry.
        forget_folder(root, safe)
        RunLogger.for_layout(default_layout(dest)).append("project.unlink", name=safe)
        return {
            "deleted": safe,
            "active": _retarget_after_delete(root, safe) if was_active else _active_name(root),
            "trashId": None,
            "unlinked": True,
        }

    # Kept rather than unlinked: typing a name correctly is a decision, but it is
    # not evidence that the wiki, the sources and the notes in it were meant to go
    # for good. The trash is a peer of the projects, so this survives the delete.
    kept = move_to_trash(
        projects_dir(root),
        dest,
        kind="project",
        name=dest.name,
        removed_by="project.delete",
    )
    active = _retarget_after_delete(root, dest.name) if was_active else _active_name(root)
    return {
        "deleted": dest.name,
        "active": active,
        "trashId": kept["id"],
        "unlinked": False,
    }
