"""The judge transport shared by the quality evals: client, verdict, turns."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from noteagent.llm.client import ChatMessage, LlmClient, LlmError, LlmTurn
from noteagent.llm.config import LlmConfig

JUDGE_TEMPERATURE = 0.0
#: A reasoning model spends completion tokens on thinking before its answer.
JUDGE_MAX_TOKENS = 16_384


class JudgeClient(Protocol):
    """The one method a judge transport has to offer."""

    def complete(self, messages: list[ChatMessage], **kwargs: Any) -> LlmTurn:
        ...


@dataclass(frozen=True, slots=True)
class DimensionVerdict:
    """One dimension's verdict, with the reason and quotes behind it."""

    dimension: str
    verdict: str
    reason: str = ""
    evidence: tuple[str, ...] = ()


def judge_config(
    config: LlmConfig, *, max_tokens: int = JUDGE_MAX_TOKENS
) -> LlmConfig:
    """The judge samples at temperature 0 with room for its thinking."""
    return replace(
        config,
        temperature=JUDGE_TEMPERATURE,
        max_tokens=max_tokens,
    )


class RetryingJudge:
    """A judge that retries one truncated turn with double the budget.

    A reasoning model can spend the whole completion budget on thinking and
    return no answer, so the first call may fail with
    ``finish_reason == "length"``. Either way, one retry with twice the base
    budget is the whole policy; the retry's usage joins the first attempt's,
    including a failed one. The base budget never changes, so a reused judge
    does not drift upward.
    """

    def __init__(
        self,
        client_for: Callable[[int], JudgeClient],
        max_tokens: int = JUDGE_MAX_TOKENS,
    ) -> None:
        self._client_for = client_for
        self.max_tokens = max_tokens
        self._client = client_for(max_tokens)

    def complete(
        self, messages: list[ChatMessage], **kwargs: Any
    ) -> LlmTurn:
        try:
            turn = self._client.complete(messages, **kwargs)
        except LlmError as err:
            if err.finish_reason != "length":
                raise
            return self._retry(
                messages,
                first_in=err.input_tokens,
                first_out=err.output_tokens,
                **kwargs,
            )
        if turn.finish_reason != "length":
            return turn
        return self._retry(
            messages,
            first_in=turn.input_tokens,
            first_out=turn.output_tokens,
            **kwargs,
        )

    def _retry(
        self,
        messages: list[ChatMessage],
        *,
        first_in: int = 0,
        first_out: int = 0,
        **kwargs: Any,
    ) -> LlmTurn:
        client = self._client_for(self.max_tokens * 2)
        try:
            turn = client.complete(messages, **kwargs)
        except LlmError as err:
            raise LlmError(
                str(err),
                finish_reason=err.finish_reason,
                input_tokens=first_in + err.input_tokens,
                output_tokens=first_out + err.output_tokens,
            ) from err
        return replace(
            turn,
            input_tokens=first_in + turn.input_tokens,
            output_tokens=first_out + turn.output_tokens,
        )


def make_judge_client(config: LlmConfig) -> JudgeClient:
    """The production judge transport, built from the same connection."""
    return RetryingJudge(
        lambda tokens: LlmClient(
            judge_config(config, max_tokens=tokens)
        )
    )


def conversation_from(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    """The user turns and the assistant's final answers, not tool preambles."""
    out: list[tuple[str, str]] = []
    for entry in entries:
        role = entry.get("role")
        content = entry.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "assistant" and entry.get("toolCalls"):
            continue
        out.append((str(role), content.strip()))
    return tuple(out)


__all__ = [
    "DimensionVerdict",
    "JudgeClient",
    "RetryingJudge",
    "conversation_from",
    "judge_config",
    "make_judge_client",
]
