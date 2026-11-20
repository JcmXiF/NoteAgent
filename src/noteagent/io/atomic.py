"""Writing a file so a reader sees either the old bytes or the new ones.

A page, a note, an index and the pointer to the active project are each read by
something that did not write them. A write interrupted part way leaves a
truncated file, which still reads as valid text and quietly loses whatever came
after the cut. So the bytes go to a temporary name beside the destination and
are moved into place in one step: the same folder keeps the move on one
filesystem, where it is atomic, and a unique name keeps two writers from sharing
a temporary file.

The bytes are flushed and synced before the move, because the point is a file
that survives the machine going away — not only the process.
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from secrets import token_hex

__all__ = ["write_bytes_atomic", "write_text_atomic"]

#: How many times a replace retries a transient Windows lock, and the base
#: pause between tries. A reader polling the file (a UI watching a run) can
#: hold it for an instant; Windows denies the move until it closes.
_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_S = 0.005


def _transient_replace_error(err: OSError) -> bool:
    """Whether a failed replace is the instant a reader holds the target.

    Windows refuses to replace a file another handle has open and reports it as
    ``PermissionError`` (WinError 5, access denied) or WinError 32/33 (sharing
    or lock violation); ``EBUSY`` is the POSIX spelling. A plain OSError, a
    missing folder or a read-only target is a real failure and is not retried.
    """
    if isinstance(err, PermissionError):
        return True
    if err.errno in (errno.EACCES, errno.EBUSY):
        return True
    return getattr(err, "winerror", None) in (5, 32, 33)


def _replace_with_retry(source: Path, target: Path) -> None:
    """Move *source* onto *target*, waiting out a reader's transient lock.

    The rename is still one atomic step and still the last thing the write
    does; only the wait before retrying changes. A lock that outlives the
    retries is raised, and the caller removes the temporary as before.
    """
    last: OSError | None = None
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except OSError as err:
            if not _transient_replace_error(err):
                raise
            last = err
            if attempt + 1 < _REPLACE_ATTEMPTS:
                time.sleep(_REPLACE_RETRY_S * (attempt + 1))
    if last is None:  # pragma: no cover - the loop only ends after a retry
        raise OSError(f"cannot replace {target}")
    raise last


def write_bytes_atomic(path: Path, data: bytes) -> int:
    """Put *data* at *path*, replacing what was there, and say how much was written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # A replacement keeps the permissions the file had. A file made for the first
    # time gets what any new file would, which is the umask's decision, not this
    # module's.
    previous_mode = _mode_of(target)
    temporary = target.parent / f".{target.name}.{token_hex(8)}.tmp"
    try:
        with open(temporary, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        _replace_with_retry(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return len(data)


def write_text_atomic(path: Path, text: str) -> int:
    """Put *text* at *path* as UTF-8, replacing what was there, and count the bytes."""
    return write_bytes_atomic(Path(path), text.encode("utf-8"))


def _mode_of(path: Path) -> int | None:
    """The permissions the file had, or None when it is new."""
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None
