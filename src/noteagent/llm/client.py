"""The OpenAI-compatible chat transport: one turn in, one answer out.

Both a plain request and a streamed one end as the same ``LlmTurn``, so a caller
does not have to know which it got. Only ``complete`` exposes tool calls.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Event
from typing import Any

import httpx

from noteagent.llm.config import LlmConfig
from noteagent.util.cancel import check_cancelled


class LlmError(RuntimeError):
    """Raised when an LLM HTTP call fails."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One OpenAI-compatible function tool call from the model."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message in a completions request."""

    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True)
class LlmTurn:
    """One completions choice: final text and/or tool calls."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = ""


def _extract_content_delta(
    payload: dict[str, Any], *, fallback_reasoning: bool
) -> str:
    """The text in one streamed chunk, falling back to a reasoning field."""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str) and content:
        return content
    if not fallback_reasoning:
        return ""
    reasoning = delta.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    return ""


def _extract_message_content(
    payload: dict[str, Any], *, fallback_reasoning: bool
) -> str:
    """The text of a completed turn, falling back to a reasoning field."""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if fallback_reasoning:
        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning
    return content if isinstance(content, str) else ""


def _extract_tool_calls(message: dict[str, Any]) -> tuple[ToolCall, ...]:
    """The calls a completed turn asked for, skipping entries without a name."""
    raw = message.get("tool_calls") or []
    if not isinstance(raw, list):
        return ()
    out: list[ToolCall] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        cid = str(item.get("id") or "").strip() or f"call-{index + 1}"
        arguments = fn.get("arguments")
        if isinstance(arguments, str):
            text = arguments
        elif arguments is None:
            text = "{}"
        else:
            text = json.dumps(arguments, ensure_ascii=False)
        out.append(ToolCall(id=cid, name=name, arguments=text))
    return tuple(out)


def _empty_content_detail(payload: dict[str, Any]) -> str:
    """What an empty answer arrived with, for the error message."""
    choices = payload.get("choices") or []
    if not choices:
        return "no choices"
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    finish = choice.get("finish_reason")
    keys = sorted(str(k) for k in message.keys())
    usage = payload.get("usage")
    return f"finish_reason={finish!r} message_keys={keys} usage={usage!r}"


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    """One message as the OpenAI request body spells it."""
    if message.role == "tool":
        payload: dict[str, Any] = {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
        if message.name:
            payload["name"] = message.name
        return payload
    payload = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
        if not message.content:
            payload["content"] = None
    return payload


def language_name(code: str) -> str:
    """
    What to call a language when telling a model which one to write in.

    An unknown or empty code answers with an empty name, and the prompt then leaves
    the question alone rather than guessing.
    """
    known = {
        "en": "English",
        "zh": "Chinese (简体中文)",
        "zh-cn": "Chinese (简体中文)",
        "zh-hans": "Chinese (简体中文)",
        "zh-tw": "Chinese (繁體中文)",
        "ja": "Japanese",
        "ko": "Korean",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
    }
    return known.get(code.strip().lower(), "")


class LlmClient:
    """Minimal OpenAI-compatible chat client."""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        on_token: Callable[[str], None] | None = None,
        cancel: Event | None = None,
    ) -> str:
        """Completions as a single string. Used by ingest.

        Empty ``content`` falls back to ``reasoning_content``. Chat Query should
        call ``complete`` instead so tool calls are not collapsed.
        """
        turn = self.complete(
            messages,
            on_token=on_token,
            fallback_reasoning=True,
            cancel=cancel,
        )
        if not turn.content.strip():
            raise LlmError("LLM returned empty message content")
        return turn.content

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
        fallback_reasoning: bool = True,
        cancel: Event | None = None,
    ) -> LlmTurn:
        """One completions turn. ``tools`` is the OpenAI tools array, or None."""
        check_cancelled(cancel)
        if on_token is None:
            return self._complete_once(
                messages,
                tools=tools,
                fallback_reasoning=fallback_reasoning,
            )
        return self._complete_stream(
            messages,
            tools=tools,
            on_token=on_token,
            fallback_reasoning=fallback_reasoning,
        )

    def _headers(self) -> dict[str, str]:
        """Request headers, including the bearer token."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _body(
        self,
        messages: list[ChatMessage],
        *,
        stream: bool,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """The request body for either kind of call."""
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [_message_payload(item) for item in messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    def _complete_once(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        fallback_reasoning: bool,
    ) -> LlmTurn:
        """One non-streamed call: the whole answer inside a single response."""
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                response = client.post(
                    self.config.chat_completions_url,
                    headers=self._headers(),
                    json=self._body(messages, stream=False, tools=tools),
                )
        except httpx.HTTPError as err:
            raise LlmError(f"LLM request failed: {err}") from err

        if response.status_code >= 400:
            raise LlmError(
                f"LLM HTTP {response.status_code}: {response.text[:800]}"
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as err:
            raise LlmError(
                f"LLM returned non-JSON body: {response.text[:400]}"
            ) from err

        choices = payload.get("choices") or []
        message = {}
        finish = ""
        if choices and isinstance(choices[0], dict):
            finish = str(choices[0].get("finish_reason") or "")
            raw = choices[0].get("message")
            if isinstance(raw, dict):
                message = raw
        tool_calls = _extract_tool_calls(message)
        content = _extract_message_content(
            payload, fallback_reasoning=fallback_reasoning and not tool_calls
        )
        if not content.strip() and not tool_calls:
            raise LlmError(
                "LLM returned empty message content "
                f"({_empty_content_detail(payload)})"
            )
        return LlmTurn(
            content=content or "",
            tool_calls=tool_calls,
            finish_reason=finish,
        )

    def _complete_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None,
        on_token: Callable[[str], None],
        fallback_reasoning: bool,
    ) -> LlmTurn:
        """One streamed call: tokens go to *on_token* as they arrive."""
        chunks: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = ""
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with client.stream(
                    "POST",
                    self.config.chat_completions_url,
                    headers=self._headers(),
                    json=self._body(messages, stream=True, tools=tools),
                ) as response:
                    if response.status_code >= 400:
                        body = response.read().decode("utf-8", errors="replace")
                        raise LlmError(
                            f"LLM HTTP {response.status_code}: {body[:800]}"
                        )
                    for event in self._iter_sse_payloads(response):
                        choices = event.get("choices") or []
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        choice = choices[0]
                        reason = choice.get("finish_reason")
                        if isinstance(reason, str) and reason:
                            finish = reason
                        delta = choice.get("delta") or {}
                        if not isinstance(delta, dict):
                            continue
                        token = _extract_content_delta(
                            event, fallback_reasoning=fallback_reasoning
                        )
                        if token:
                            chunks.append(token)
                            on_token(token)
                        for item in delta.get("tool_calls") or []:
                            if not isinstance(item, dict):
                                continue
                            index = item.get("index", 0)
                            if not isinstance(index, int):
                                index = 0
                            slot = calls.setdefault(
                                index, {"id": "", "name": "", "arguments": ""}
                            )
                            if item.get("id"):
                                slot["id"] = str(item["id"])
                            fn = item.get("function")
                            if isinstance(fn, dict):
                                if fn.get("name"):
                                    slot["name"] = str(fn["name"])
                                if isinstance(fn.get("arguments"), str):
                                    slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as err:
            raise LlmError(f"LLM stream failed: {err}") from err

        tool_calls = tuple(
            ToolCall(
                id=slot["id"] or f"call-{index + 1}",
                name=slot["name"],
                arguments=slot["arguments"] or "{}",
            )
            for index, slot in sorted(calls.items())
            if slot["name"]
        )
        content = "".join(chunks)
        if not content.strip() and not tool_calls:
            raise LlmError("LLM stream returned empty content")
        return LlmTurn(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish,
        )

    def _iter_sse_payloads(
        self, response: httpx.Response
    ) -> Iterator[dict[str, Any]]:
        """The JSON objects of a server-sent-events stream, up to ``[DONE]``."""
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
            else:
                continue
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload
