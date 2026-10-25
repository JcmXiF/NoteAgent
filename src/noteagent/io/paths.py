"""Path housekeeping shared by the writers that keep a folder per thing.

A page, an original, and a note each live in a folder that only exists to hold
them; once the last file in one goes, the folder is left with nothing to say and
would still show up in a listing. So the writers that remove a file tidy up
after it, by the same rule: walk up while the folders are empty, and stop at the
root that is meant to stay.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["prune_empty_parents"]


def prune_empty_parents(start: Path, stop: Path) -> None:
    """Remove now-empty folders from *start* upwards, never *stop* itself."""
    stop_resolved = stop.resolve()
    current = start
    while True:
        try:
            current.resolve().relative_to(stop_resolved)
        except ValueError:
            return
        if current.resolve() == stop_resolved:
            return
        if not current.is_dir():
            return
        try:
            next(current.iterdir())
        except StopIteration:
            current.rmdir()
            current = current.parent
            continue
        return
