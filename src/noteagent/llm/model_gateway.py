"""The OpenAI-compatible adapter behind the kernel's ``ModelClient``.

The loop asks one question in the kernel's language and receives the same events
from every provider. This module turns that request into the OpenAI wire shape,
reads the stream back, and reports deltas, whole tool calls, usage and the end of
the turn.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

import httpx

from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelDoneEvent,
    ModelErrorEvent,
    ModelEvent,
    ModelRequest,
    ModelStartEvent,
    ModelStreamError,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCallDelta,
    ModelToolCallEnd,
    ModelUsageEvent,
    ToolCall,
    Usage,
)
from noteagent.llm.client import iter_sse_payloads
from noteagent.llm.config import LlmConfig


def openai_body(request: ModelRequest, config: LlmConfig) -> dict[str, Any]:
    """The request as an OpenAI-compatible chat completions body."""
    body: dict[str, Any] = {
        "model": request.model or config.model,
        "messages": [_message(item) for item in request.messages],
        "stream": True,
        # Without this an OpenAI-compatible stream reports no usage at all.
        "stream_options": {"include_usage": True},
        "temperature": (
            request.temperature
            if request.temperature is not None
            else config.temperature
        ),
        "max_tokens": (
            request.max_tokens if request.max_tokens is not None else config.max_tokens
        ),
    }
    if request.tools:
        body["tools"] = [dict(tool) for tool in request.tools]
        body["tool_choice"] = "auto"
    return body


def _message(message: Message) -> dict[str, Any]:
    if message.role == "tool":
        body: dict[str, Any] = {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
        if message.name:
            body["name"] = message.name
        return body
    body = {"role": message.role, "content": message.content}
    if message.tool_calls:
        body["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
        if not message.content:
            body["content"] = None
    return body


class OpenAIModelClient:
    """A ``ModelClient`` for OpenAI and the providers that copy its shape."""

    def __init__(self, config: LlmConfig) -> None:
        self._config = config
        self._url = config.chat_completions_url

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        try:
            with httpx.Client(timeout=self._config.timeout_seconds) as client:
                with client.stream(
                    "POST",
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=openai_body(request, self._config),
                ) as response:
                    if response.status_code >= 400:
                        detail = response.read().decode("utf-8", errors="replace")
                        yield ModelErrorEvent(
                            _error_code(response.status_code, detail),
                            f"model HTTP {response.status_code}: {detail[:800]}",
                            retryable=_retryable(response.status_code),
                        )
                        return
                    yield ModelStartEvent(request.model or self._config.model)
                    yield from _stream_events(response, request)
        except httpx.HTTPError as err:
            yield ModelErrorEvent(
                "model_error", f"model request failed: {err}", retryable=True
            )


def _stream_events(
    response: httpx.Response, request: ModelRequest
) -> Iterator[ModelEvent]:
    calls: dict[int, dict[str, str]] = {}
    finish = ""
    usage: Usage | None = None
    saw_done = False

    def mark_done() -> None:
        nonlocal saw_done
        saw_done = True

    for payload in iter_sse_payloads(response, on_done=mark_done):
        if request.cancel is not None and request.cancel.is_set():
            raise ModelStreamError("cancelled", "the run was cancelled", retryable=True)
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, Mapping):
            usage = _usage(raw_usage)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            finish = reason
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            continue
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            yield ModelThinkingDelta(reasoning)
        text = delta.get("content")
        if isinstance(text, str) and text:
            yield ModelTextDelta(text)
        for item in delta.get("tool_calls") or []:
            if not isinstance(item, Mapping):
                continue
            index = item.get("index", 0)
            if not isinstance(index, int):
                index = 0
            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                slot["id"] = call_id
            function = item.get("function")
            if not isinstance(function, Mapping):
                function = {}
            name = function.get("name")
            if isinstance(name, str) and name:
                slot["name"] = name
            fragment = function.get("arguments")
            if not isinstance(fragment, str):
                fragment = ""
            slot["arguments"] += fragment
            yield ModelToolCallDelta(
                index=index,
                call_id=slot["id"],
                name=slot["name"],
                arguments=fragment,
            )

    if not finish and not saw_done:
        # A connection that closes without either marker was cut, not finished.
        # Committing what arrived would save a half answer as a whole one.
        yield ModelErrorEvent(
            "model_error",
            "the model stream ended before it finished",
            retryable=True,
        )
        return

    for index, slot in sorted(calls.items()):
        if not slot["name"]:
            continue
        yield ModelToolCallEnd(
            ToolCall(
                id=slot["id"] or f"call-{index + 1}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
        )
    if usage is not None:
        yield ModelUsageEvent(usage)
    yield ModelDoneEvent(AssistantMessage(stop_reason=finish, usage=usage))


def _usage(raw: Mapping[str, Any]) -> Usage:
    cache_read = 0
    details = raw.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        cached = details.get("cached_tokens")
        if isinstance(cached, int):
            cache_read = cached
    if not cache_read:
        cached = raw.get("cache_read_input_tokens")
        if isinstance(cached, int):
            cache_read = cached
    return Usage(
        input_tokens=_int(raw.get("prompt_tokens")),
        output_tokens=_int(raw.get("completion_tokens")),
        cache_read_tokens=cache_read,
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _retryable(status: int) -> bool:
    return status in (408, 409, 425, 429) or status >= 500


#: How providers word "this prompt did not fit". The loop compacts and asks
#: once more when it sees this, so the match is deliberately narrow: a bad
#: request must stay a bad request.
_CONTEXT_OVERFLOW = re.compile(
    r"context length|context window|maximum context|too many tokens|"
    r"prompt is too long|input is too long|exceeds the context",
    re.IGNORECASE,
)


def _error_code(status: int, detail: str) -> str:
    if status in (400, 413) and _CONTEXT_OVERFLOW.search(detail):
        return "context_overflow"
    return "model_error"
