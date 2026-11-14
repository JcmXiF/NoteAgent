"""Grade an answer's text with a second model, against the seeded wiki.

The judge sees only the case's wiki pages, the conversation, and the case's
rules. It never sees the tool trace: tool choice and wait discipline belong to
the trace assertions. A response that is not the promised JSON becomes a
``judge_error``; there is no semantic fallback.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from evals.quality.cases import CaseSpec, JUDGE_DIMENSIONS, JudgeSpec

from noteagent.llm.client import ChatMessage, LlmClient, LlmTurn
from noteagent.llm.config import LlmConfig
from noteagent.util.redact import redact

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
DIMENSIONS = JUDGE_DIMENSIONS
MAX_RAW = 4000
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


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """The judge's answer, or the reason it could not be used."""

    dimensions: tuple[DimensionVerdict, ...] = ()
    error: str = ""
    raw: str = ""
    prompt_sha256: str = ""
    skipped: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and not self.skipped
            and len(self.dimensions) == len(DIMENSIONS)
            and all(item.verdict == "pass" for item in self.dimensions)
        )

    def verdicts(self) -> dict[str, str]:
        """The dimension verdicts, or empty when the judge did not decide."""
        if self.error or self.skipped:
            return {}
        return {
            item.dimension: item.verdict for item in self.dimensions
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "dimensions": [
                {
                    "dimension": item.dimension,
                    "verdict": item.verdict,
                    "reason": item.reason,
                    "evidence": list(item.evidence),
                }
                for item in self.dimensions
            ],
            "error": self.error,
            "skipped": self.skipped,
            "prompt_sha256": self.prompt_sha256,
            "raw": self.raw,
        }


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

    A silent ``propose`` call leaves no assistant message; its recorded reason
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


def prompt_digest(prompt: str, requirements: str) -> str:
    """The instructions the judge read: the prompt and the case's rules."""
    return hashlib.sha256(
        (prompt + "\n\n" + requirements).encode("utf-8")
    ).hexdigest()


def global_prompt_sha256() -> str:
    """The hash of the judge's shared instructions, without case rules."""
    return hashlib.sha256(
        PROMPT_PATH.read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()


def judge_case(
    case: CaseSpec,
    entries: Iterable[Mapping[str, Any]],
    *,
    client: JudgeClient,
    secret: str = "",
) -> JudgeVerdict:
    """Ask *client* to grade the final answer in *entries*."""
    conversation = conversation_from(entries)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    requirements = render_requirements(case.judge)
    digest = prompt_digest(prompt, requirements)
    if not any(role == "assistant" for role, _content in conversation):
        return JudgeVerdict(
            skipped="no assistant answer", prompt_sha256=digest
        )

    messages = [
        ChatMessage("system", prompt),
        ChatMessage("user", build_user_prompt(case, conversation)),
    ]
    try:
        turn = client.complete(
            messages,
            tools=None,
            fallback_reasoning=False,
            response_format={"type": "json_object"},
        )
    except Exception as err:  # noqa: BLE001 - the case records the failure
        return JudgeVerdict(
            error=f"judge_error: {_redact(str(err), secret)}",
            prompt_sha256=digest,
        )

    return _redact_verdict(
        parse_verdict(
            turn.content,
            prompt_sha256=digest,
            raw=_redact(turn.content, secret)[:MAX_RAW],
        ),
        secret,
    )


def parse_verdict(
    text: str, *, prompt_sha256: str = "", raw: str = ""
) -> JudgeVerdict:
    """Parse the judge's strict JSON, or say why it could not be used."""
    try:
        payload = _json_object(text)
        dimensions = _dimensions(payload)
    except ValueError as err:
        return JudgeVerdict(
            error=f"judge_error: {err}",
            prompt_sha256=prompt_sha256,
            raw=raw or text[:MAX_RAW],
        )
    return JudgeVerdict(
        dimensions=dimensions,
        prompt_sha256=prompt_sha256,
        raw=raw or text[:MAX_RAW],
    )


def build_user_prompt(
    case: CaseSpec, conversation: tuple[tuple[str, str], ...]
) -> str:
    return (
        "## Wiki pages\n\n"
        + render_wiki(case)
        + "\n\n## Conversation\n\n"
        + render_conversation(conversation)
        + "\n\nThe final assistant message is the answer to grade."
        + "\n\n## Case requirements\n\n"
        + render_requirements(case.judge)
    )


def render_wiki(case: CaseSpec) -> str:
    return "\n\n".join(
        f"### {page.slug} — {page.title}\n\n{page.body}" for page in case.wiki
    )


def render_conversation(
    conversation: tuple[tuple[str, str], ...]
) -> str:
    return "\n\n".join(
        f"{role}:\n{content}" for role, content in conversation
    )


def render_requirements(judge: JudgeSpec) -> str:
    blocks: list[str] = []
    for dimension in DIMENSIONS:
        rule = getattr(judge, dimension)
        lines = [f"### {dimension}"]
        if rule.must:
            lines.append("Must:")
            lines.extend(f"- {item}" for item in rule.must)
        if rule.must_not:
            lines.append("Must not:")
            lines.extend(f"- {item}" for item in rule.must_not)
        if not rule.must and not rule.must_not:
            lines.append("(no case-specific rules)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _json_object(text: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(text.strip())
    except json.JSONDecodeError as err:
        raise ValueError(f"the judge did not return JSON: {err}") from err
    if not isinstance(payload, Mapping):
        raise ValueError("the judge did not return a JSON object")
    return payload


def _dimensions(payload: Mapping[str, Any]) -> tuple[DimensionVerdict, ...]:
    unknown = sorted(
        str(key) for key in payload if key not in DIMENSIONS
    )
    if unknown:
        raise ValueError(
            f"unexpected dimension(s): {', '.join(unknown)}"
        )
    missing = [name for name in DIMENSIONS if name not in payload]
    if missing:
        raise ValueError(f"missing dimension(s): {', '.join(missing)}")

    out: list[DimensionVerdict] = []
    for name in DIMENSIONS:
        item = payload[name]
        if not isinstance(item, Mapping):
            raise ValueError(f"{name} must be a JSON object")
        verdict = item.get("verdict")
        if verdict not in ("pass", "fail"):
            raise ValueError(
                f"{name}.verdict must be 'pass' or 'fail'"
            )
        reason = item.get("reason", "")
        if not isinstance(reason, str):
            raise ValueError(f"{name}.reason must be a string")
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or any(
            not isinstance(quote, str) for quote in evidence
        ):
            raise ValueError(
                f"{name}.evidence must be a list of strings"
            )
        out.append(
            DimensionVerdict(
                dimension=name,
                verdict=str(verdict),
                reason=reason,
                evidence=tuple(evidence),
            )
        )
    return tuple(out)


def _redact(text: str, secret: str) -> str:
    return redact(text, (secret,)) if secret else text


def _redact_verdict(
    verdict: JudgeVerdict, secret: str
) -> JudgeVerdict:
    if not secret:
        return verdict
    return replace(
        verdict,
        dimensions=tuple(
            replace(
                item,
                reason=_redact(item.reason, secret),
                evidence=tuple(
                    _redact(quote, secret) for quote in item.evidence
                ),
            )
            for item in verdict.dimensions
        ),
        raw=_redact(verdict.raw, secret),
        error=_redact(verdict.error, secret),
    )


__all__ = [
    "DIMENSIONS",
    "JUDGE_TEMPERATURE",
    "DimensionVerdict",
    "JudgeClient",
    "JudgeVerdict",
    "build_user_prompt",
    "conversation_from",
    "global_prompt_sha256",
    "judge_case",
    "judge_config",
    "make_judge_client",
    "parse_verdict",
    "prompt_digest",
    "render_requirements",
]
