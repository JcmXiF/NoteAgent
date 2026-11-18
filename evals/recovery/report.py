"""The recovery eval for two readers: JSON for a diff, Markdown for a person.

The JSON keeps every metric's numerator and denominator so two runs can be
compared without re-opening the artifacts. The Markdown puts the same numbers,
the per-suite counts and the missed boundaries where a person can read them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.recovery.metrics import Metrics, Ratio, ScenarioRun, compute_metrics

__all__ = [
    "REPORT_VERSION",
    "RecoveryReport",
    "counts",
    "markdown",
    "payload",
    "write_report",
]

REPORT_VERSION = 1


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Everything one run of the eval concluded."""

    runs: tuple[ScenarioRun, ...]
    started_at: str
    duration_s: float
    model: str
    artifacts: str


@dataclass(frozen=True, slots=True)
class _Row:
    key: str
    label: str
    target: str
    safety: bool


_ROWS = (
    _Row("detection", "Interrupt detection", "100%", True),
    _Row("false_interrupt", "False interrupts", "0%", True),
    _Row("preservation", "Content preservation", "100%", True),
    _Row("replay", "Tool replay", "0", True),
    _Row("resume_success", "Resume success", ">= 90%", False),
    _Row("recovery_time", "Recovery time", "observational", False),
    _Row("classification", "Classification accuracy", "100%", True),
    _Row("overwrite", "User-edit overwrite", "0%", True),
    _Row("idempotency", "Restore idempotency", "100%", True),
    _Row("restore_success", "Restore success", ">= 90%", False),
    _Row("budget_completion", "Budget completion", ">= 90%", False),
)


def payload(report: RecoveryReport) -> dict[str, Any]:
    """The run as JSON: counts, exact ratios, suites and artifact paths."""
    metrics = compute_metrics(report.runs)
    return {
        "version": REPORT_VERSION,
        "startedAt": report.started_at,
        "durationSeconds": round(report.duration_s, 3),
        "model": report.model,
        "counts": counts(report.runs),
        "metrics": _metrics(metrics),
        "suites": _suites(report.runs),
        "artifacts": report.artifacts,
    }


def markdown(report: RecoveryReport) -> str:
    """The run as a short report: result, metric table, suites, misses."""
    metrics = compute_metrics(report.runs)
    verdicts = counts(report.runs)
    lines = [
        "# Recovery eval",
        "",
        f"- Started: {report.started_at}",
        (
            "- Result: **"
            f"{verdicts['passed']} passed, {verdicts['failed']} failed, "
            f"{verdicts['missed']} missed, {verdicts['errors']} infra"
            "**"
        ),
        f"- Duration: {report.duration_s:.1f}s",
        f"- Model: `{report.model}`",
        f"- Artifacts: `{report.artifacts}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Sample | Target |",
        "|---|---:|---:|---:|",
    ]
    for row in _ROWS:
        lines.append(
            f"| {row.label} | {_value_text(row.key, metrics)} | "
            f"{_sample_text(row.key, metrics)} | {row.target} |"
        )

    lines.extend(
        [
            "",
            "## Scenarios",
            "",
            "| Suite | Scenario | Reps | Passed | Failed | Missed | Infra |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for suite in _suites(report.runs):
        for item in suite["scenarios"]:
            lines.append(
                f"| {suite['name']} | {item['name']} | {item['reps']} | "
                f"{item['passed']} | {item['failed']} | "
                f"{item['missed']} | {item['infra']} |"
            )

    lines.extend(["", "## Missed boundaries", ""])
    missed = [
        item
        for item in report.runs
        if item.status == "missed"
        or (
            item.status == "infra"
            and "never hit the target boundary" in item.detail
        )
    ]
    if not missed:
        lines.append("- None.")
    else:
        for item in missed:
            lines.append(
                f"- `{item.scenario}` rep {item.rep} — "
                f"observed `{item.observed_boundary or 'unknown'}`"
                + (f": {item.detail}" if item.detail else "")
            )

    lines.extend(["", "## Infra failures", ""])
    infra = [
        item
        for item in report.runs
        if item.status == "infra" and item not in missed
    ]
    if not infra:
        lines.append("- None.")
    else:
        for item in infra:
            lines.append(
                f"- `{item.scenario}` rep {item.rep} — "
                + (item.detail or "no detail")
            )
    return "\n".join(lines) + "\n"


def write_report(report: RecoveryReport, path: Path) -> Path:
    """Write the JSON and its Markdown summary beside it; return the JSON."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    target.with_suffix(".md").write_text(
        markdown(report), encoding="utf-8"
    )
    return target


def counts(runs: tuple[ScenarioRun, ...]) -> dict[str, int]:
    """How the runs split by verdict; ``errors`` counts infra failures."""
    return {
        "runs": len(runs),
        "passed": sum(1 for item in runs if item.status == "passed"),
        "failed": sum(1 for item in runs if item.status == "failed"),
        "missed": sum(1 for item in runs if item.status == "missed"),
        "errors": sum(1 for item in runs if item.status == "infra"),
    }


def _metrics(metrics: Metrics) -> dict[str, Any]:
    return {
        "detection": _ratio_payload(metrics.detection, safety=True),
        "false_interrupt": _ratio_payload(
            metrics.false_interrupt, safety=True
        ),
        "preservation": _ratio_payload(metrics.preservation, safety=True),
        "replay": {
            "count": metrics.replay_count,
            "runs": metrics.replay_runs,
            "safety": True,
        },
        "resume_success": _ratio_payload(
            metrics.resume_success, safety=False
        ),
        "recovery_time": {
            "p50Ms": round(metrics.recovery_p50_ms, 3),
            "p95Ms": round(metrics.recovery_p95_ms, 3),
            "samples": metrics.recovery_samples,
            "safety": False,
        },
        "classification": _ratio_payload(
            metrics.classification, safety=True
        ),
        "overwrite": _ratio_payload(metrics.overwrite, safety=True),
        "idempotency": _ratio_payload(metrics.idempotency, safety=True),
        "restore_success": _ratio_payload(
            metrics.restore_success, safety=False
        ),
        "budget_completion": _ratio_payload(
            metrics.budget_completion, safety=False
        ),
    }


def _ratio_payload(ratio: Ratio, *, safety: bool) -> dict[str, Any]:
    return {
        "numerator": ratio.numerator,
        "denominator": ratio.denominator,
        "value": None if ratio.value is None else round(ratio.value, 6),
        "safety": safety,
    }


def _suites(runs: tuple[ScenarioRun, ...]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[ScenarioRun]]] = {}
    for item in runs:
        grouped.setdefault(item.suite, {}).setdefault(item.scenario, []).append(
            item
        )
    suites: list[dict[str, Any]] = []
    for name, scenarios in grouped.items():
        entries = []
        for scenario, group in scenarios.items():
            entries.append(
                {
                    "name": scenario,
                    "reps": len(group),
                    "passed": sum(
                        1 for item in group if item.status == "passed"
                    ),
                    "failed": sum(
                        1 for item in group if item.status == "failed"
                    ),
                    "missed": sum(
                        1 for item in group if item.status == "missed"
                    ),
                    "infra": sum(
                        1 for item in group if item.status == "infra"
                    ),
                    "boundaries": sorted(
                        {
                            item.observed_boundary
                            for item in group
                            if item.observed_boundary
                        }
                    ),
                }
            )
        suites.append({"name": name, "scenarios": entries})
    return suites


def _value_text(key: str, metrics: Metrics) -> str:
    if key == "replay":
        return str(metrics.replay_count)
    if key == "recovery_time":
        return (
            f"p50 {metrics.recovery_p50_ms:.0f} ms · "
            f"p95 {metrics.recovery_p95_ms:.0f} ms"
        )
    ratio = getattr(metrics, key)
    return f"{ratio.value:.1%}" if ratio.present else "—"


def _sample_text(key: str, metrics: Metrics) -> str:
    if key == "replay":
        return f"{metrics.replay_runs} runs"
    if key == "recovery_time":
        return f"{metrics.recovery_samples} runs"
    ratio = getattr(metrics, key)
    return f"{ratio.numerator}/{ratio.denominator}"
