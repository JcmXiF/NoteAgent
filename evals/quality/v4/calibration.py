"""Calibrate the V4 judge against every case's signed samples.

The labels are the human reference: they are compared after the judge answers
and never placed in its prompt. A mismatch points at the rubric or the prompt,
not at the sample.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from evals.quality.judge_client import JudgeClient
from evals.quality.v4.case_spec import (
    QualityCase,
    fixture_task_compliance,
)
from evals.quality.v4.judge import (
    POINT_IDS,
    V4JudgeVerdict,
    judge_artifact,
)
from evals.report import markdown_cell


@dataclass(frozen=True, slots=True)
class PointMatch:
    """One point's human label and the judge's answer."""

    point: str
    expected: str
    actual: str
    reason: str = ""

    @property
    def matches(self) -> bool:
        return self.expected == self.actual


@dataclass(frozen=True, slots=True)
class SampleCalibration:
    """One sample's point-by-point agreement."""

    sample: str
    verdict: V4JudgeVerdict
    points: tuple[PointMatch, ...]

    @property
    def matched(self) -> bool:
        return not self.verdict.error and all(
            point.matches for point in self.points
        )

    @property
    def mismatches(self) -> tuple[PointMatch, ...]:
        return tuple(point for point in self.points if not point.matches)

    def to_payload(self) -> dict[str, object]:
        return {
            "sample": self.sample,
            "matched": self.matched,
            "points": [
                {
                    "point": point.point,
                    "expected": point.expected,
                    "actual": point.actual,
                    "matches": point.matches,
                    "reason": point.reason,
                }
                for point in self.points
            ],
            "judge": self.verdict.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """One case's samples, their agreement, and the judge's usage."""

    case_id: str
    samples: tuple[SampleCalibration, ...]
    duration_s: float

    @property
    def matched(self) -> bool:
        return bool(self.samples) and all(
            sample.matched for sample in self.samples
        )

    @property
    def matched_count(self) -> int:
        return sum(1 for sample in self.samples if sample.matched)

    @property
    def tokens_in(self) -> int:
        return sum(sample.verdict.input_tokens for sample in self.samples)

    @property
    def tokens_out(self) -> int:
        return sum(
            sample.verdict.output_tokens for sample in self.samples
        )

    def payload(self) -> dict[str, object]:
        return {
            "case": self.case_id,
            "matched": self.matched,
            "samplesMatched": self.matched_count,
            "samples": len(self.samples),
            "durationSeconds": round(self.duration_s, 3),
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "results": [
                sample.to_payload() for sample in self.samples
            ],
        }

    def markdown(self) -> str:
        lines = [
            f"# V4 judge calibration — case {self.case_id}",
            "",
            (
                f"- Samples matched: **{self.matched_count}"
                f"/{len(self.samples)}**"
            ),
            f"- Duration: {self.duration_s:.1f}s",
            f"- Tokens in/out: {self.tokens_in}/{self.tokens_out}",
            "",
            "| Sample | Point | Label | Judge | Match | Reason |",
            "|---|---|---|---|---|---|",
        ]
        for sample in self.samples:
            for point in sample.points:
                lines.append(
                    f"| `{sample.sample}` | {point.point} | "
                    f"{point.expected} | {point.actual or '-'} | "
                    f"{'yes' if point.matches else 'NO'} | "
                    f"{markdown_cell(point.reason)} |"
                )
        for sample in self.samples:
            if sample.verdict.error:
                lines.extend(
                    [
                        "",
                        f"## `{sample.sample}` judge error",
                        "",
                        "```",
                        sample.verdict.error,
                        "```",
                    ]
                )
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        return path


def calibrate_case(
    case: QualityCase,
    client: JudgeClient,
    *,
    secret: str = "",
) -> CalibrationReport:
    """Judge every sample of one case and compare each point to its label."""
    started = time.monotonic()
    samples: list[SampleCalibration] = []
    for sample in case.samples:
        verdict = judge_artifact(
            task=case.task,
            materials=case.judge_materials(),
            expected_points=case.expected_points,
            artifact_id=sample.name,
            artifact=sample.artifact,
            task_compliance=fixture_task_compliance(),
            client=client,
            secret=secret,
            scope_note=case.scope_note,
        )
        actual = verdict.verdicts()
        points = tuple(
            PointMatch(
                point=point,
                expected=sample.labels.get(point, ""),
                actual=actual.get(point, ""),
                reason=next(
                    (
                        item.reason
                        for item in verdict.points
                        if item.id == point
                    ),
                    verdict.error,
                ),
            )
            for point in POINT_IDS
        )
        samples.append(SampleCalibration(sample.name, verdict, points))
    return CalibrationReport(
        case_id=case.id,
        samples=tuple(samples),
        duration_s=time.monotonic() - started,
    )


def calibrate_cases(
    cases: tuple[QualityCase, ...],
    client: JudgeClient,
    *,
    secret: str = "",
) -> tuple[CalibrationReport, ...]:
    """Calibrate every case with one judge transport."""
    return tuple(
        calibrate_case(case, client, secret=secret) for case in cases
    )


__all__ = [
    "CalibrationReport",
    "PointMatch",
    "SampleCalibration",
    "calibrate_case",
    "calibrate_cases",
]
