"""The events a run reports, as the interface shows them.

An event is what happened while a run was happening; the transcript is what
survives it. ``payload`` is the body an SSE event carries, so the names here are
the names the interface reads, not an internal shorthand.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar, TypeAlias


@dataclass(frozen=True, slots=True)
class StatusEvent:
    """What the run is doing, in words the reader can follow."""

    message: str
    step_id: str = ""
    kind: ClassVar[str] = "status"

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {"message": self.message}
        if self.step_id:
            body["stepId"] = self.step_id
        return body


@dataclass(frozen=True, slots=True)
class TokenEvent:
    """A piece of the answer as it is written.

    ``reset`` takes back what was streamed for a round that turned out to be a
    tool call rather than an answer.
    """

    text: str
    reset: bool = False
    kind: ClassVar[str] = "token"

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {"text": self.text}
        if self.reset:
            body["reset"] = True
        return body


@dataclass(frozen=True, slots=True)
class ObservationEvent:
    """A card for something the assistant looked at.

    The card is already the whitelisted view: content stays with the model.
    """

    observation: Mapping[str, object]
    kind: ClassVar[str] = "observation"

    def payload(self) -> dict[str, object]:
        return dict(self.observation)


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """Why the run stopped, in a shape a client can act on."""

    code: str
    message: str
    params: Mapping[str, str] = field(default_factory=dict)
    retryable: bool = False
    kind: ClassVar[str] = "error"

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.params:
            body["params"] = dict(self.params)
        return body


@dataclass(frozen=True, slots=True)
class DoneEvent:
    """The end of a run: the session as it now stands, and the run itself."""

    session: Mapping[str, object]
    run: Mapping[str, object] | None = None
    kind: ClassVar[str] = "done"

    def payload(self) -> dict[str, object]:
        body: dict[str, object] = {"session": dict(self.session)}
        if self.run is not None:
            body["run"] = dict(self.run)
        return body


#: Everything a run may report. ``error`` and ``done`` are the only terminal
#: ones: a tool that failed is an observation, not the end of the run.
RunEvent: TypeAlias = (
    StatusEvent
    | TokenEvent
    | ObservationEvent
    | ErrorEvent
    | DoneEvent
)


def is_terminal(event: RunEvent) -> bool:
    """Whether this event ends the run's stream."""
    return isinstance(event, (ErrorEvent, DoneEvent))
