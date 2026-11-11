"""Save a quality run's verdicts and compare the next run to them.

The baseline holds each case's pass/fail, assertion results, judge verdicts,
the judge prompt hash, and the run date that names the baseline. Durations and
evidence text are deliberately absent from the comparison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

from evals.quality.report import QualityReport, connection_payload

SCHEMA = 1

DeltaStatus: TypeAlias = Literal[
    "unchanged", "fixed", "regressed", "new"
]
STATUSES: tuple[DeltaStatus, ...] = (
    "unchanged",
    "fixed",
    "regressed",
    "new",
)


class BaselineError(ValueError):
    """A baseline file does not match its contract."""


@dataclass(frozen=True, slots=True)
class CaseBaseline:
    passed: bool
    assertions: dict[str, bool]
    judge: dict[str, str]
    prompt_sha256: str


@dataclass(frozen=True, slots=True)
class Baseline:
    schema: int
    created_at: str
    agent: dict[str, Any]
    judge: dict[str, Any]
    cases: dict[str, CaseBaseline]


@dataclass(frozen=True, slots=True)
class CaseDelta:
    """How one case changed against the baseline."""

    name: str
    status: DeltaStatus
    assertions: tuple[str, ...]
    judge: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BaselineDelta:
    created_at: str
    cases: tuple[CaseDelta, ...]
    config_changes: tuple[str, ...]
    prompt_changes: tuple[str, ...]


def build_payload(report: QualityReport) -> dict[str, Any]:
    """The baseline a run would save."""
    connection = connection_payload(report)
    judge = dict(connection["judge"])
    if report.judge is not None:
        judge["prompt_sha256"] = report.judge.prompt_sha256
    return {
        "schema": SCHEMA,
        "created_at": report.started_at,
        "agent": connection["agent"],
        "judge": judge,
        "cases": {
            case.name: {
                "passed": case.passed,
                "assertions": {
                    item.name: item.passed for item in case.assertions
                },
                "judge": (
                    case.judge.verdicts() if case.judge else {}
                ),
                "prompt_sha256": (
                    case.judge.prompt_sha256 if case.judge else ""
                ),
            }
            for case in report.cases
        },
    }


def save_baseline(path: Path, report: QualityReport) -> Path:
    """Write *report* as the new baseline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_payload(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_baseline(path: Path) -> Baseline | None:
    """Read a baseline, or ``None`` when the file is absent."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise BaselineError(f"{path}: {err}") from err
    if not isinstance(raw, dict):
        raise BaselineError(f"{path}: the baseline must be an object")
    if raw.get("schema") != SCHEMA:
        raise BaselineError(
            f"{path}: unsupported schema {raw.get('schema')!r}"
        )
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, dict):
        raise BaselineError(f"{path}: cases must be an object")

    cases: dict[str, CaseBaseline] = {}
    for name, item in cases_raw.items():
        if not isinstance(item, dict):
            raise BaselineError(f"{path}: case {name!r} must be an object")
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise BaselineError(
                f"{path}: case {name!r} passed must be true or false"
            )
        assertions = item.get("assertions", {})
        if not isinstance(assertions, dict) or any(
            not isinstance(value, bool) for value in assertions.values()
        ):
            raise BaselineError(
                f"{path}: case {name!r} assertions must map names to booleans"
            )
        judge = item.get("judge", {})
        if not isinstance(judge, dict) or any(
            not isinstance(value, str) for value in judge.values()
        ):
            raise BaselineError(
                f"{path}: case {name!r} judge must map dimensions to verdicts"
            )
        prompt = item.get("prompt_sha256", "")
        if not isinstance(prompt, str):
            raise BaselineError(
                f"{path}: case {name!r} prompt_sha256 must be a string"
            )
        cases[str(name)] = CaseBaseline(
            passed=passed,
            assertions={
                str(key): value for key, value in assertions.items()
            },
            judge={str(key): value for key, value in judge.items()},
            prompt_sha256=prompt,
        )

    agent = raw.get("agent")
    judge_info = raw.get("judge")
    return Baseline(
        schema=SCHEMA,
        created_at=str(raw.get("created_at") or ""),
        agent=dict(agent) if isinstance(agent, dict) else {},
        judge=dict(judge_info) if isinstance(judge_info, dict) else {},
        cases=cases,
    )


def compare(report: QualityReport, baseline: Baseline) -> BaselineDelta:
    """Compare *report* to *baseline*, reading only verdicts and config."""
    payload = build_payload(report)
    config_changes = _config_changes(payload, baseline)
    prompt_changes: list[str] = []
    cases: list[CaseDelta] = []

    for case in report.cases:
        current = payload["cases"][case.name]
        current_assertions = current["assertions"]
        current_judge = current["judge"]
        current_prompt = current["prompt_sha256"]
        old = baseline.cases.get(case.name)
        if old is None:
            cases.append(CaseDelta(case.name, "new", (), ()))
            continue

        assertion_changes = tuple(
            sorted(
                name
                for name in set(current_assertions) | set(old.assertions)
                if current_assertions.get(name)
                != old.assertions.get(name)
            )
        )
        judge_changes = tuple(
            sorted(
                name
                for name in set(current_judge) | set(old.judge)
                if current_judge.get(name) != old.judge.get(name)
            )
        )
        if current_prompt != old.prompt_sha256:
            prompt_changes.append(case.name)
        cases.append(
            CaseDelta(
                case.name,
                _status(case.passed, old.passed),
                assertion_changes,
                judge_changes,
            )
        )

    return BaselineDelta(
        created_at=baseline.created_at,
        cases=tuple(cases),
        config_changes=config_changes,
        prompt_changes=tuple(prompt_changes),
    )


def _status(current: bool, old: bool) -> DeltaStatus:
    if current and not old:
        return "fixed"
    if not current and old:
        return "regressed"
    return "unchanged"


def _config_changes(
    current: dict[str, Any], baseline: Baseline
) -> tuple[str, ...]:
    changes: list[str] = []
    agent = current.get("agent") or {}
    judge = current.get("judge") or {}
    for key in ("model", "base_url", "temperature"):
        if agent.get(key) != baseline.agent.get(key):
            changes.append(f"agent.{key}")
    for key in ("model", "temperature"):
        if judge.get(key) != baseline.judge.get(key):
            changes.append(f"judge.{key}")
    return tuple(changes)


__all__ = [
    "Baseline",
    "BaselineDelta",
    "BaselineError",
    "CaseBaseline",
    "CaseDelta",
    "DeltaStatus",
    "SCHEMA",
    "STATUSES",
    "build_payload",
    "compare",
    "load_baseline",
    "save_baseline",
]
