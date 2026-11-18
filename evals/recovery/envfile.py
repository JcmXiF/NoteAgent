"""Load the project's local ``.env`` for the eval process.

The app reads its model connection from the process environment, and this repo
does not ship a dotenv dependency. The eval keeps a small reader instead:
existing environment variables win, a missing file is not an error, and no
value is ever printed.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env_file"]


def load_env_file(path: Path) -> tuple[str, ...]:
    """Set ``KEY=VALUE`` pairs from *path* for keys not already in the process.

    Returns the keys it set. Surrounding quotes are stripped; comments, blank
    lines and malformed lines are skipped.
    """
    path = Path(path)
    if not path.is_file():
        return ()
    loaded: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
        loaded.append(key)
    return tuple(loaded)
