"""Cooperative cancel for long LLM jobs.

Checked between rounds. An in-flight HTTP request is never interrupted.
"""

from __future__ import annotations

import threading


class JobCancelled(RuntimeError):
    """The reader stopped the job; stop before the next LLM round."""

    def __init__(self, message: str = "job cancelled") -> None:
        super().__init__(message)


def check_cancelled(cancel: threading.Event | None) -> None:
    """Raise ``JobCancelled`` when the reader has asked to stop."""
    if cancel is not None and cancel.is_set():
        raise JobCancelled()
