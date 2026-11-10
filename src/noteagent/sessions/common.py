"""What the session store and its migration must agree on.

One id rule, one line encoding, and one lock: the migration writes the same
files the store appends to, so they cannot each keep their own copy of the rule
or run at the same time.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping

#: A session id is one path component and is also the header's id.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
RESERVED_SESSION_IDS = frozenset({"index"})

#: Appends, deletes and the one migration pass share this. It is process-wide,
#: which matches the app: one reader, one writer, one project at a time.
SESSION_LOCK = threading.Lock()


def session_line(payload: Mapping[str, object]) -> str:
    """One entry as it is written to a session file."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
