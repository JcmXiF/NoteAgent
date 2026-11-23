"""Old runs on disk, read back: one file per run, never written here.

A run is its own file rather than a row in a shared list. The engine is gone;
the files stay for the legacy reader, and the id is still checked before it
becomes a path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from noteagent.pipeline.run import PipelineRun, is_run_id, run_from_dict
from noteagent.project.layout import ProjectLayout


class PipelineRunStore:
    """Read-only access to one project's old run files."""

    def __init__(self, layout: ProjectLayout) -> None:
        self._directory = layout.pipeline_runs

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

    def _path(self, run_id: str) -> Path:
        text = (run_id or "").strip()
        if not is_run_id(text):
            raise ValueError(f"not a pipeline run id: {run_id!r}")
        return self._directory / f"{text}.json"



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
