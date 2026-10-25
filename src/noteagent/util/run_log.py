"""Append-only JSONL run log for development and debugging.

Stored under the wiki project's ``.noteagent/run-log.jsonl``, separate from the
content-facing ``wiki/log.md``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Annotation-only. Importing it at runtime would pull in the whole
    # ``noteagent.project`` package (whose modules import this one back).
    from noteagent.project.layout import ProjectLayout

RUN_LOG_FILENAME = "run-log.jsonl"


def run_log_path(layout: ProjectLayout) -> Path:
    """The file a project's run log is written to."""
    return layout.meta / RUN_LOG_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class RunLogger:
    """Best-effort JSONL logger. Disk failures warn on stderr and never raise."""

    path: Path | None
    enabled: bool = True

    @classmethod
    def for_layout(cls, layout: ProjectLayout, *, enabled: bool = True) -> RunLogger:
        """A logger pointed at *layout*, or a disabled one when turned off."""
        if not enabled:
            return cls.disabled()
        layout.meta.mkdir(parents=True, exist_ok=True)
        return cls(path=run_log_path(layout), enabled=True)

    @classmethod
    def disabled(cls) -> RunLogger:
        """A logger that writes nothing."""
        return cls(path=None, enabled=False)

    @classmethod
    def for_active_project(
        cls,
        *,
        enabled: bool = True,
        start: Path | str | None = None,
    ) -> RunLogger:
        """Best-effort logger for the active wiki project.

        Returns a disabled logger if there is no valid project. Search and
        other callers must not fail just because logging cannot be opened.
        """
        if not enabled:
            return cls.disabled()
        from noteagent.project.config import (
            ProjectNotConfiguredError,
            resolve_active_project,
        )
        from noteagent.project.init import is_valid_project
        from noteagent.project.layout import default_layout

        try:
            config = resolve_active_project(start=start)
            layout = default_layout(config.project_path)
            if not is_valid_project(layout.root):
                return cls.disabled()
            return cls.for_layout(layout)
        except (OSError, ValueError, json.JSONDecodeError, ProjectNotConfiguredError):
            return cls.disabled()

    def append(self, event: str, **fields: Any) -> None:
        """Append one JSON object with ``ts`` and ``event`` keys."""
        if not self.enabled or self.path is None:
            return
        payload: dict[str, Any] = {
            "ts": _utc_now_iso(),
            "event": event,
        }
        for key, value in fields.items():
            if value is not None:
                payload[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as err:
            print(
                f"[run-log] failed to write {self.path}: {err}",
                file=sys.stderr,
            )

    def status(self, message: str, **fields: Any) -> None:
        """Record a step the job reached."""
        self.append("status", message=message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        """Record something the job carried on past."""
        self.append("warning", message=message, **fields)

    def page_write(
        self,
        *,
        path: str,
        mode: str,
        bytes_written: int | None = None,
        **fields: Any,
    ) -> None:
        """Record one page written to the wiki."""
        self.append(
            "page_write",
            path=path,
            mode=mode,
            bytes_written=bytes_written,
            **fields,
        )


def emit_status(on_status: Any, message: str, **kwargs: Any) -> None:
    """Call ``on_status(message, **kwargs)``; fall back if the callback is unary."""
    if on_status is None:
        return
    try:
        on_status(message, **kwargs)
    except TypeError:
        on_status(message)
