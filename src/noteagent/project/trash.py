"""What was removed, kept beside the projects until it is restored or expires.

Three actions destroy work that cannot be rebuilt from anywhere else: deleting a
project, resetting a wiki, and removing a stored source. Each one moves what it
takes into ``.trash`` instead of unlinking it — one folder per removal, holding
``meta.json`` and the bytes themselves, named after the moment it happened.

Restoring puts the bytes back and moves whatever stands in the way into the trash
in turn, so a restore is itself undoable. Thirty days is the promise; it is kept
when the trash is read rather than by a timer, so nothing has to be running for
it to hold.

The trash is a peer of the project folders, never inside one: deleting a project
must not take its own undo with it.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_hex
from typing import Any

from noteagent.io.atomic import write_text_atomic
from noteagent.project.folders import linked_projects
from noteagent.util.refusal import Refusal

__all__ = [
    "TRASH_DIR",
    "RETENTION_DAYS",
    "list_trash",
    "move_to_trash",
    "prune_trash",
    "restore_trash",
    "trash_root",
]

TRASH_DIR = ".trash"
RETENTION_DAYS = 30
_META = "meta.json"
_PAYLOAD = "payload"

#: What a trashed thing was. Kept as text so a project written by a newer build
#: reads as unknown rather than crashing an older one.
KIND_PROJECT = "project"
KIND_WIKI = "wiki"
KIND_FILE = "file"


def trash_root(base: Path) -> Path:
    """Where the removals beside the projects in *base* are kept."""
    return Path(base) / TRASH_DIR


def move_to_trash(
    base: Path,
    source: Path,
    *,
    kind: str,
    name: str,
    project: str = "",
    relative_path: str = "",
    removed_by: str = "",
    project_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move *source* into the trash under *base* and say what was kept.

    *source* is moved rather than copied, so a large project does not double the
    disk it takes to undo one click. Anything that goes wrong before the move is
    complete leaves the original where it was.

    *project_path* is where the project really is. A project is usually a folder
    under *base*, but one that was opened where it stands is somewhere else, and
    its removals have to be able to find their way back there.
    """
    origin = Path(source)
    if not origin.exists():
        raise Refusal("trash.missing", f"nothing to remove: {origin}", status=404)

    moment = now or datetime.now().astimezone()
    item_id = _new_id(moment)
    root = trash_root(base)
    folder = root / item_id
    folder.mkdir(parents=True, exist_ok=False)
    payload = folder / _PAYLOAD
    meta = {
        "id": item_id,
        "kind": kind,
        "name": name,
        "project": project,
        "projectPath": Path(project_path).expanduser().resolve().as_posix()
        if project_path is not None
        else "",
        "relativePath": relative_path.replace("\\", "/"),
        "removedAt": moment.isoformat(timespec="seconds"),
        "removedBy": removed_by,
    }
    write_text_atomic(folder / _META, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    try:
        shutil.move(str(origin), str(payload))
    except OSError:
        # The folder describing a move that did not happen would offer a restore
        # of nothing, so it goes rather than staying as a broken entry.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return _item(folder, meta)


def list_trash(base: Path, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Everything kept under *base*, newest first, after the old is let go."""
    prune_trash(base, now=now)
    root = trash_root(base)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        meta = _read_meta(folder)
        if meta is None:
            continue
        items.append(_item(folder, meta))
    items.sort(key=lambda item: item["removedAt"], reverse=True)
    return items


def restore_trash(
    base: Path,
    item_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Put one kept thing back where it came from.

    Anything already at the destination is moved into the trash first, so
    restoring an older version of a page does not silently overwrite the newer
    one: the newer one becomes what can be restored next.
    """
    root = trash_root(base)
    folder = root / _safe_id(item_id)
    meta = _read_meta(folder)
    if meta is None:
        raise Refusal("trash.unknown", f"nothing in the trash: {item_id}", status=404)
    payload = folder / _PAYLOAD
    if not payload.exists():
        raise Refusal("trash.damaged", f"the trash entry has no content: {item_id}", status=410)

    target = _restore_target(Path(base), meta)
    replaced: str | None = None
    if target.exists():
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
        else:
            kept = move_to_trash(
                base,
                target,
                kind=str(meta.get("kind") or KIND_FILE),
                name=str(meta.get("name") or target.name),
                project=str(meta.get("project") or ""),
                relative_path=str(meta.get("relativePath") or ""),
                removed_by="trash.restore",
                now=now,
            )
            replaced = kept["id"]

    target.parent.mkdir(parents=True, exist_ok=True)
    restored = _item(folder, meta)
    shutil.move(str(payload), str(target))
    shutil.rmtree(folder, ignore_errors=True)
    return {"restored": restored, "replaced": replaced}


def prune_trash(
    base: Path,
    *,
    now: datetime | None = None,
    retention_days: int = RETENTION_DAYS,
) -> list[str]:
    """Let go of what has been kept longer than the promise, and name it.

    An entry whose timestamp cannot be read is kept: "unreadable" is not the same
    as "old", and guessing would delete something a person may still want back.
    """
    root = trash_root(base)
    if not root.is_dir():
        return []
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(days=retention_days)
    removed: list[str] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.name.startswith("."):
            continue
        meta = _read_meta(folder)
        if meta is None:
            continue
        when = _parse_time(meta.get("removedAt"))
        if when is None or when > cutoff:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder.name)
    return removed


def _restore_target(base: Path, meta: dict[str, Any]) -> Path:
    kind = str(meta.get("kind") or "")
    name = str(meta.get("name") or "").strip()
    if not name:
        raise Refusal("trash.damaged", "the trash entry names nothing", status=410)
    if kind == KIND_PROJECT:
        destination = (base / name).resolve()
        return _checked(destination, base)
    if kind not in {KIND_WIKI, KIND_FILE}:
        raise Refusal("trash.unknownKind", f"cannot restore this kind: {kind}", status=400)

    project = str(meta.get("project") or name).strip()
    project_root = _project_root(base, project, meta.get("projectPath"))
    if not project_root.is_dir():
        # Putting something back into a folder that is not a project would leave
        # it where nothing can open it.
        raise Refusal(
            "trash.projectMissing",
            f"the project is gone: {project}",
            params={"name": project},
            status=409,
        )
    if kind == KIND_WIKI:
        return _checked(project_root / "wiki", project_root)
    relative = str(meta.get("relativePath") or "").strip()
    if not relative:
        raise Refusal("trash.damaged", "the trash entry does not name its file", status=410)
    return _checked((project_root / relative).resolve(), project_root)


def _project_root(base: Path, project: str, recorded: object) -> Path:
    """Where a kept removal goes back to: a managed project, or one opened in place.

    The recorded path is only believed when it is a folder the list still knows,
    because a trash entry is a file on disk and could name anywhere at all.
    """
    if isinstance(recorded, str) and recorded.strip():
        candidate = Path(recorded).expanduser().resolve()
        known = {folder.resolve() for folder in linked_projects(_repo_of(base)).values()}
        if candidate in known:
            return candidate
    return (base / project).resolve()


def _checked(destination: Path, root: Path) -> Path:
    """*destination*, when it is inside *root*."""
    try:
        destination.relative_to(root.resolve())
    except ValueError as err:
        raise Refusal(
            "trash.outside", "the trash entry points outside the projects"
        ) from err
    return destination


def _repo_of(base: Path) -> Path:
    """The folder holding the projects folder *base*, where the linked list lives."""
    return Path(base).resolve().parent


def _item(folder: Path, meta: dict[str, Any]) -> dict[str, Any]:
    payload = folder / _PAYLOAD
    return {
        "id": str(meta.get("id") or folder.name),
        "kind": str(meta.get("kind") or ""),
        "name": str(meta.get("name") or ""),
        "project": str(meta.get("project") or ""),
        "relativePath": str(meta.get("relativePath") or ""),
        "removedAt": str(meta.get("removedAt") or ""),
        "removedBy": str(meta.get("removedBy") or ""),
        "fileCount": _file_count(payload),
    }


def _file_count(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file())


def _read_meta(folder: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads((folder / _META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _new_id(moment: datetime) -> str:
    return f"{moment.strftime('%Y%m%d-%H%M%S')}-{token_hex(3)}"


def _safe_id(item_id: str) -> str:
    """A trash entry's name, checked because it becomes part of a path."""
    text = (item_id or "").strip()
    if not text or text.startswith(".") or "/" in text or "\\" in text or ".." in text:
        raise Refusal("trash.unknown", f"nothing in the trash: {item_id}", status=404)
    return text
