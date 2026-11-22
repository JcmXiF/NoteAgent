"""Deterministic seams for the eval cases.

The app reads time and ids from the composition root, so a case can fix both
and compare the same run twice. They are kept here rather than imported from
the test suite: the evals are a separate tool and must not change behaviour
when a test helper does.
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


class FixedClock:
    """A clock that only moves when a case says so."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now


class SequentialIds:
    """Ids a case can name in advance, counted per prefix."""

    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def new_id(self, prefix: str = "") -> str:
        number = self._next.get(prefix, 0) + 1
        self._next[prefix] = number
        return f"{prefix}-{number}" if prefix else str(number)


def remove_tree(path: Path) -> None:
    """Remove a temporary project, retrying a handle that is still closing."""
    for attempt in range(6):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 5:
                raise
            time.sleep(0.1)


__all__ = ["FixedClock", "SequentialIds", "remove_tree"]
