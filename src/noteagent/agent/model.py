"""The model seam: one streamed request, events out, one message collected.

A provider adapter speaks its own wire format and reports through this contract.
The loop never sees a provider's payload: it sees deltas, whole tool calls, usage,
and the message they add up to. The message keeps its content blocks so a
provider's thinking signature and the order of text and calls survive the trip.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from threading import Event
from typing import ClassVar, Literal, Protocol, TypeAlias, runtime_checkable


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One function call the model asked for."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class Message:
    """One message in a request, in the shape every provider understands."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class Usage:
    """What the provider said the call cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    text: str
    #: Some providers sign their reasoning; the signature must come back with it.
    signature: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    call: ToolCall


ContentBlock: TypeAlias = TextBlock | ThinkingBlock | ToolCallBlock


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """The message a streamed turn adds up to, in the order it was written."""

    blocks: tuple[ContentBlock, ...] = ()
    stop_reason: str = ""
    usage: Usage | None = None

    @property
    def content(self) -> str:
        """The prose, with the thinking and the calls left out."""
        return "".join(
            block.text for block in self.blocks if isinstance(block, TextBlock)
        )

    @property
    def thinking(self) -> str:
        return "".join(
            block.text for block in self.blocks if isinstance(block, ThinkingBlock)
        )

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            block.call for block in self.blocks if isinstance(block, ToolCallBlock)
        )


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One call: the conversation so far, the tools offered, and the knobs."""

    model: str
    messages: tuple[Message, ...]
    tools: tuple[Mapping[str, object], ...] = ()
    #: ``None`` leaves the setting to the adapter's configured default.
    temperature: float | None = None
    max_tokens: int | None = None
    thinking_level: str = ""
    #: The session a prompt cache may be keyed by.
    session_id: str = ""
    #: How long the provider may keep a cached prefix, when it offers a choice.
    cache_retention: str = ""
    #: The abort signal, checked between deltas.
    cancel: Event | None = None


@dataclass(frozen=True, slots=True)
class ModelStartEvent:
    model: str
    kind: ClassVar[str] = "start"


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    text: str
    kind: ClassVar[str] = "text_delta"


@dataclass(frozen=True, slots=True)
class ModelThinkingDelta:
    text: str
    kind: ClassVar[str] = "thinking_delta"


@dataclass(frozen=True, slots=True)
class ModelToolCallDelta:
    """A fragment of a tool call, as its arguments arrive."""

    index: int
    call_id: str = ""
    name: str = ""
    arguments: str = ""
    kind: ClassVar[str] = "tool_call_delta"


@dataclass(frozen=True, slots=True)
class ModelToolCallEnd:
    """A tool call whose arguments are complete."""

    call: ToolCall
    kind: ClassVar[str] = "tool_call_end"


@dataclass(frozen=True, slots=True)
class ModelUsageEvent:
    usage: Usage
    kind: ClassVar[str] = "usage"


@dataclass(frozen=True, slots=True)
class ModelDoneEvent:
    """The turn finished; this message is what it produced."""

    message: AssistantMessage
    kind: ClassVar[str] = "done"


@dataclass(frozen=True, slots=True)
class ModelErrorEvent:
    """The provider failed before the turn finished."""

    code: str
    message: str
    retryable: bool = False
    kind: ClassVar[str] = "error"


ModelEvent: TypeAlias = (
    ModelStartEvent
    | ModelTextDelta
    | ModelThinkingDelta
    | ModelToolCallDelta
    | ModelToolCallEnd
    | ModelUsageEvent
    | ModelDoneEvent
    | ModelErrorEvent
)


class ModelStreamError(RuntimeError):
    """A stream that failed or stopped before it produced a message."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@runtime_checkable
class ModelClient(Protocol):
    """The one method a provider adapter has to offer."""

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        ...


def collect(events: Iterable[ModelEvent]) -> AssistantMessage:
    """Turn a whole stream into the message it adds up to.

    Adjacent deltas become one block; a switch between thinking, text and a tool
    call starts the next one, so their order survives. A provider that sends the
    completed message on ``done`` wins over the deltas it also streamed.
    """
    blocks: list[ContentBlock] = []
    text: list[str] = []
    thinking: list[str] = []
    usage: Usage | None = None

    def flush_text() -> None:
        if text:
            blocks.append(TextBlock("".join(text)))
            text.clear()

    def flush_thinking() -> None:
        if thinking:
            blocks.append(ThinkingBlock("".join(thinking)))
            thinking.clear()

    for event in events:
        if isinstance(event, ModelTextDelta):
            flush_thinking()
            text.append(event.text)
        elif isinstance(event, ModelThinkingDelta):
            flush_text()
            thinking.append(event.text)
        elif isinstance(event, ModelToolCallEnd):
            flush_text()
            flush_thinking()
            blocks.append(ToolCallBlock(event.call))
        elif isinstance(event, ModelUsageEvent):
            usage = event.usage
        elif isinstance(event, ModelErrorEvent):
            raise ModelStreamError(event.code, event.message, retryable=event.retryable)
        elif isinstance(event, ModelDoneEvent):
            done = event.message
            if done.blocks:
                if done.usage is None and usage is not None:
                    return AssistantMessage(done.blocks, done.stop_reason, usage)
                return done
            flush_text()
            flush_thinking()
            return AssistantMessage(tuple(blocks), done.stop_reason, done.usage or usage)
    raise ModelStreamError("model_error", "model stream ended without a done event")
