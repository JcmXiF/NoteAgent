"""Calibration answers: hand-written answers with the verdicts the judge must reach.

The calibration set is the judge's unit test. It runs the judge only, without
the agent: each entry is a transcript built from the case's user turns plus one
hand-written answer. A judge that disagrees with the hand-written verdicts is
not ready to grade a live run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.quality.cases import CaseSpec
from evals.quality.judge import DIMENSIONS, JudgeClient, judge_case


class CalibrationError(ValueError):
    """A calibration file does not match its contract."""


@dataclass(frozen=True, slots=True)
class CalibrationEntry:
    """One hand-written answer and the verdicts it must receive."""

    name: str
    case: str
    answer: str
    expected: dict[str, str]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """What the judge said about one calibration answer."""

    name: str
    passed: bool
    expected: dict[str, str]
    actual: dict[str, str]
    detail: str = ""


def load_calibration(path: Path) -> tuple[CalibrationEntry, ...]:
    """Load and validate the calibration answers."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise CalibrationError(f"{path}: invalid YAML: {err}") from err
    except OSError as err:
        raise CalibrationError(f"{path}: cannot be read: {err}") from err
    if not isinstance(raw, list) or not raw:
        raise CalibrationError(
            f"{path}: the calibration must be a non-empty list"
        )

    entries: list[CalibrationEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise CalibrationError(
                f"{path}: entry {index} must be a mapping"
            )
        _reject_unknown(
            item, ("name", "case", "answer", "expected"), path, index
        )
        name = _text(item.get("name"), "name", path, index)
        if name in seen:
            raise CalibrationError(
                f"{path}: duplicate calibration name {name!r}"
            )
        seen.add(name)
        entries.append(
            CalibrationEntry(
                name=name,
                case=_text(item.get("case"), "case", path, index),
                answer=_text(item.get("answer"), "answer", path, index),
                expected=_expected(item.get("expected"), path, index, name),
            )
        )
    return tuple(entries)


def calibrate(
    cases: Iterable[CaseSpec],
    entries: Iterable[CalibrationEntry],
    *,
    client: JudgeClient,
    secret: str = "",
) -> tuple[CalibrationResult, ...]:
    """Run the judge on every entry and compare it with the hand verdicts."""
    by_name = {case.name: case for case in cases}
    results: list[CalibrationResult] = []
    for entry in entries:
        case = by_name.get(entry.case)
        if case is None:
            raise CalibrationError(
                f"{entry.name}: no case named {entry.case!r}"
            )
        transcript: list[dict[str, Any]] = [
            {"type": "message", "role": "user", "content": turn}
            for turn in case.turns
        ]
        transcript.append(
            {"type": "message", "role": "assistant", "content": entry.answer}
        )
        verdict = judge_case(
            case, transcript, client=client, secret=secret
        )
        actual = {
            item.dimension: item.verdict for item in verdict.dimensions
        }
        detail = verdict.error or verdict.skipped
        passed = (
            not verdict.error
            and not verdict.skipped
            and actual == entry.expected
        )
        results.append(
            CalibrationResult(
                name=entry.name,
                passed=passed,
                expected=entry.expected,
                actual=actual,
                detail=detail,
            )
        )
    return tuple(results)


# The validators stay local: this loader reports entry context and raises
# CalibrationError, while the case loader reports case context and raises
# CaseError. Sharing them would mean sharing the error type too.
def _reject_unknown(
    raw: Mapping[Any, Any],
    allowed: tuple[str, ...],
    path: Path,
    index: int,
) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise CalibrationError(
            f"{path}: entry {index} has unknown key(s): "
            + ", ".join(unknown)
        )


def _text(value: Any, key: str, path: Path, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CalibrationError(
            f"{path}: entry {index} {key} must be a non-empty string"
        )
    return value.strip()


def _expected(
    value: Any, path: Path, index: int, name: str
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise CalibrationError(
            f"{path}: entry {name!r} expected must be a mapping"
        )
    unknown = sorted(
        str(key) for key in value if key not in DIMENSIONS
    )
    if unknown:
        raise CalibrationError(
            f"{path}: entry {name!r} has unknown dimension(s): "
            + ", ".join(unknown)
        )
    missing = [dimension for dimension in DIMENSIONS if dimension not in value]
    if missing:
        raise CalibrationError(
            f"{path}: entry {name!r} is missing dimension(s): "
            + ", ".join(missing)
        )
    out: dict[str, str] = {}
    for dimension in DIMENSIONS:
        verdict = value[dimension]
        if verdict not in ("pass", "fail"):
            raise CalibrationError(
                f"{path}: entry {name!r} {dimension} must be "
                "'pass' or 'fail'"
            )
        out[dimension] = str(verdict)
    return out


__all__ = [
    "CalibrationEntry",
    "CalibrationError",
    "CalibrationResult",
    "calibrate",
    "load_calibration",
]
