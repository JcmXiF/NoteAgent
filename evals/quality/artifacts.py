"""Copy a finished case's project, session and events for a report."""

from __future__ import annotations

import shutil
from pathlib import Path

from evals.harness import CaseRun


def copy_case_artifacts(
    run: CaseRun, session_id: str | None, target: Path
) -> None:
    """Keep the project and the one session the case drove."""
    if run.layout.root.is_dir():
        shutil.copytree(
            run.layout.root, target / "project", dirs_exist_ok=True
        )
    if session_id is None:
        return
    session_path = run.layout.session_path(session_id)
    if session_path.is_file():
        shutil.copyfile(session_path, target / "session.jsonl")
    events_path = run.layout.session_events_path(session_id)
    if events_path.is_file():
        shutil.copyfile(events_path, target / "events.jsonl")


__all__ = ["copy_case_artifacts"]
