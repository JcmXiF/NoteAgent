"""Grade one V3 quality item with the shared rubric, content only.

The judge sees the seeded wiki, the wiki's purpose and index when the item has
them, the pages the agent actually read, the conversation, and the shared
rubric's rules. The dimension rules live in ``rubric.yaml`` only; the prompt
carries the grading protocol. The judge never sees the tool trace: the
tool/trace assertions are diagnostics, not part of the verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from evals.quality.judge_client import DimensionVerdict, JudgeClient
from evals.quality.v3.set import Rubric

from noteagent.llm.client import ChatMessage
from noteagent.util.redact import redact

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
EXPECTED_KEYS = ("grounding", "completeness", "uncertainty", "hallucination")


@dataclass(frozen=True, slots=True)
class V3JudgeVerdict:
    """One verdict per dimension, or why the judge was unusable."""

    dimensions: tuple[DimensionVerdict, ...] = ()
    error: str = ""
    skipped: str = ""
    raw: str = ""
    prompt_sha256: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

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
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def judge_item(
    rubric: Rubric,
    *,
    question: str,
    wiki: tuple[tuple[str, str, str], ...],
    conversation: tuple[tuple[str, str], ...],
    expected_points: tuple[str, ...],
    pitfalls: tuple[str, ...],
    client: JudgeClient,
    read_pages: tuple[str, ...] = (),
    wiki_index: tuple[str, ...] = (),
    purpose: str = "",
    secret: str = "",
) -> V3JudgeVerdict:
    """Ask the judge for one verdict per rubric dimension of one item."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    message = _user_message(
        rubric,
        question=question,
        wiki=wiki,
        conversation=conversation,
        expected_points=expected_points,
        pitfalls=pitfalls,
        read_pages=read_pages,
        wiki_index=wiki_index,
        purpose=purpose,
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
        return V3JudgeVerdict(
            error=f"judge_error: {err}",
            prompt_sha256=prompt_sha256,
            input_tokens=getattr(err, "input_tokens", 0),
            output_tokens=getattr(err, "output_tokens", 0),
        )
    raw = redact(
        response.content or "", (secret,) if secret else ()
    )
    verdict = _parse(
        raw, keys=rubric.verdict_keys(), prompt_sha256=prompt_sha256
    )
    return replace(
        verdict,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )


def _user_message(
    rubric: Rubric,
    *,
    question: str,
    wiki: tuple[tuple[str, str, str], ...],
    conversation: tuple[tuple[str, str], ...],
    expected_points: tuple[str, ...],
    pitfalls: tuple[str, ...],
    read_pages: tuple[str, ...],
    wiki_index: tuple[str, ...],
    purpose: str,
) -> str:
    lines = ["## Question", question]
    if purpose.strip():
        lines.extend(["", "## Wiki purpose", purpose.strip()])
    if wiki_index:
        lines.extend(["", "## Wiki index"])
        lines.extend(f"- {item}" for item in wiki_index)
    lines.extend(["", "## Wiki pages"])
    for slug, title, body in wiki:
        lines.extend(["", f"### {slug} — {title}", body])
    lines.extend(["", "## Pages the agent read"])
    read = tuple(dict.fromkeys(read_pages))
    if read:
        lines.extend(f"- {slug}" for slug in read)
    else:
        lines.append("- none")
    lines.extend(["", "## Conversation"])
    for role, text in conversation:
        lines.extend(["", f"### {role}", text])
    lines.extend(["", "## Expected points"])
    lines.extend(f"- {point}" for point in expected_points)
    if pitfalls:
        lines.extend(["", "## Pitfalls"])
        lines.extend(f"- {pitfall}" for pitfall in pitfalls)
    lines.extend(["", "## Rubric"])
    for dimension in rubric.graded_dimensions():
        lines.extend(["", f"### {dimension.name} ({dimension.weight})"])
        lines.extend(_rules("Must", dimension.must))
        lines.extend(_rules("Must not", dimension.must_not))
    lines.extend(
        [
            "",
            "## Verdict format",
            "",
            "Return only the JSON object below, with no code fences and no "
            "extra keys:",
            "",
            _verdict_shape(rubric),
        ]
    )
    return "\n".join(lines)


def _rules(label: str, rules: tuple[str, ...]) -> list[str]:
    if not rules:
        return []
    return [f"{label}:", *(f"- {rule}" for rule in rules)]


def _verdict_shape(rubric: Rubric) -> str:
    body = ",\n".join(
        f'  "{key}": {{"verdict": "pass", "reason": "...", '
        '"evidence": ["..."]}'
        for key in rubric.verdict_keys()
    )
    return "{\n" + body + "\n}"


def _parse(
    raw: str, *, keys: tuple[str, ...], prompt_sha256: str
) -> V3JudgeVerdict:
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
    missing = [key for key in keys if key not in payload]
    if missing:
        return V3JudgeVerdict(
            error=(
                "judge_error: missing "
                f"{', '.join(missing)}; got {', '.join(sorted(payload))}"
            ),
            raw=raw,
            prompt_sha256=prompt_sha256,
        )
    dimensions: list[DimensionVerdict] = []
    for name in keys:
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
    "judge_item",
]
