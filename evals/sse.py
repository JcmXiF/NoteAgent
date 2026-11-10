"""Reading server-sent events back, for the eval cases.

The app writes one ``event:`` line and one JSON ``data:`` line per block. A
keep-alive is a comment, not an event, so it never reaches a case's assertions.
"""

from __future__ import annotations

import json
from typing import Any


def parse_sse(text: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    """The named events in one SSE body, in the order they were sent."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        kind = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if kind and data_lines:
            events.append((kind, json.loads("\n".join(data_lines))))
    return tuple(events)


__all__ = ["parse_sse"]
