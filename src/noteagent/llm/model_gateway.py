"""The OpenAI-compatible adapter behind the kernel's ``ModelClient``.

The loop asks one question in the kernel's language and receives the same events
from every provider. This module turns that request into the OpenAI wire shape,
reads the stream back, and reports deltas, whole tool calls, usage and the end of
the turn.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
from noteagent.util.redact import redact


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
    """A ``ModelClient`` for OpenAI and the providers that copy its shape.

    *client* is a test seam: production leaves it ``None`` and the adapter owns
    the connection. A caller that injects one also owns its lifecycle.
    """

    def __init__(
        self, config: LlmConfig, *, client: httpx.Client | None = None
    ) -> None:
        self._config = config
        self._url = config.chat_completions_url
        self._client = client

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        if self._client is not None:
            yield from self._stream_with(self._client, request)
            return
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            yield from self._stream_with(client, request)

    def _stream_with(
        self, client: httpx.Client, request: ModelRequest
    ) -> Iterator[ModelEvent]:
        try:
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
                    # A proxy or provider may quote the request, key included,
                    # in its error body. This message reaches the transcript and
                    # the interface, so the key is removed here, at the boundary.
                    detail = redact(detail, (self._config.api_key,))
                    yield ModelErrorEvent(
                        _error_code(response.status_code, detail),
                        f"model HTTP {response.status_code}: {detail[:800]}",
                        retryable=_retryable(response.status_code),
                        retry_after_s=_retry_after(
                            response.headers.get("retry-after")
                        ),
                    )
                    return
                yield ModelStartEvent(request.model or self._config.model)
                yield from _stream_events(
                    response, request, secret=self._config.api_key
                )
        except httpx.HTTPError as err:
            yield ModelErrorEvent(
                "model_error",
                redact(f"model request failed: {err}", (self._config.api_key,)),
                retryable=True,
            )


#: DeepSeek can serialize a tool call as DSML instead of the OpenAI
#: ``tool_calls`` channel, especially when the request omits the tools array.
#: The envelope has appeared with full-width bars, ASCII pipes, and no bars.
_DSML_OPEN = r"<[｜|]{0,2}\s*DSML\s*[｜|]{0,2}"
_DSML_CLOSE = r"</[｜|]{0,2}\s*DSML\s*[｜|]{0,2}"
_DSML_TAG = re.compile(
    r"</?[｜|]{0,2}\s*DSML\s*[｜|]{0,2}\s*"
    r"(?:calls|tool_calls|function_calls|invoke|parameter)\b[^>]*>",
    re.IGNORECASE,
)
_DSML_INVOKE = re.compile(
    _DSML_OPEN + r"\s*invoke\s+name=\"([^\"]+)\"\s*>(.*?)"
    + _DSML_CLOSE + r"\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER = re.compile(
    _DSML_OPEN
    + r"\s*parameter\s+name=\"([^\"]+)\""
    + r"(?:\s+string=\"(true|false)\")?\s*>(.*?)"
    + _DSML_CLOSE
    + r"\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MAX_HELD_PREFIX = 16


def _recover_dsml(content: str) -> tuple[str, tuple[ToolCall, ...]]:
    """Recover complete DSML invokes and remove every DSML tag."""
    if not _DSML_TAG.search(content):
        return content, ()
    calls: list[ToolCall] = []
    for index, match in enumerate(_DSML_INVOKE.finditer(content), 1):
        name = match.group(1).strip()
        if not name:
            continue
        arguments: dict[str, object] = {}
        for parameter in _DSML_PARAMETER.finditer(match.group(2)):
            key = parameter.group(1).strip()
            if not key:
                continue
            raw = parameter.group(3).strip()
            if (parameter.group(2) or "true").lower() == "true":
                arguments[key] = raw
                continue
            try:
                arguments[key] = json.loads(raw)
            except json.JSONDecodeError:
                arguments[key] = raw
        calls.append(
            ToolCall(
                id=f"dsml-{uuid.uuid4().hex[:8]}-{index}",
                name=name,
                arguments=json.dumps(arguments, ensure_ascii=False),
            )
        )
    # Drop the invoke element before its tags: the parameter values live
    # between tags and would otherwise survive as prose.
    cleaned = _DSML_INVOKE.sub("", content)
    return _DSML_TAG.sub("", cleaned).strip(), tuple(calls)


def _hold_index(text: str) -> int:
    """Hold a tail that could still become a DSML opener."""
    index = text.rfind("<")
    if index < 0:
        return len(text)
    tail = text[index:]
    if len(tail) > _MAX_HELD_PREFIX:
        return len(text)
    rest = tail[1:].lstrip("｜| \t")
    if not rest:
        return index
    upper = rest.upper()
    if "DSML".startswith(upper) or upper.startswith("DSML"):
        return index
    return len(text)


def _unemitted(cleaned: str, emitted: str) -> str:
    """The part of *cleaned* the caller has not already streamed."""
    if not emitted:
        return cleaned
    common = 0
    for left, right in zip(cleaned, emitted):
        if left != right:
            break
        common += 1
    return cleaned[common:]


def _stream_events(
    response: httpx.Response,
    request: ModelRequest,
    *,
    secret: str = "",
) -> Iterator[ModelEvent]:
    calls: dict[int, dict[str, str]] = {}
    recovered: list[ToolCall] = []
    finish = ""
    usage: Usage | None = None
    saw_done = False
    content_parts: list[str] = []
    pending = ""
    emitted = ""
    leak_started = False

    def mark_done() -> None:
        nonlocal saw_done
        saw_done = True

    for payload in iter_sse_payloads(response, on_done=mark_done):
        if request.cancel is not None and request.cancel.is_set():
            raise ModelStreamError("cancelled", "the run was cancelled", retryable=True)
        if request.pause is not None and request.pause.is_set():
            # The kernel turns this short stream into an interrupted run.
            return
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
            content_parts.append(text)
            pending += text
            if leak_started:
                continue
            match = _DSML_TAG.search(pending)
            if match is not None:
                prefix = pending[: match.start()]
                if prefix:
                    emitted += prefix
                    yield ModelTextDelta(prefix)
                pending = pending[match.start() :]
                leak_started = True
                continue
            hold = _hold_index(pending)
            if hold < len(pending):
                prefix = pending[:hold]
                if prefix:
                    emitted += prefix
                    yield ModelTextDelta(prefix)
                pending = pending[hold:]
            else:
                emitted += pending
                yield ModelTextDelta(pending)
                pending = ""
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

    full = "".join(content_parts)
    if leak_started or _DSML_TAG.search(full):
        cleaned, recovered_calls = _recover_dsml(full)
        recovered.extend(recovered_calls)
        if recovered_calls:
            suffix = _unemitted(cleaned, emitted)
            if suffix:
                yield ModelTextDelta(suffix)
            finish = "tool_calls"
        elif calls:
            # Structured calls arrived beside the markup; keep them, drop the
            # markup from the preamble.
            suffix = _unemitted(cleaned, emitted)
            if suffix:
                yield ModelTextDelta(suffix)
        else:
            snippet = full[:300]
            if secret:
                snippet = redact(snippet, (secret,))
            raise ModelStreamError(
                "tool_call_leak",
                "the model wrote tool-call markup instead of an answer: "
                + snippet,
                retryable=True,
            )
    elif pending:
        emitted += pending
        yield ModelTextDelta(pending)
        pending = ""

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
    for call in recovered:
        yield ModelToolCallEnd(call)
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
    # 409/425 are request/state conflicts, not the transient failures the loop
    # retries; only timeouts, ordinary rate limits and server errors are.
    return status in (408, 429) or status >= 500


def _retry_after(value: str | None) -> float | None:
    """The provider's ``retry-after``, in seconds or as an HTTP date."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
    return max(0.0, seconds)


#: How providers word "this prompt did not fit". The loop compacts and asks
#: once more when it sees this, so the match is deliberately narrow: a bad
#: request must stay a bad request.
_CONTEXT_OVERFLOW = re.compile(
    r"context length|context window|maximum context|too many tokens|"
    r"prompt is too long|input is too long|exceeds the context",
    re.IGNORECASE,
)

#: A 429 that is about the account's spend, not a transient rate.
_SPEND_LIMIT = re.compile(
    r"insufficient.?quota|spend limit|credit balance|billing|"
    r"exceeded your current quota|quota exceeded",
    re.IGNORECASE,
)


def _error_code(status: int, detail: str) -> str:
    if status in (400, 413) and _CONTEXT_OVERFLOW.search(detail):
        return "context_overflow"
    if status == 429 and _SPEND_LIMIT.search(detail):
        return "usage_limit"
    return "model_error"
