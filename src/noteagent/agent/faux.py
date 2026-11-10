"""A model that replays a script, for tests and evals.

It offers the same ``ModelClient`` seam as a provider adapter, so a test drives
the real loop without a network, a key, or a clock it cannot predict.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from noteagent.agent.model import (
    AssistantMessage,
    ModelDoneEvent,
    ModelEvent,
    ModelRequest,
    ModelStartEvent,
    ModelStreamError,
    ModelTextDelta,
    ModelToolCallEnd,
    TextBlock,
    ToolCall,
)


class FauxModelClient:
    """One script per call: each call consumes the next list of events."""

    def __init__(self, *scripts: Iterable[ModelEvent]) -> None:
        self._scripts = [list(script) for script in scripts]
        self.requests: list[ModelRequest] = []

    @property
    def script(self) -> list[ModelEvent]:
        """The one script this client holds, for composing a multi-call script."""
        if len(self._scripts) != 1:
            raise ValueError("a faux client holds exactly one script here")
        return list(self._scripts[0])

    @classmethod
    def text(
        cls,
        text: str,
        *,
        chunks: Sequence[str] | None = None,
    ) -> FauxModelClient:
        pieces = list(chunks) if chunks is not None else [text]
        events: list[ModelEvent] = [ModelStartEvent("faux")]
        events.extend(ModelTextDelta(piece) for piece in pieces)
        events.append(
            ModelDoneEvent(
                AssistantMessage(blocks=(TextBlock(text),), stop_reason="stop")
            )
        )
        return cls(events)

    @classmethod
    def tool_call(
        cls,
        call_id: str,
        name: str,
        arguments: str,
        *,
        text: str = "",
    ) -> FauxModelClient:
        events: list[ModelEvent] = [ModelStartEvent("faux")]
        if text:
            events.append(ModelTextDelta(text))
        events.append(ModelToolCallEnd(ToolCall(call_id, name, arguments)))
        events.append(ModelDoneEvent(AssistantMessage(stop_reason="tool_calls")))
        return cls(events)

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        self.requests.append(request)
        if not self._scripts:
            raise ModelStreamError("model_error", "no scripted response")
        script = self._scripts.pop(0)
        yield from script
