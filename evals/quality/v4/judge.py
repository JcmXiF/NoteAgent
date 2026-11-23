"""The V4 judge: one artifact, five points, evidence, no overall verdict.

The judge sees only the task, the allowed material originals, the expected
points, the artifact and the deterministic task-compliance result. It never
sees a reference answer, the calibration labels, the whole wiki index, failed
read attempts or the tool trace.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from evals.quality.judge_client import JudgeClient

from noteagent.llm.client import ChatMessage
from noteagent.util.redact import redact

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"
POINT_IDS = (
    "correctness",
    "completeness",
    "source_legality",
    "task_compliance",
    "honesty",
)
FAILURE_KINDS = frozenset(
    {
        "unsupported_claim",
        "wrong_fact",
        "missing_point",
        "illegal_source",
        "invented_citation",
        "shape",
        "hidden_gap",
        "unlabeled_derivation",
        "other",
    }
)
_ALLOWED_KEYS = frozenset({"points", "expected_points", "failures"})


@dataclass(frozen=True, slots=True)
class JudgeMaterial:
    """One allowed original, named by the locator the artifact must cite."""

    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class TaskCompliance:
    """The deterministic shape result the judge must not re-infer."""

    passed: bool
    checks: tuple[str, ...] = ()

    def render(self) -> str:
        lines = ["pass" if self.passed else "fail"]
        lines.extend(f"- {check}" for check in self.checks)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class JudgeEvidence:
    artifact: str
    material: str


@dataclass(frozen=True, slots=True)
class JudgePoint:
    id: str
    verdict: str
    reason: str
    evidence: tuple[JudgeEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpectedPointVerdict:
    point: str
    covered: bool
    artifact_quote: str = ""


@dataclass(frozen=True, slots=True)
class JudgeFailure:
    point: str
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class V4JudgeVerdict:
    """Five point verdicts, or why the judge was unusable."""

    points: tuple[JudgePoint, ...] = ()
    expected_points: tuple[ExpectedPointVerdict, ...] = ()
    failures: tuple[JudgeFailure, ...] = ()
    error: str = ""
    raw: str = ""
    prompt_sha256: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def verdicts(self) -> dict[str, str]:
        if self.error:
            return {}
        return {point.id: point.verdict for point in self.points}

    def all_points_pass(self) -> bool:
        """Every point passed; this is a summary, never the judge's verdict."""
        return (
            not self.error
            and len(self.points) == len(POINT_IDS)
            and all(point.verdict == "pass" for point in self.points)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "points": [
                {
                    "id": point.id,
                    "verdict": point.verdict,
                    "reason": point.reason,
                    "evidence": [
                        {
                            "artifact": item.artifact,
                            "material": item.material,
                        }
                        for item in point.evidence
                    ],
                }
                for point in self.points
            ],
            "expected_points": [
                {
                    "point": item.point,
                    "covered": item.covered,
                    "artifact_quote": item.artifact_quote,
                }
                for item in self.expected_points
            ],
            "failures": [
                {
                    "point": item.point,
                    "kind": item.kind,
                    "detail": item.detail,
                }
                for item in self.failures
            ],
            "error": self.error,
            "raw": self.raw,
            "prompt_sha256": self.prompt_sha256,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def judge_artifact(
    *,
    task: str,
    materials: tuple[JudgeMaterial, ...],
    expected_points: tuple[str, ...],
    artifact_id: str,
    artifact: str,
    task_compliance: TaskCompliance,
    client: JudgeClient,
    secret: str = "",
) -> V4JudgeVerdict:
    """Ask the judge for one verdict per point of one artifact."""
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    message = build_user_message(
        task=task,
        materials=materials,
        expected_points=expected_points,
        artifact_id=artifact_id,
        artifact=artifact,
        task_compliance=task_compliance,
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
        return V4JudgeVerdict(
            error=f"judge_error: {err}",
            prompt_sha256=prompt_sha256,
            input_tokens=getattr(err, "input_tokens", 0),
            output_tokens=getattr(err, "output_tokens", 0),
        )
    raw = redact(
        response.content or "", (secret,) if secret else ()
    )
    verdict = _parse(
        raw,
        expected_count=len(expected_points),
        prompt_sha256=prompt_sha256,
    )
    return replace(
        verdict,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )


def build_user_message(
    *,
    task: str,
    materials: tuple[JudgeMaterial, ...],
    expected_points: tuple[str, ...],
    artifact_id: str,
    artifact: str,
    task_compliance: TaskCompliance,
) -> str:
    """The one user message: task, allowed originals, artifact, checks."""
    lines = ["## Task", task.strip(), "", "## Allowed material"]
    for material in materials:
        lines.extend(
            [
                "",
                f"### {material.id} — {material.title}",
                material.text.strip(),
            ]
        )
    lines.extend(["", "## Expected points"])
    lines.extend(f"- {point}" for point in expected_points)
    lines.extend(
        [
            "",
            "## Artifact",
            f"### {artifact_id}",
            artifact.strip(),
            "",
            "## Deterministic task-compliance",
            task_compliance.render(),
        ]
    )
    return "\n".join(lines)


def _parse(
    raw: str, *, expected_count: int, prompt_sha256: str
) -> V4JudgeVerdict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as err:
        return _error(f"judge_error: invalid JSON: {err}", raw, prompt_sha256)
    if not isinstance(payload, dict):
        return _error(
            "judge_error: the verdict must be an object",
            raw,
            prompt_sha256,
        )
    extra = set(payload) - _ALLOWED_KEYS
    if extra:
        return _error(
            "judge_error: unexpected keys: "
            + ", ".join(sorted(str(key) for key in extra)),
            raw,
            prompt_sha256,
        )
    missing = [key for key in _ALLOWED_KEYS if key not in payload]
    if missing:
        return _error(
            "judge_error: missing " + ", ".join(sorted(missing)),
            raw,
            prompt_sha256,
        )
    points, points_error = _points(payload.get("points"))
    if points_error:
        return _error(points_error, raw, prompt_sha256)
    expected, expected_error = _expected_points(
        payload.get("expected_points"), expected_count
    )
    if expected_error:
        return _error(expected_error, raw, prompt_sha256)
    failures, failures_error = _failures(
        payload.get("failures"), points
    )
    if failures_error:
        return _error(failures_error, raw, prompt_sha256)
    return V4JudgeVerdict(
        points=points,
        expected_points=expected,
        failures=failures,
        raw=raw,
        prompt_sha256=prompt_sha256,
    )


def _points(value: Any) -> tuple[tuple[JudgePoint, ...], str]:
    if not isinstance(value, list):
        return (), "judge_error: points must be a list"
    by_id: dict[str, JudgePoint] = {}
    for item in value:
        if not isinstance(item, dict):
            return (), "judge_error: a point must be an object"
        ident = str(item.get("id") or "")
        if ident not in POINT_IDS:
            return (), f"judge_error: unknown point id: {ident!r}"
        if ident in by_id:
            return (), f"judge_error: duplicate point: {ident}"
        verdict = str(item.get("verdict") or "")
        if verdict not in ("pass", "fail"):
            return (), (
                f"judge_error: {ident}.verdict must be pass or fail"
            )
        reason = str(item.get("reason") or "").strip()
        if not reason:
            return (), f"judge_error: {ident}.reason is empty"
        evidence, evidence_error = _evidence(item.get("evidence"), ident)
        if evidence_error:
            return (), evidence_error
        if not evidence:
            return (), f"judge_error: {ident} has no evidence"
        if verdict == "pass" and not any(
            entry.artifact.strip() for entry in evidence
        ):
            return (), (
                f"judge_error: {ident} passed without an artifact quote"
            )
        by_id[ident] = JudgePoint(
            id=ident,
            verdict=verdict,
            reason=reason,
            evidence=evidence,
        )
    missing = [ident for ident in POINT_IDS if ident not in by_id]
    if missing:
        return (), (
            "judge_error: missing points: " + ", ".join(missing)
        )
    return tuple(by_id[ident] for ident in POINT_IDS), ""


def _evidence(
    value: Any, point: str
) -> tuple[tuple[JudgeEvidence, ...], str]:
    if not isinstance(value, list):
        return (), f"judge_error: {point}.evidence must be a list"
    items: list[JudgeEvidence] = []
    for entry in value:
        if not isinstance(entry, dict):
            return (), (
                f"judge_error: {point}.evidence entries must be objects"
            )
        artifact = str(entry.get("artifact") or "").strip()
        material = str(entry.get("material") or "").strip()
        if not artifact:
            return (), (
                f"judge_error: {point}.evidence needs an artifact quote"
            )
        if not material:
            return (), (
                f"judge_error: {point}.evidence needs a material quote"
            )
        items.append(JudgeEvidence(artifact, material))
    return tuple(items), ""


def _expected_points(
    value: Any, expected_count: int
) -> tuple[tuple[ExpectedPointVerdict, ...], str]:
    if not isinstance(value, list):
        return (), "judge_error: expected_points must be a list"
    items: list[ExpectedPointVerdict] = []
    for entry in value:
        if not isinstance(entry, dict):
            return (), (
                "judge_error: an expected point must be an object"
            )
        point = str(entry.get("point") or "").strip()
        covered = entry.get("covered")
        if not point:
            return (), "judge_error: an expected point is empty"
        if not isinstance(covered, bool):
            return (), (
                f"judge_error: {point}.covered must be true or false"
            )
        items.append(
            ExpectedPointVerdict(
                point=point,
                covered=covered,
                artifact_quote=str(
                    entry.get("artifact_quote") or ""
                ).strip(),
            )
        )
    if len(items) != expected_count:
        return (), (
            "judge_error: expected_points has "
            f"{len(items)} entries, the task has {expected_count}"
        )
    return tuple(items), ""


def _failures(
    value: Any, points: tuple[JudgePoint, ...]
) -> tuple[tuple[JudgeFailure, ...], str]:
    if not isinstance(value, list):
        return (), "judge_error: failures must be a list"
    items: list[JudgeFailure] = []
    for entry in value:
        if not isinstance(entry, dict):
            return (), (
                "judge_error: a failure must be an object"
            )
        point = str(entry.get("point") or "")
        if point not in POINT_IDS:
            return (), (
                f"judge_error: unknown failure point: {point!r}"
            )
        kind = str(entry.get("kind") or "")
        if kind not in FAILURE_KINDS:
            return (), (
                f"judge_error: unknown failure kind: {kind!r}"
            )
        detail = str(entry.get("detail") or "").strip()
        if not detail:
            return (), "judge_error: a failure needs a detail"
        items.append(JudgeFailure(point, kind, detail))
    failed = {
        point.id for point in points if point.verdict == "fail"
    }
    covered = {item.point for item in items}
    missing = sorted(failed - covered)
    if missing:
        return (), (
            "judge_error: no failure entry for "
            + ", ".join(missing)
        )
    unearned = sorted(covered - failed)
    if unearned:
        return (), (
            "judge_error: failure entry for a passing point: "
            + ", ".join(unearned)
        )
    return tuple(items), ""


def _error(
    message: str, raw: str, prompt_sha256: str
) -> V4JudgeVerdict:
    return V4JudgeVerdict(
        error=message, raw=raw, prompt_sha256=prompt_sha256
    )


__all__ = [
    "FAILURE_KINDS",
    "POINT_IDS",
    "PROMPT_PATH",
    "ExpectedPointVerdict",
    "JudgeEvidence",
    "JudgeFailure",
    "JudgeMaterial",
    "JudgePoint",
    "TaskCompliance",
    "V4JudgeVerdict",
    "build_user_message",
    "judge_artifact",
]
