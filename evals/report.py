"""What the evals concluded: the checks, the case results, and the report file.

The report is written for two readers: CI decides from the JSON whether a run
passed, and a person reads the Markdown to see which behaviour broke without
opening the artifacts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals.harness import Exchange

REPORT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Check:
    """One thing a case asserted, and why it failed when it did."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    title: str
    checks: tuple[Check, ...]
    error: str
    duration_s: float
    exchanges: tuple["Exchange", ...]
    log: tuple[str, ...]

    @property
    def passed(self) -> bool:
        # A case that ran without asserting anything proves nothing.
        return (
            not self.error
            and bool(self.checks)
            and all(check.passed for check in self.checks)
        )

    @property
    def passed_checks(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.passed)

    @property
    def failed_checks(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    @property
    def error_line(self) -> str:
        """The last line of a traceback, for a report that stays readable."""
        return _last_line(self.error)


@dataclass(frozen=True, slots=True)
class EvalReport:
    """Every case's verdict, as a report a person or CI can read."""

    results: tuple[CaseResult, ...]
    started_at: str
    duration_s: float

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed_count(self) -> int:
        return len(self.results) - self.passed_count

    @property
    def passed(self) -> bool:
        return self.failed_count == 0

    def payload(self) -> dict[str, object]:
        return {
            "version": REPORT_VERSION,
            "startedAt": self.started_at,
            "durationSeconds": round(self.duration_s, 3),
            "passed": self.passed_count,
            "failed": self.failed_count,
            "cases": [
                {
                    "name": result.name,
                    "title": result.title,
                    "passed": result.passed,
                    "durationSeconds": round(result.duration_s, 3),
                    "checks": [
                        {
                            "name": item.name,
                            "passed": item.passed,
                            "detail": item.detail,
                        }
                        for item in result.checks
                    ],
                    "error": result.error_line,
                }
                for result in self.results
            ],
        }

    def markdown(self) -> str:
        lines = [
            "# Agent harness evals",
            "",
            f"- Started: {self.started_at}",
            (
                f"- Result: **{self.passed_count} passed, "
                f"{self.failed_count} failed** in {self.duration_s:.1f}s"
            ),
            "",
            "| Case | Result | Checks | Time |",
            "|---|---|---|---|",
        ]
        for result in self.results:
            count = f"{len(result.passed_checks)}/{len(result.checks)}"
            verdict = "PASS" if result.passed else "FAIL"
            lines.append(
                f"| `{result.name}` | {verdict} | {count} | "
                f"{result.duration_s:.1f}s |"
            )
        failures = [result for result in self.results if not result.passed]
        if failures:
            lines.extend(["", "## Failures"])
            for result in failures:
                lines.extend(["", f"### `{result.name}`", "", result.title])
                for item in result.failed_checks:
                    lines.append(f"- {item.name}: {item.detail}")
                if result.error:
                    lines.extend(["", "```", result.error_line, "```"])
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        return path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


__all__ = [
    "CaseResult",
    "Check",
    "EvalReport",
    "REPORT_VERSION",
    "utc_now",
]
