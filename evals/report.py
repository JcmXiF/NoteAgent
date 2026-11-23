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

REPORT_VERSION = 3
UNCATEGORIZED = "uncategorized"
STATE = "state"
TOOL_CONTRACT = "tool-contract"
PROVENANCE = "provenance"
PUBLISH = "publish"
LOCK = "lock"
RECOVERY = "recovery"
NODE_RUN = "node-run"
COMPACTION = "compaction"
CATEGORIES = (
    STATE,
    TOOL_CONTRACT,
    PROVENANCE,
    PUBLISH,
    LOCK,
    RECOVERY,
    NODE_RUN,
    COMPACTION,
)


@dataclass(frozen=True, slots=True)
class Check:
    """One thing a case asserted, and why it failed when it did."""

    name: str
    passed: bool
    detail: str = ""
    #: The failure domain this check belongs to.
    category: str = UNCATEGORIZED
    #: Where the failed check lives (a file or state path), when it has one.
    location: str = ""

    def __post_init__(self) -> None:
        if not self.category:
            object.__setattr__(self, "category", UNCATEGORIZED)
        elif self.category not in CATEGORIES and self.category != UNCATEGORIZED:
            raise ValueError(
                f"unknown check category: {self.category!r}"
            )


@dataclass(frozen=True, slots=True)
class FirstError:
    """The first place a failed run departed from the expected behaviour."""

    check: str
    category: str
    step: int | None
    kind: str
    event: str
    expected: str
    actual: str

    @property
    def label(self) -> str:
        return self.check or self.event or self.kind

    @property
    def where(self) -> str:
        return "" if self.step is None else f" at step {self.step}"

    def to_payload(self) -> dict[str, object]:
        return {
            "check": self.check,
            "category": self.category,
            "step": self.step,
            "kind": self.kind,
            "event": self.event,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True, slots=True)
class CaseResult:
    name: str
    title: str
    checks: tuple[Check, ...]
    error: str
    duration_s: float
    exchanges: tuple["Exchange", ...]
    log: tuple[str, ...]
    #: The first failed check or trace violation, when the case failed.
    first_error: FirstError | None = None

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
    def passed_check_count(self) -> int:
        return sum(len(result.passed_checks) for result in self.results)

    @property
    def total_check_count(self) -> int:
        return sum(len(result.checks) for result in self.results)

    @property
    def first_error_categories(self) -> dict[str, int]:
        """Count the failed cases by their first-error category."""
        counts: dict[str, int] = {}
        for result in self.results:
            if result.passed:
                continue
            category = (
                result.first_error.category
                if result.first_error is not None
                else UNCATEGORIZED
            )
            counts[category] = counts.get(category, 0) + 1
        return dict(sorted(counts.items()))

    def aggregate(self) -> dict[str, object]:
        return {
            "cases": _score(self.passed_count, len(self.results)),
            "checks": _score(
                self.passed_check_count, self.total_check_count
            ),
            "firstErrorCategories": self.first_error_categories,
        }

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
            "aggregate": self.aggregate(),
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
                            "category": item.category,
                            "location": item.location,
                        }
                        for item in result.checks
                    ],
                    "firstError": (
                        result.first_error.to_payload()
                        if result.first_error is not None
                        else None
                    ),
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
            "## Aggregate",
            f"- Cases: {_fraction(self.passed_count, len(self.results))}",
            (
                f"- Checks: "
                f"{_fraction(self.passed_check_count, self.total_check_count)}"
            ),
            (
                "- First-error categories: "
                + (
                    ", ".join(
                        f"{category}={count}"
                        for category, count in self.first_error_categories.items()
                    )
                    or "none"
                )
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
                if result.first_error is not None:
                    first = result.first_error
                    lines.append(
                        f"- first error: {first.label} [{first.category}]"
                        f"{first.where}: {first.actual}"
                    )
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


def _score(passed: int, total: int) -> dict[str, object]:
    return {
        "passed": passed,
        "total": total,
        "rate": _rate(passed, total),
    }


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 4) if total else None


def _fraction(passed: int, total: int) -> str:
    return f"{passed}/{total} passed ({passed / total:.0%})" if total else "n/a"


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


__all__ = [
    "CATEGORIES",
    "COMPACTION",
    "CaseResult",
    "Check",
    "EvalReport",
    "FirstError",
    "LOCK",
    "NODE_RUN",
    "PROVENANCE",
    "PUBLISH",
    "RECOVERY",
    "REPORT_VERSION",
    "STATE",
    "TOOL_CONTRACT",
    "UNCATEGORIZED",
    "utc_now",
]
