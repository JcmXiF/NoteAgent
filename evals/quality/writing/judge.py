"""Grade one written page with a second model, against the case's rules."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.quality.judge import JudgeClient
from evals.quality.writing.cases import WritingCase
from noteagent.llm.client import ChatMessage
from noteagent.util.redact import redact

DIMENSIONS = ("coverage", "citations", "structure", "coherence")
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
MAX_RAW = 4_000


@dataclass(frozen=True, slots=True)
class DimensionVerdict:
    dimension: str
    verdict: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class WritingJudge:
    """The judge's four dimensions, or why it could not be used."""

    dimensions: tuple[DimensionVerdict, ...] = ()
    error: str = ""
    raw: str = ""
    prompt_sha256: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and len(self.dimensions) == len(DIMENSIONS)
            and all(item.verdict == "pass" for item in self.dimensions)
        )

    def score(self) -> int:
        """How many dimensions passed (0–4), for the report."""
        return sum(
            1 for item in self.dimensions if item.verdict == "pass"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": self.score(),
            "error": self.error,
            "promptSha256": self.prompt_sha256,
            "dimensions": [
                {
                    "dimension": item.dimension,
                    "verdict": item.verdict,
                    "reason": item.reason,
                }
                for item in self.dimensions
            ],
        }


def _rules_text(case: WritingCase) -> str:
    lines: list[str] = []
    for dimension in DIMENSIONS:
        rule = case.rules.get(dimension) or {}
        must = rule.get("must") or ()
        must_not = rule.get("must_not") or ()
        lines.append(f"## {dimension}")
        lines.extend(f"- must: {item}" for item in must)
        lines.extend(f"- must not: {item}" for item in must_not)
    return "\n".join(lines)


def _sources_text(case: WritingCase) -> str:
    parts = []
    for page in case.sources:
        parts.append(f"### {page.title} ({page.url})")
        parts.append(page.text())
    return "\n".join(parts)


def judge_page(
    case: WritingCase,
    page: str,
    *,
    client: JudgeClient,
    secret: str = "",
) -> WritingJudge:
    """Ask the judge for the four dimensions; never guess a verdict."""
    prompt_sha = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    content = (
        f"Case: {case.title}\n\n"
        f"## Sources\n{_sources_text(case)}\n\n"
        f"## Case rules\n{_rules_text(case)}\n\n"
        f"## Page\n{page.strip() or '(empty)'}\n"
    )
    try:
        turn = client.complete(
            [
                ChatMessage("system", PROMPT),
                ChatMessage("user", content),
            ],
            response_format={"type": "json_object"},
        )
        raw = _redacted(str(turn.content or ""), secret)
    except Exception as err:  # noqa: BLE001 - the report keeps the reason
        return WritingJudge(
            error=f"judge call failed: {_redacted(str(err), secret)}",
            prompt_sha256=prompt_sha,
        )
    try:
        payload = _parse(raw)
    except ValueError as err:
        return WritingJudge(
            error=str(err),
            raw=raw[:MAX_RAW],
            prompt_sha256=prompt_sha,
        )
    dimensions: list[DimensionVerdict] = []
    for dimension in DIMENSIONS:
        item = payload.get(dimension)
        if not isinstance(item, dict):
            return WritingJudge(
                error=f"the judge omitted {dimension}",
                raw=raw[:MAX_RAW],
                prompt_sha256=prompt_sha,
            )
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"pass", "fail"}:
            return WritingJudge(
                error=f"the judge gave {dimension} no verdict",
                raw=raw[:MAX_RAW],
                prompt_sha256=prompt_sha,
            )
        dimensions.append(
            DimensionVerdict(
                dimension=dimension,
                verdict=verdict,
                reason=str(item.get("reason") or "")[:500],
            )
        )
    return WritingJudge(
        dimensions=tuple(dimensions),
        raw=raw[:MAX_RAW],
        prompt_sha256=prompt_sha,
    )


def _redacted(text: str, secret: str) -> str:
    return redact(text, [secret]) if secret else text


def _parse(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the judge did not answer JSON")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as err:
        raise ValueError(f"the judge's JSON is invalid: {err}") from err
    if not isinstance(payload, dict):
        raise ValueError("the judge's answer is not an object")
    return payload


__all__ = [
    "DIMENSIONS",
    "DimensionVerdict",
    "WritingJudge",
    "judge_page",
]
