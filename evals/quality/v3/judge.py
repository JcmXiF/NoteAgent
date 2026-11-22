"""Grade one V3 quality item with the shared rubric, content only.

The judge sees the seeded wiki, the conversation, the shared rubric and the
question's expected points and pitfalls. It never sees the tool trace: the
tool/trace assertions are diagnostics, not part of the verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.quality.judge import (
    DimensionVerdict,
    JudgeClient,
    conversation_from,
    judge_config,
    make_judge_client,
)
from evals.quality.v3.set import Rubric

from noteagent.llm.client import ChatMessage, LlmTurn
from noteagent.llm.config import LlmConfig
from noteagent.util.redact import redact

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
EXPECTED_KEYS = ("grounding", "completeness", "uncertainty", "hallucination")


@dataclass(frozen=True, slots=True)
class V3JudgeVerdict:
    """The judge's four verdicts, or the reason it could not be used."""

    dimensions: tuple[DimensionVerdict, ...] = ()
    error: str = ""
    skipped: str = ""
    raw: str = ""
    prompt_sha256: str = ""

    def verdicts(self) -> dict[str, str]:
        if self.error or self.skipped:
            return {}
        return {item.dimension: item.verdict for item in self.dimensions}

    def passed(self, rubric: Rubric) -> bool:
        if self.error or self.skipped:
            return False
        return rubric.passes(self.verdicts())

    def to_payload(self) -> dict[str, Any]:
        return {
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


def judge_item(
    rubric: Rubric,
    *,
    question: str,
    wiki: tuple[tuple[str, str], ...],
    conversation: tuple[tuple[str, str], ...],
    expected_points: tuple[str, ...],
    pitfalls: tuple[str, ...],
    client: JudgeClient,
    secret: str = "",
) -> V3JudgeVerdict:
    """Ask the judge for the four content verdicts of one item."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    message = _user_message(
        question=question,
        wiki=wiki,
        conversation=conversation,
        expected_points=expected_points,
        pitfalls=pitfalls,
    )
    try:
        response = client.complete(
            [
                ChatMessage("system", prompt),
                ChatMessage("user", message),
            ],
            tools=None,
            fallback_reasoning=False,
            response_format={"type": "json_object"},
        )
    except Exception as err:  # noqa: BLE001 - recorded for the report
        return V3JudgeVerdict(error=f"judge_error: {err}")
    raw = redact(
        response.content or "", (secret,) if secret else ()
    )
    return _parse(
        raw,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    )


def _user_message(
    *,
    question: str,
    wiki: tuple[tuple[str, str], ...],
    conversation: tuple[tuple[str, str], ...],
    expected_points: tuple[str, ...],
    pitfalls: tuple[str, ...],
) -> str:
    lines = ["## Wiki"]
    for title, body in wiki:
        lines.extend(["", f"### {title}", body])
    lines.extend(["", "## Conversation"])
    for role, text in conversation:
        lines.extend(["", f"### {role}", text])
    lines.extend(["", "## Question", question])
    lines.extend(["", "## Expected points"])
    lines.extend(f"- {point}" for point in expected_points)
    if pitfalls:
        lines.extend(["", "## Pitfalls"])
        lines.extend(f"- {pitfall}" for pitfall in pitfalls)
    return "\n".join(lines)


def _parse(raw: str, *, prompt_sha256: str) -> V3JudgeVerdict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        return V3JudgeVerdict(
            error=f"judge_error: invalid JSON: {err}",
            raw=raw,
            prompt_sha256=prompt_sha256,
        )
    if not isinstance(payload, dict):
        return V3JudgeVerdict(
            error="judge_error: the verdict must be an object",
            raw=raw,
            prompt_sha256=prompt_sha256,
        )
    if set(payload) != set(EXPECTED_KEYS):
        return V3JudgeVerdict(
            error=(
                "judge_error: expected "
                f"{', '.join(EXPECTED_KEYS)}, got {', '.join(sorted(payload))}"
            ),
            raw=raw,
            prompt_sha256=prompt_sha256,
        )
    dimensions: list[DimensionVerdict] = []
    for name in EXPECTED_KEYS:
        item = payload.get(name)
        if not isinstance(item, dict):
            return V3JudgeVerdict(
                error=f"judge_error: {name} must be an object",
                raw=raw,
                prompt_sha256=prompt_sha256,
            )
        verdict = str(item.get("verdict") or "")
        if verdict not in ("pass", "fail"):
            return V3JudgeVerdict(
                error=f"judge_error: {name}.verdict must be pass or fail",
                raw=raw,
                prompt_sha256=prompt_sha256,
            )
        evidence = item.get("evidence") or []
        dimensions.append(
            DimensionVerdict(
                name,
                verdict,
                str(item.get("reason") or ""),
                tuple(
                    str(quote)
                    for quote in evidence
                    if isinstance(quote, str)
                ),
            )
        )
    return V3JudgeVerdict(
        tuple(dimensions), raw=raw, prompt_sha256=prompt_sha256
    )


__all__ = [
    "EXPECTED_KEYS",
    "PROMPT_PATH",
    "V3JudgeVerdict",
    "conversation_from",
    "judge_config",
    "judge_item",
    "make_judge_client",
]
