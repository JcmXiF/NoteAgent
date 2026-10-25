"""Persistent gated-compile records.

`.noteagent/ingest-recovery.json` holds two things, both project-side and neither
of them about who is looking: the rounds that were confirmed but did not finish
(`items`, which a human resumes or discards), and the last round that did finish
(`last`, so what it wrote is still readable after the window is gone).

This module never writes wiki, never calls ingest, and never starts an LLM.
Opening a project leaves pending rows pending until a human resumes or discards.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from noteagent.project.layout import ProjectLayout

RECOVERY_VERSION = 1
STATUS_OPEN = "open"

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@dataclass(frozen=True, slots=True)
class RecoveryJob:
    """One interrupted gated compile. Not a source-change notice."""

    id: str
    intent: str
    confirmed: tuple[dict[str, Any], ...]
    write_paths: tuple[str, ...]
    status: str
    error: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class CompletedRound:
    """The last gated compile that finished. A report, not a job."""

    intent: str
    finished_at: str
    written: tuple[dict[str, Any], ...]
    doctor: dict[str, Any] | None
    write_warnings: tuple[dict[str, Any], ...]
    parse_warnings: tuple[str, ...]


def recovery_file_path(layout: ProjectLayout) -> Path:
    return layout.ingest_recovery


def _root_key(layout: ProjectLayout) -> str:
    return str(layout.root.resolve())


def _lock_for(layout: ProjectLayout) -> threading.Lock:
    key = _root_key(layout)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _now_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _confirmed_row(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    slug = str(raw.get("slug") or "").strip()
    action = str(raw.get("action") or "skip").strip() or "skip"
    wiki_path = (
        str(raw.get("wikiPath") or raw.get("wiki_path") or "").replace("\\", "/").strip()
    )
    if not name and not slug:
        return None
    section_raw = raw.get("sectionIds") or raw.get("section_ids") or []
    section_ids: list[str] = []
    if isinstance(section_raw, list):
        for item in section_raw:
            if isinstance(item, str) and item.strip():
                section_ids.append(item.strip())
    return {
        "name": name or slug,
        "slug": slug,
        "wikiPath": wiki_path,
        "action": action,
        "sectionIds": section_ids,
    }


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.replace("\\", "/").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return tuple(out)


def _job_from_row(raw: object) -> RecoveryJob | None:
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or "").strip()
    status = str(raw.get("status") or "").strip()
    if not ident or status != STATUS_OPEN:
        return None
    confirmed_raw = raw.get("confirmed")
    confirmed: list[dict[str, Any]] = []
    if isinstance(confirmed_raw, list):
        for item in confirmed_raw:
            row = _confirmed_row(item)
            if row is not None:
                confirmed.append(row)
    if not any(item.get("action") == "write" for item in confirmed):
        return None
    return RecoveryJob(
        id=ident,
        intent=str(raw.get("intent") or "").strip(),
        confirmed=tuple(confirmed),
        write_paths=_strings(raw.get("writePaths")),
        status=status,
        error=str(raw.get("error") or ""),
        updated_at=str(raw.get("updatedAt") or ""),
    )


def _job_to_row(job: RecoveryJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "intent": job.intent,
        "confirmed": [dict(item) for item in job.confirmed],
        "writePaths": list(job.write_paths),
        "status": job.status,
        "error": job.error,
        "updatedAt": job.updated_at,
    }


def _round_from_row(raw: object) -> CompletedRound | None:
    if not isinstance(raw, dict):
        return None
    written: list[dict[str, Any]] = []
    rows = raw.get("written")
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            written.append({"path": path, "mode": str(item.get("mode") or "")})
    doctor = raw.get("doctor")
    warnings: list[dict[str, Any]] = []
    raw_warnings = raw.get("writeWarnings")
    if isinstance(raw_warnings, list):
        for item in raw_warnings:
            if not isinstance(item, dict):
                continue
            warnings.append(
                {
                    "code": str(item.get("code") or ""),
                    "message": str(item.get("message") or ""),
                    "path": str(item.get("path") or "").replace("\\", "/"),
                }
            )
    return CompletedRound(
        intent=str(raw.get("intent") or "").strip(),
        finished_at=str(raw.get("finishedAt") or "").strip(),
        written=tuple(written),
        doctor=doctor if isinstance(doctor, dict) else None,
        write_warnings=tuple(warnings),
        parse_warnings=_strings(raw.get("parseWarnings")),
    )


def _round_to_row(round_: CompletedRound) -> dict[str, Any]:
    return {
        "intent": round_.intent,
        "finishedAt": round_.finished_at,
        "written": [dict(item) for item in round_.written],
        "doctor": round_.doctor,
        "writeWarnings": [dict(item) for item in round_.write_warnings],
        "parseWarnings": list(round_.parse_warnings),
    }


def _load_file(layout: ProjectLayout) -> tuple[list[RecoveryJob], CompletedRound | None]:
    path = recovery_file_path(layout)
    if not path.is_file():
        return [], None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return [], None
    if not isinstance(payload, dict):
        return [], None
    completed = _round_from_row(payload.get("last"))
    rows = payload.get("items")
    if not isinstance(rows, list):
        job = _job_from_row(payload.get("job"))
        return ([job] if job is not None else []), completed
    jobs: list[RecoveryJob] = []
    seen: set[str] = set()
    for row in rows:
        job = _job_from_row(row)
        if job is None or job.id in seen:
            continue
        seen.add(job.id)
        jobs.append(job)
    return jobs, completed


def _save_file(
    layout: ProjectLayout,
    jobs: list[RecoveryJob],
    completed: CompletedRound | None,
) -> None:
    path = recovery_file_path(layout)
    if not jobs and completed is None:
        path.unlink(missing_ok=True)
        return
    payload: dict[str, Any] = {
        "version": RECOVERY_VERSION,
        "items": [_job_to_row(item) for item in jobs],
    }
    if completed is not None:
        payload["last"] = _round_to_row(completed)
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _payload(
    jobs: list[RecoveryJob],
    completed: CompletedRound | None,
) -> dict[str, Any]:
    open_jobs = [item for item in jobs if item.status == STATUS_OPEN]
    return {
        "openCount": len(open_jobs),
        "items": [_job_to_row(item) for item in open_jobs],
        "last": _round_to_row(completed) if completed is not None else None,
    }


def recovery_payload(layout: ProjectLayout) -> dict[str, Any]:
    with _lock_for(layout):
        jobs, completed = _load_file(layout)
        return _payload(jobs, completed)


def remember_confirmed_job(
    layout: ProjectLayout,
    *,
    intent: str,
    confirmed: list[dict[str, Any]],
    write_paths: list[str] | tuple[str, ...] = (),
) -> str:
    rows: list[dict[str, Any]] = []
    for item in confirmed:
        row = _confirmed_row(item)
        if row is not None:
            rows.append(row)
    if not any(item.get("action") == "write" for item in rows):
        return ""
    job = RecoveryJob(
        id=uuid.uuid4().hex[:12],
        intent=intent.strip(),
        confirmed=tuple(rows),
        write_paths=_strings(list(write_paths)),
        status=STATUS_OPEN,
        error="",
        updated_at=_now_stamp(),
    )
    with _lock_for(layout):
        _save_file(layout, [job], _load_file(layout)[1])
    return job.id


def complete_confirmed_job(
    layout: ProjectLayout,
    job_id: str,
    *,
    intent: str,
    written: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    doctor: dict[str, Any] | None = None,
    write_warnings: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    parse_warnings: list[str] | tuple[str, ...] = (),
) -> CompletedRound:
    """
    A round finished: its job leaves, and what it wrote stays as the last round.

    The two are written together so a reader never sees a project whose job is
    gone and whose round is not there yet.
    """
    rows: list[dict[str, Any]] = []
    for path, mode in written:
        cleaned = str(path).replace("\\", "/").strip()
        if cleaned:
            rows.append({"path": cleaned, "mode": str(mode or "")})
    warnings: list[dict[str, Any]] = []
    for item in write_warnings:
        warnings.append(
            {
                "code": str(item.get("code") or ""),
                "message": str(item.get("message") or ""),
                "path": str(item.get("path") or "").replace("\\", "/"),
            }
        )
    completed = CompletedRound(
        intent=intent.strip(),
        finished_at=_now_stamp(),
        written=tuple(rows),
        doctor=doctor,
        write_warnings=tuple(warnings),
        parse_warnings=tuple(str(item) for item in parse_warnings),
    )
    ident = job_id.strip()
    with _lock_for(layout):
        jobs, _previous = _load_file(layout)
        if ident:
            jobs = [item for item in jobs if item.id != ident]
        _save_file(layout, jobs, completed)
    return completed


def mark_recovery_error(layout: ProjectLayout, job_id: str, error: str) -> None:
    ident = job_id.strip()
    if not ident:
        return
    with _lock_for(layout):
        jobs, completed = _load_file(layout)
        next_jobs: list[RecoveryJob] = []
        for job in jobs:
            if job.id == ident:
                next_jobs.append(
                    replace(job, error=error.strip(), updated_at=_now_stamp())
                )
            else:
                next_jobs.append(job)
        _save_file(layout, next_jobs, completed)


def discard_recovery_job(layout: ProjectLayout, job_id: str) -> dict[str, Any]:
    ident = job_id.strip()
    if not ident:
        raise KeyError("missing recovery id")
    with _lock_for(layout):
        jobs, completed = _load_file(layout)
        if not any(item.id == ident for item in jobs):
            raise KeyError(ident)
        kept = [item for item in jobs if item.id != ident]
        _save_file(layout, kept, completed)
        return _payload(kept, completed)


__all__ = [
    "RECOVERY_VERSION",
    "CompletedRound",
    "RecoveryJob",
    "STATUS_OPEN",
    "complete_confirmed_job",
    "discard_recovery_job",
    "mark_recovery_error",
    "recovery_file_path",
    "recovery_payload",
    "remember_confirmed_job",
]
