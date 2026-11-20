"""Removing a temporary tree after the process that wrote it lets go.

Windows refuses to delete a file or folder while any handle still has it open.
A background thread can outlive the test that started it by an instant, and the
run file, the task lock or an event log is still being written when cleanup
begins. Temporary cleanup waits out that window a few times before failing.

Only the transient sharing errors are retried. A missing path is already
removed; a wrong path, a decode failure or any non-OS error is raised from the
first attempt, because waiting cannot fix it.
"""

from __future__ import annotations

import errno
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["remove_tree", "temporary_tree"]

#: A few short retries cover a closing handle without hiding a tree that stays
#: locked: six tries with a 100 ms pause wait 0.5 s at most.
_ATTEMPTS = 6
_RETRY_S = 0.1


def _transient_cleanup_error(err: OSError) -> bool:
    """Whether the delete can succeed once the last handle closes.

    Windows reports an open file as ``PermissionError`` / WinError 32 (a
    sharing violation); POSIX uses ``EACCES`` or ``EBUSY``. Everything else is
    a real failure and is not retried.
    """
    if isinstance(err, PermissionError):
        return True
    if err.errno in (errno.EACCES, errno.EBUSY):
        return True
    return getattr(err, "winerror", None) == 32


def remove_tree(
    path: str | Path,
    *,
    attempts: int = _ATTEMPTS,
    delay: float = _RETRY_S,
    sleep: Callable[[float], None] = time.sleep,
    remover: Callable[[str | Path], None] = shutil.rmtree,
) -> None:
    """Remove *path*, retrying only a handle that is still closing.

    ``remover`` and ``sleep`` are seams for the unit test; production uses
    ``shutil.rmtree`` and ``time.sleep``. A path that is already gone is
    success. A persistent sharing error is raised after the last attempt, and a
    non-transient error is raised without retrying.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    for attempt in range(attempts):
        try:
            remover(path)
            return
        except FileNotFoundError:
            return
        except OSError as err:
            if not _transient_cleanup_error(err):
                raise
            if attempt + 1 == attempts:
                raise
            sleep(delay)


@contextmanager
def temporary_tree(
    *,
    prefix: str = "noteagent-",
    suffix: str = "",
) -> Iterator[Path]:
    """Yield a temporary directory whose removal retries a closing handle."""
    root = Path(tempfile.mkdtemp(prefix=prefix, suffix=suffix))
    try:
        yield root
    finally:
        remove_tree(root)
