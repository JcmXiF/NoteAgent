"""What this running copy of the app can say about itself.

The screen that reads this is where a person looks before writing a report, or
before deciding that the app is the thing that is broken. So every figure here is
read from the process that is answering, not remembered from startup, and a
figure that cannot be read is left out rather than guessed at.

Two things are deliberately not here. There is no telemetry: nothing is sent
anywhere unless somebody presses "check for updates", and the only address that
request goes to is the one this installation was built with. And there is no
"repair" action: the facts are enough to say what to do next, and a button that
changes things from a screen meant for reading is a trap.
"""

from __future__ import annotations

import json
import os
import platform
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from noteagent import __version__
from noteagent.api.projects import list_projects, projects_dir, resolve_repo_root
from noteagent.api.serve_control import read_record
from noteagent.settings.install import install_view
from noteagent.util.refusal import Refusal

#: When the server process began. Read at import, because this module is imported
#: by the app factory, which is the first thing the process does.
PROCESS_STARTED = time.time()

#: Set by the desktop shell, which starts the core and starts it again if it dies.
ENV_SUPERVISED = "NOTEAGENT_SUPERVISED"
#: Where the desktop shell keeps the core's own output and its restart record.
ENV_LOG_DIR = "NOTEAGENT_LOG_DIR"
#: Where a newer version can be looked up, as a JSON document. Unset means this
#: installation has no source to ask, which is a fact worth saying plainly.
ENV_UPDATE_URL = "NOTEAGENT_UPDATE_URL"

LOG_NAME = "core.log"
SUPERVISOR_NAME = "supervisor.json"

#: How much of the log a screen can ask for, and how much of it is read from the
#: end of the file. A crash traceback is at the end; the size of the file is what
#: says whether it is the whole story.
TAIL_BYTES = 64 * 1024
TAIL_LINES = 80
MAX_TAIL_LINES = 500

#: Long enough for a manifest on a slow connection, short enough that the button
#: comes back with an answer either way.
UPDATE_TIMEOUT = 6.0


class UpdateUnreachable(RuntimeError):
    """The update source did not answer, or did not answer as a source."""


class UpdateMalformed(RuntimeError):
    """The update source answered, but not with something that names a version."""


def _env(source: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if source is None else source


def log_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """Where the desktop shell keeps the core's log, or ``None`` in a checkout."""
    raw = str(_env(env).get(ENV_LOG_DIR, "")).strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def _flag(env: Mapping[str, str] | None, name: str) -> bool:
    return str(_env(env).get(name, "")).strip().lower() in {"1", "true", "yes"}


def supervisor_state(logs: Path | None) -> dict[str, Any] | None:
    """The restart record the desktop shell keeps, when there is one to read.

    A damaged record reads as no record: a diagnostics screen that cannot open
    itself because of a half-written file would be the least useful screen in the
    app.
    """
    if logs is None:
        return None
    try:
        payload = json.loads((logs / SUPERVISOR_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    restarts = payload.get("restarts")
    if not isinstance(restarts, int) or restarts < 0:
        restarts = 0
    last_exit: dict[str, Any] | None = None
    raw_exit = payload.get("lastExit")
    if isinstance(raw_exit, dict):
        code = raw_exit.get("code")
        at = raw_exit.get("at")
        if isinstance(code, int) and isinstance(at, (int, float)):
            last_exit = {"code": code, "at": float(at)}
    return {"restarts": restarts, "lastExit": last_exit}


def read_tail(
    path: Path,
    *,
    lines: int = TAIL_LINES,
    max_bytes: int = TAIL_BYTES,
) -> list[str]:
    """The last *lines* lines of *path*, or nothing when it cannot be read.

    Only the end of the file is read: a log that grew for a week should not be put
    on a screen. A block that starts in the middle of a line is dropped, because
    half a sentence about the crash is worse than starting at the next one.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read()
    except OSError:
        return []
    rows = data.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and rows:
        rows = rows[1:]
    return rows[-lines:]


def _address() -> dict[str, Any] | None:
    """The address this process is answering on, as it recorded for itself."""
    record = read_record(os.getpid())
    if record is None:
        return None
    return {
        "host": record.host,
        "port": record.port,
        "requestedPort": record.requested_port,
        "fallback": record.port != record.requested_port,
        "url": record.url,
    }


def diagnostics_payload(
    *,
    now: float | None = None,
    started: float | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Everything this process can say about itself, in one answer."""
    moment = time.time() if now is None else now
    began = PROCESS_STARTED if started is None else started
    source = _env(env)

    install = install_view()
    projects = list_projects()
    logs = log_dir(source)
    state = supervisor_state(logs)
    root = resolve_repo_root()

    log_info: dict[str, Any] | None = None
    core_log: Path | None = None
    if logs is not None:
        core_log = logs / LOG_NAME
        try:
            size = core_log.stat().st_size
        except OSError:
            size = 0
        log_info = {"path": str(core_log), "exists": core_log.is_file(), "bytes": size}

    warnings: list[str] = []
    if not projects:
        warnings.append("noProject")
    if not install["llm"]["configured"]:
        warnings.append("noModel")
    if logs is not None and not (core_log is not None and core_log.is_file()):
        warnings.append("noLog")

    return {
        "version": __version__,
        "pid": os.getpid(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "startedAt": round(began, 3),
        "uptimeSeconds": round(max(0.0, moment - began), 1),
        "address": _address(),
        "dataRoot": str(root),
        "projectsRoot": str(projects_dir(root)),
        "projectCount": len(projects),
        "activeProject": next(
            (str(item["name"]) for item in projects if item.get("active")),
            None,
        ),
        "logs": log_info,
        "supervised": _flag(source, ENV_SUPERVISED),
        "restarts": state["restarts"] if state else 0,
        "lastExit": state["lastExit"] if state else None,
        "warnings": warnings,
    }


def log_payload(
    *,
    lines: int = TAIL_LINES,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The end of the core's log, for a screen that is already asking about it."""
    logs = log_dir(env)
    if logs is None:
        return {"path": None, "lines": []}
    path = logs / LOG_NAME
    count = max(1, min(int(lines), MAX_TAIL_LINES))
    return {"path": str(path), "lines": read_tail(path, lines=count)}


def update_manifest_url(env: Mapping[str, str] | None = None) -> str:
    return str(_env(env).get(ENV_UPDATE_URL, "")).strip()


def version_key(value: str) -> tuple[int, ...]:
    """A comparable form of a version, or ``ValueError`` when it is not one.

    Only numbers are compared. A pre-release suffix is dropped rather than ranked:
    this is a question of "is there something newer to install", and a build label
    is not an answer to that.
    """
    text = str(value).strip().lower().lstrip("v")
    for separator in ("+", "-"):
        text = text.split(separator, 1)[0]
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"not a version: {value!r}")
    return tuple(int(part) for part in parts)


def _newer(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    width = max(len(latest), len(current))
    padded_latest = latest + (0,) * (width - len(latest))
    padded_current = current + (0,) * (width - len(current))
    return padded_latest > padded_current


def _http_url(value: object) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


def _fetch_manifest(url: str) -> dict[str, Any]:
    """Read the update document, keeping what went wrong to itself."""
    try:
        answer = httpx.get(
            url,
            timeout=UPDATE_TIMEOUT,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": f"noteagent/{__version__}",
            },
        )
    except httpx.HTTPError as err:
        raise UpdateUnreachable(str(err)) from err
    if answer.status_code >= 400:
        raise UpdateUnreachable(f"update source answered {answer.status_code}")
    try:
        payload = answer.json()
    except ValueError as err:
        raise UpdateMalformed("update source did not answer with a document") from err
    if not isinstance(payload, dict):
        raise UpdateMalformed("update source did not answer with a document")
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise UpdateMalformed("update source does not name a version")
    return payload


def check_update(
    *,
    current: str = __version__,
    manifest_url: str | None = None,
    env: Mapping[str, str] | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ask the configured source whether there is a newer version.

    A source that was never configured is not a failure: it is an installation
    that has no answer to this question, and saying so is more useful than a
    request to nowhere.
    """
    url = update_manifest_url(env) if manifest_url is None else manifest_url.strip()
    if not url:
        return {"configured": False, "current": current}
    if not url.lower().startswith(("http://", "https://")):
        raise Refusal(
            "update.badSource",
            f"update source is not an http(s) address: {url}",
            status=409,
        )

    reader = _fetch_manifest if fetch is None else fetch
    try:
        manifest = reader(url)
        latest = str(manifest.get("version", "")).strip()
        key_latest = version_key(latest)
        key_current = version_key(current)
    except UpdateUnreachable as err:
        raise Refusal("update.unreachable", str(err), status=502) from err
    except (UpdateMalformed, AttributeError, TypeError, ValueError) as err:
        raise Refusal("update.badManifest", str(err), status=502) from err

    notes = manifest.get("notes")
    return {
        "configured": True,
        "current": current,
        "latest": latest,
        "available": _newer(key_latest, key_current),
        "notes": str(notes).strip() if isinstance(notes, str) else "",
        "url": _http_url(manifest.get("url")),
    }


__all__ = [
    "ENV_LOG_DIR",
    "ENV_SUPERVISED",
    "ENV_UPDATE_URL",
    "LOG_NAME",
    "MAX_TAIL_LINES",
    "PROCESS_STARTED",
    "SUPERVISOR_NAME",
    "check_update",
    "diagnostics_payload",
    "log_dir",
    "log_payload",
    "read_tail",
    "supervisor_state",
    "update_manifest_url",
    "version_key",
]
