"""G-Eval: one 1–5 score per text dimension, weighted by the score token.

Each dimension is a separate judge call with a fixed rubric and anchors. The
judge writes its steps, then a final ``Score: <n>`` line. The score token's
top log probabilities are normalised over 1–5 and turned into a soft score;
when the provider does not expose logprobs, the discrete score is used.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from evals.golden.dataset import CorpusPage, GoldenQuery

from noteagent.llm.client import ChatMessage, LlmTurn, TokenLogprob
from noteagent.llm.config import LlmConfig
from noteagent.util.redact import redact

DIMENSIONS = ("grounding", "coverage", "uncertainty")
PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "geval"
#: The judge is a reasoning model: its thinking tokens count against the
#: completion budget, so 4096 can end the turn before the score line exists.
JUDGE_MAX_TOKENS = 16_384
_SCORES = {"1", "2", "3", "4", "5"}


class ScoreClient(Protocol):
    """The one method a judge transport has to offer."""

    def complete(self, messages: list[ChatMessage], **kwargs: Any) -> LlmTurn:
        ...


@dataclass(frozen=True, slots=True)
class DimensionScore:
    """One dimension's discrete and probability-weighted score."""

    dimension: str
    score: int
    soft: float
    reason: str
    raw: str
    error: str = ""

    def to_payload(self, *, include_raw: bool = False) -> dict[str, object]:
        body: dict[str, object] = {
            "dimension": self.dimension,
            "score": self.score,
            "soft": round(self.soft, 4),
            "reason": self.reason,
            "error": self.error,
        }
        if include_raw:
            body["raw"] = self.raw
        return body


@dataclass(frozen=True, slots=True)
class GevalVerdict:
    """The three dimension scores, or the reason the judge could not finish."""

    scores: tuple[DimensionScore, ...] = ()
    error: str = ""
    prompt_sha256: str = ""

    @property
    def means(self) -> dict[str, float]:
        return {
            item.dimension: item.soft
            for item in self.scores
            if not item.error
        }

    @property
    def ok(self) -> bool:
        """Every dimension produced a usable score."""
        return (
            not self.error
            and len(self.scores) == len(DIMENSIONS)
            and all(not item.error for item in self.scores)
        )

    def to_payload(self, *, include_raw: bool = False) -> dict[str, object]:
        return {
            "ok": self.ok,
            "scores": [
                item.to_payload(include_raw=include_raw)
                for item in self.scores
            ],
            "error": self.error,
            "promptSha256": self.prompt_sha256,
        }


def judge_config(config: LlmConfig) -> LlmConfig:
    """The judge samples at temperature 0 with room for its steps."""
    return replace(config, temperature=0.0, max_tokens=JUDGE_MAX_TOKENS)


def judge(
    case: GoldenQuery,
    answer: str,
    pages: Sequence[CorpusPage],
    *,
    client: ScoreClient,
    secret: str = "",
    wiki_index: Sequence[str] = (),
    purpose: str = "",
) -> GevalVerdict:
    """Grade *answer* on the three dimensions, one call each."""
    prompts = {
        name: (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
        for name in DIMENSIONS
    }
    digest = prompt_digest(
        prompts, case, wiki_index=wiki_index, purpose=purpose
    )
    scores: list[DimensionScore] = []
    for name in DIMENSIONS:
        messages = [
            ChatMessage("system", prompts[name]),
            ChatMessage(
                "user",
                build_user_prompt(
                    case,
                    answer,
                    pages,
                    name,
                    wiki_index=wiki_index,
                    purpose=purpose,
                ),
            ),
        ]
        try:
            turn = client.complete(
                messages,
                logprobs=True,
                top_logprobs=20,
                fallback_reasoning=False,
            )
        except Exception as err:  # noqa: BLE001 - the case records the failure
            return GevalVerdict(
                scores=tuple(scores),
                error=f"geval_error: {_clean(str(err), secret)}",
                prompt_sha256=digest,
            )
        scores.append(
            _clean_score(
                parse_score(
                    turn.content, turn.logprobs, name, turn.reasoning
                ),
                secret,
            )
        )
    return GevalVerdict(
        scores=tuple(scores), prompt_sha256=digest
    )


def build_user_prompt(
    case: GoldenQuery,
    answer: str,
    pages: Sequence[CorpusPage],
    dimension: str,
    *,
    wiki_index: Sequence[str] = (),
    purpose: str = "",
) -> str:
    parts = ["## Question", case.question]
    if purpose:
        parts.extend(["## Wiki purpose", purpose])
    if wiki_index:
        parts.append("## Wiki index")
        parts.extend(f"- {item}" for item in wiki_index)
    parts.append("## Wiki pages")
    for page in pages:
        parts.extend([f"### {page.slug} — {page.title}", page.body])
    if dimension == "coverage":
        parts.append("## Key points")
        parts.extend(f"- {point}" for point in case.key_points)
    if dimension == "uncertainty":
        parts.append("## Forbidden")
        parts.extend(f"- {item}" for item in case.forbidden)
    parts.extend(["## Answer", answer])
    return "\n\n".join(parts)


def shared_prompt_sha256() -> str:
    """The three fixed judge instructions, without any case rules."""
    digest = hashlib.sha256()
    for name in DIMENSIONS:
        digest.update((PROMPT_DIR / f"{name}.md").read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def prompt_digest(
    prompts: dict[str, str],
    case: GoldenQuery,
    *,
    wiki_index: Sequence[str] = (),
    purpose: str = "",
) -> str:
    """The instructions and the case rules the judge read."""
    digest = hashlib.sha256()
    for name in DIMENSIONS:
        digest.update(prompts[name].encode("utf-8"))
        digest.update(b"\0")
    for value in (
        case.question,
        *case.key_points,
        *case.forbidden,
        purpose,
        *wiki_index,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def parse_score(
    content: str,
    logprobs: Sequence[TokenLogprob],
    dimension: str,
    reasoning: str = "",
) -> DimensionScore:
    """Read the final 1–5 score, weighting it by the score token's top logprobs."""
    chosen: int | None = None
    index: int | None = None
    for position, item in enumerate(logprobs):
        token = item.token.strip()
        if token in _SCORES:
            chosen = int(token)
            index = position

    if chosen is None or index is None:
        matches = re.findall(r"(?<!\d)([1-5])(?!\d)", content)
        if not matches:
            return DimensionScore(
                dimension,
                0,
                0.0,
                _reason(content, reasoning),
                content,
                "geval_error: the judge did not return a 1-5 score",
            )
        discrete = int(matches[-1])
        return DimensionScore(
            dimension,
            discrete,
            float(discrete),
            _reason(content, reasoning),
            content,
        )

    item = logprobs[index]
    probabilities: dict[int, float] = {}
    for token, logprob in item.top_logprobs:
        name = token.strip()
        if name in _SCORES:
            probabilities[int(name)] = math.exp(logprob)
    probabilities.setdefault(chosen, math.exp(item.logprob))
    total = sum(probabilities.values())
    soft = (
        sum(score * probability for score, probability in probabilities.items())
        / total
        if total
        else float(chosen)
    )
    return DimensionScore(
        dimension,
        chosen,
        soft,
        _reason(content, reasoning),
        content,
    )


def _reason(content: str, reasoning: str = "") -> str:
    """The judge's steps: what it wrote before ``Score``, or its reasoning."""
    if "Score" in content:
        steps = content.rsplit("Score", 1)[0].strip()
        return steps or reasoning.strip()
    return content.strip() or reasoning.strip()


def _clean(text: str, secret: str) -> str:
    return redact(text, (secret,)) if secret else text


def _clean_score(
    score: DimensionScore, secret: str
) -> DimensionScore:
    if not secret:
        return score
    return replace(
        score,
        reason=_clean(score.reason, secret),
        raw=_clean(score.raw, secret),
        error=_clean(score.error, secret),
    )


__all__ = [
    "DIMENSIONS",
    "DimensionScore",
    "GevalVerdict",
    "JUDGE_MAX_TOKENS",
    "PROMPT_DIR",
    "ScoreClient",
    "build_user_prompt",
    "judge",
    "judge_config",
    "parse_score",
    "prompt_digest",
    "shared_prompt_sha256",
]
