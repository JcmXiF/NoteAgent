"""Write a quality run's JSON report.

The report is for a reader at the terminal: it holds the case summaries and
assertion verdicts, and the connection the run used without the key. The full
transcript and trace belong to the per-case artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from evals.quality.assertions import AssertionResult
from evals.quality.judge import JudgeVerdict


@dataclass(frozen=True, slots=True)
class AgentInfo:
    """The connection a run used, as the report may show it."""

    model: str
    base_url: str
    temperature: float


@dataclass(frozen=True, slots=True)
class JudgeInfo:
    """The judge connection, as the report may show it."""

    model: str
    temperature: float


@dataclass(frozen=True, slots=True)
class CaseSummary:
    """One case's verdict, the assertions behind it, and the judge's reading."""

    name: str
    title: str
    passed: bool
    duration_s: float
    assertions: tuple[AssertionResult, ...]
    judge: JudgeVerdict | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class QualityReport:
    """One quality run."""

    started_at: str
    duration_s: float
    agent: AgentInfo
    cases: tuple[CaseSummary, ...]
    judge: JudgeInfo | None = None


def public_base_url(value: str) -> str:
    """The base URL without userinfo, query or fragment."""
    parts = urlsplit(value.strip())
    if not parts.hostname:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = parts.hostname if port is None else f"{parts.hostname}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def write_report(report: QualityReport, path: Path) -> Path:
    """Write *report* as JSON, creating its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "startedAt": report.started_at,
        "durationSeconds": round(report.duration_s, 3),
        "agent": {
            "model": report.agent.model,
            "baseUrl": public_base_url(report.agent.base_url),
            "temperature": report.agent.temperature,
        },
        "judge": (
            None
            if report.judge is None
            else {
                "model": report.judge.model,
                "temperature": report.judge.temperature,
                "prompts": {
                    case.name: case.judge.prompt_sha256
                    for case in report.cases
                    if case.judge is not None
                    and case.judge.prompt_sha256
                },
            }
        ),
        "cases": [
            {
                "name": case.name,
                "title": case.title,
                "passed": case.passed,
                "durationSeconds": round(case.duration_s, 3),
                "assertions": [
                    item.to_payload() for item in case.assertions
                ],
                "judge": (
                    None if case.judge is None else case.judge.to_payload()
                ),
                "error": case.error,
            }
            for case in report.cases
        ],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


__all__ = [
    "AgentInfo",
    "CaseSummary",
    "JudgeInfo",
    "QualityReport",
    "public_base_url",
    "write_report",
]
