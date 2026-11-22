"""The judge transport shared by the quality evals: client, verdict, turns."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from noteagent.llm.client import ChatMessage, LlmClient, LlmTurn
from noteagent.llm.config import LlmConfig

JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 4096


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


def judge_config(config: LlmConfig) -> LlmConfig:
    """The judge samples at temperature 0 and a small output budget."""
    return replace(
        config,
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
    )


def make_judge_client(config: LlmConfig) -> LlmClient:
    """The production judge transport, built from the same connection."""
    return LlmClient(judge_config(config))


def conversation_from(
    entries: Iterable[Mapping[str, Any]],
) -> tuple[tuple[str, str], ...]:
    """The user turns and the assistant's final answers, not tool preambles.

    A silent propose call leaves no assistant message; its recorded reason
    (or intent) is the proposal the judge must grade.
    """
    out: list[tuple[str, str]] = []
    proposal = ""
    for entry in entries:
        if entry.get("type") == "wait_proposed":
            text = entry.get("reason") or entry.get("intent")
            if isinstance(text, str) and text.strip():
                proposal = text.strip()
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "assistant" and entry.get("toolCalls"):
            continue
        out.append((str(role), content.strip()))
    if proposal and not any(role == "assistant" for role, _ in out):
        out.append(("assistant", proposal))
    return tuple(out)


__all__ = [
    "DimensionVerdict",
    "JudgeClient",
    "conversation_from",
    "judge_config",
    "make_judge_client",
]
