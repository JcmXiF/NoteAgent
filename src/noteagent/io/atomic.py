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

import os
from pathlib import Path
from secrets import token_hex

__all__ = ["write_bytes_atomic", "write_text_atomic"]


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
        os.replace(temporary, target)
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
