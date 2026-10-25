"""Which wiki project the current working directory points at.

The pointer is a small ``.noteagent-project`` file holding a project path, found
by walking up from the working directory. An explicit path or
``NOTEAGENT_PROJECT`` overrides it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from noteagent.io.atomic import write_text_atomic

ACTIVE_PROJECT_FILE = ".noteagent-project"
ENV_PROJECT_ROOT = "NOTEAGENT_PROJECT"
#: Where a packaged install keeps its projects. Its absence means "a checkout",
#: which keeps them under the repository as before.
ENV_HOME = "NOTEAGENT_HOME"


class ProjectNotConfiguredError(RuntimeError):
    """Raised when no active wiki project is configured."""


def home_dir() -> Path | None:
    """The folder a packaged install keeps projects in, or ``None`` in a checkout.

    Read on every call rather than cached: a test changes it for the length of
    one case, and the desktop shell passes it to the process at startup.
    """
    raw = os.environ.get(ENV_HOME, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class ActiveProjectConfig:
    """Pointer from a working directory to a wiki project folder."""

    config_path: Path
    project_path: Path


def find_config_file(start: Path | str | None = None) -> Path | None:
    """The nearest ``.noteagent-project`` at or above *start*, or ``None``."""
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        config = candidate / ACTIVE_PROJECT_FILE
        if config.is_file():
            return config
    return None


def load_active_project(config_path: Path | str) -> ActiveProjectConfig:
    """Read a pointer file and resolve the project path it names."""
    path = Path(config_path).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"Invalid {ACTIVE_PROJECT_FILE}: missing non-empty 'path'")
    project_path = (path.parent / raw).resolve()
    return ActiveProjectConfig(config_path=path, project_path=project_path)


def save_active_project(
    project_path: Path | str,
    *,
    config_dir: Path | str | None = None,
) -> ActiveProjectConfig:
    """Write the pointer in *config_dir*; store a relative path when it can."""
    project = Path(project_path).expanduser().resolve()
    base = Path(config_dir or Path.cwd()).expanduser().resolve()
    try:
        relative = project.relative_to(base)
        stored = relative.as_posix()
    except ValueError:
        stored = project.as_posix()
    config_path = base / ACTIVE_PROJECT_FILE
    write_text_atomic(
        config_path,
        json.dumps({"path": stored}, indent=2, ensure_ascii=False) + "\n",
    )
    return ActiveProjectConfig(config_path=config_path, project_path=project)


def resolve_active_project(
    *,
    explicit: Path | str | None = None,
    start: Path | str | None = None,
) -> ActiveProjectConfig:
    """The project in use: explicit path, then environment, then the pointer."""
    if explicit is not None:
        project = Path(explicit).expanduser().resolve()
        config = find_config_file(start)
        config_path = config or (Path(start or Path.cwd()).resolve() / ACTIVE_PROJECT_FILE)
        return ActiveProjectConfig(config_path=config_path, project_path=project)

    env_path = os.environ.get(ENV_PROJECT_ROOT, "").strip()
    if env_path:
        return ActiveProjectConfig(
            config_path=Path(env_path) / ACTIVE_PROJECT_FILE,
            project_path=Path(env_path).expanduser().resolve(),
        )

    config_path = find_config_file(start)
    if config_path is None:
        raise ProjectNotConfiguredError(
            "No active wiki project. Run "
            "'noteagent project use <path>' or set NOTEAGENT_PROJECT."
        )
    return load_active_project(config_path)
