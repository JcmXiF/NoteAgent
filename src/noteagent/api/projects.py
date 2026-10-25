"""Resolve and list local wiki projects under the repo."""

from __future__ import annotations

import json
from pathlib import Path

from noteagent.project.config import (
    ProjectNotConfiguredError,
    find_config_file,
    home_dir,
    load_active_project,
    resolve_active_project,
    save_active_project,
)
from noteagent.project.export import export_project
from noteagent.project.folders import linked_projects
from noteagent.project.init import is_valid_project
from noteagent.project.layout import ProjectLayout, default_layout
from noteagent.project.lifecycle import (
    create_named_project,
    delete_named_project,
    init_named_project,
    named_project_dirs,
    projects_dir,
    reset_named_project,
    resolve_project_dir,
)
from noteagent.project.opening import inspect_folder as inspect_existing_folder
from noteagent.project.opening import open_folder as open_existing
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger

# Where a checkout keeps its projects. Overridden by NOTEAGENT_HOME in a package.
REPO_ROOT = Path(__file__).resolve().parents[3]


def resolve_repo_root(start: Path | None = None) -> Path:
    """Where projects, and the pointer to the active one, are kept.

    ``start`` is how a caller (and every test) names a folder explicitly. Without
    it a packaged install names its own home through ``NOTEAGENT_HOME``, and a
    checkout falls back to the repository — never to the working directory, which
    a packaged shell controls and which would make the answer depend on how the
    app was launched.
    """
    if start is not None:
        return Path(start).expanduser().resolve()
    home = home_dir()
    if home is not None:
        return home
    return Path(REPO_ROOT).expanduser().resolve()


def data_home(repo_root: Path | None = None) -> Path:
    """The folder that holds the project folders and the active-project pointer."""
    return projects_dir(resolve_repo_root(repo_root))


def get_active_layout(repo_root: Path | None = None) -> ProjectLayout:
    """Layout for the pointer in ``.noteagent-project`` / ``NOTEAGENT_PROJECT``."""
    root = resolve_repo_root(repo_root)
    config = resolve_active_project(start=root)
    layout = default_layout(config.project_path)
    if not is_valid_project(layout.root):
        raise ProjectNotConfiguredError(
            f"Not a valid wiki project (missing raw/): {layout.root}"
        )
    return layout


def get_project_layout(name: str, *, repo_root: Path | None = None) -> ProjectLayout:
    root = resolve_repo_root(repo_root)
    candidate = resolve_project_dir(name, repo_root=root)
    if not candidate.is_dir():
        raise Refusal(
            "project.unknown", f"unknown project: {name}", params={"name": name}, status=404
        )
    return default_layout(candidate)


def require_project_layout(name: str, *, repo_root: Path | None = None) -> ProjectLayout:
    """Layout for writes / ingest; must be a real wiki project under ``projects/``."""
    layout = get_project_layout(name, repo_root=repo_root)
    if not is_valid_project(layout.root):
        raise Refusal(
            "project.notValid",
            f"not a valid wiki project: {name}",
            params={"name": name},
            status=404,
        )
    return layout


def logger_for_project(name: str, *, repo_root: Path | None = None) -> RunLogger:
    """Run-log for one project, or a disabled log when it cannot be opened."""
    try:
        return RunLogger.for_layout(require_project_layout(name, repo_root=repo_root))
    except (FileNotFoundError, Refusal):
        return RunLogger.disabled()


def use_project(name: str, *, repo_root: Path | None = None) -> dict[str, object]:
    """Point ``.noteagent-project`` at ``projects/{name}`` (same as ``noteagent project use``)."""
    root = resolve_repo_root(repo_root)
    layout = require_project_layout(name, repo_root=root)
    save_active_project(layout.root, config_dir=root)
    RunLogger.for_layout(layout).append("project.use", name=name)
    try:
        rel = layout.root.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = layout.root.as_posix()
    return {"name": name, "path": rel, "active": True}


def create_project(name: str, *, repo_root: Path | None = None) -> dict[str, object]:
    return create_named_project(name, repo_root=resolve_repo_root(repo_root))


def init_project_named(name: str, *, repo_root: Path | None = None) -> dict[str, object]:
    return init_named_project(name, repo_root=resolve_repo_root(repo_root))


def reset_project(
    name: str,
    *,
    confirm_name: str = "",
    dry_run: bool = False,
    repo_root: Path | None = None,
) -> dict[str, object]:
    return reset_named_project(
        name,
        repo_root=resolve_repo_root(repo_root),
        confirm_name=confirm_name,
        dry_run=dry_run,
    )


def delete_project(
    name: str,
    *,
    confirm_name: str,
    repo_root: Path | None = None,
) -> dict[str, object]:
    return delete_named_project(
        name,
        repo_root=resolve_repo_root(repo_root),
        confirm_name=confirm_name,
    )


def list_projects(repo_root: Path | None = None) -> list[dict[str, object]]:
    root = resolve_repo_root(repo_root)
    base = projects_dir(root)
    active_path: Path | None = None
    config_path = find_config_file(root)
    if config_path is not None:
        try:
            active_path = load_active_project(config_path).project_path.resolve()
        except (OSError, ValueError, json.JSONDecodeError):
            active_path = None

    linked = linked_projects(root)
    items: list[dict[str, object]] = []
    for name, folder in named_project_dirs(root):
        if not is_valid_project(folder) and not (folder / "wiki").is_dir():
            continue
        items.append(
            {
                "name": name,
                "path": _relative_to(folder, root),
                "active": active_path is not None and folder.resolve() == active_path,
                # Where it came from, so the interface can say that removing it
                # from the list does not remove the folder.
                "linked": linked.get(name) == folder.resolve(),
            }
        )
    return items


def inspect_folder(path: str, *, repo_root: Path | None = None) -> dict[str, object]:
    """What is in a folder the reader is about to open."""
    return inspect_existing_folder(path, repo_root=resolve_repo_root(repo_root))


def open_existing_folder(
    path: str,
    *,
    name: str = "",
    mode: str = "link",
    repo_root: Path | None = None,
) -> dict[str, object]:
    """Open a folder in place, or copy it in as a new managed project."""
    return open_existing(
        path,
        repo_root=resolve_repo_root(repo_root),
        name=name,
        mode=mode,
    )


def export_project_archive(
    name: str,
    *,
    repo_root: Path | None = None,
) -> tuple[Path, str, int]:
    """Build a zip of a project and answer with the file, its name, and the count."""
    layout = require_project_layout(name, repo_root=resolve_repo_root(repo_root))
    return export_project(name, folder=layout.root)


def _relative_to(folder: Path, root: Path) -> str:
    """A folder as the interface shows it: relative when it can be, absolute when not."""
    try:
        return folder.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return folder.resolve().as_posix()
