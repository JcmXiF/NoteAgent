"""The sections a prompt is built from.

A context source contributes one named block. The projector decides where it
goes and what it costs; the source only says what it would say right now, or
``None`` when it has nothing to add.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """What a source may use when deciding what to render."""

    session_id: str
    run_id: str = ""
    query: str = ""
    last_tool_result: str = ""


@runtime_checkable
class ContextSource(Protocol):
    def key(self) -> str:
        """The section name this source fills."""
        ...

    def render(self, request: ContextRequest) -> str | None:
        """The section's text, or ``None`` when it contributes nothing."""
        ...
