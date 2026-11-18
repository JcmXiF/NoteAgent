"""Runs on disk: one file per run, written so a reader never sees half of one.

A run is its own file rather than a row in a shared list, so two operations on
different runs cannot rewrite each other. The id is the file name and is checked
before it becomes a path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from noteagent.io.atomic import write_text_atomic
from noteagent.pipeline.run import (
    ACTIVE_STATUSES,
    INTERRUPTIBLE_STATUSES,
    PipelineRun,
    is_run_id,
    run_from_dict,
    run_to_dict,
)
from noteagent.project.layout import ProjectLayout


class PipelineRunStore:
    """Every run of one project, read from and written to ``.noteagent``."""

    def __init__(self, layout: ProjectLayout) -> None:
        self._directory = layout.pipeline_runs

    def create(self, run: PipelineRun) -> None:
        path = self._path(run.id)
        if path.exists():
            raise FileExistsError(f"a pipeline run with this id already exists: {run.id}")
        self._write(run)

    def save(self, run: PipelineRun) -> None:
        self._write(run)

    def get(self, run_id: str) -> PipelineRun | None:
        try:
            path = self._path(run_id)
        except ValueError:
            return None
        return _read(path)

    def list(self) -> list[PipelineRun]:
        if not self._directory.is_dir():
            return []
        runs: list[PipelineRun] = []
        for path in self._directory.glob("*.json"):
            run = _read(path)
            if run is not None:
                runs.append(run)
        runs.sort(key=lambda item: (item.started_at, item.id), reverse=True)
        return runs

    def active(self) -> list[PipelineRun]:
        return [run for run in self.list() if run.status in ACTIVE_STATUSES]

    def mark_interrupted(self, *, now: str) -> int:
        """Close runs a previous core left open; answers how many it closed.

        A run waiting for its human is not one of them: it was paused, not
        running, and the plan it shows is still the plan that was made.
        """
        changed = 0
        for run in self.list():
            if run.status not in INTERRUPTIBLE_STATUSES:
                continue
            self._write(
                replace(
                    run,
                    status="interrupted",
                    updated_at=now,
                    error="the core restarted before this run finished",
                )
            )
            changed += 1
        return changed

    def _path(self, run_id: str) -> Path:
        text = (run_id or "").strip()
        if not is_run_id(text):
            raise ValueError(f"not a pipeline run id: {run_id!r}")
        return self._directory / f"{text}.json"

    def _write(self, run: PipelineRun) -> None:
        path = self._path(run.id)
        write_text_atomic(
            path,
            json.dumps(run_to_dict(run), ensure_ascii=False, indent=2) + "\n",
        )


def _read(path: Path) -> PipelineRun | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        return run_from_dict(raw)
    except ValueError:
        return None


__all__ = ["PipelineRunStore"]
