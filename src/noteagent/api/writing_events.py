"""The writing state as it changes, for the panel that is open on it.

The state is a file, not a live object: the stream reads its version and says
when it moved. A person's save and a run's tool call both move it, so the panel
does not have to poll or guess when the reader should look again.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from noteagent.api.sse import KEEPALIVE_SECONDS
from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingStateError, WritingStateStore

#: How often the stream reads the state file.
POLL_SECONDS = 0.5


def iter_writing_events(
    layout: ProjectLayout, *, poll: float = POLL_SECONDS
) -> Iterator[bytes]:
    """A ``state`` event whenever the state's version changes."""
    store = WritingStateStore(layout)
    seen: int | None = None
    waited = 0.0
    while True:
        try:
            version = store.load().state_version
        except WritingStateError:
            version = -1
        if version != seen:
            seen = version
            waited = 0.0
            payload = json.dumps(
                {"stateVersion": version}, ensure_ascii=False
            )
            yield f"event: state\ndata: {payload}\n\n".encode("utf-8")
        time.sleep(poll)
        waited += poll
        if waited >= KEEPALIVE_SECONDS:
            waited = 0.0
            yield b": keepalive\n\n"


__all__ = ["POLL_SECONDS", "iter_writing_events"]
