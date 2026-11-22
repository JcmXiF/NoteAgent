"""The trace dimension of a behaviour eval: the tool path a case expects.

A case declares a TraceSpec: required tools as an ordered subsequence, tools
that must never appear, and a model-turn bound. Evidence is read from a
session's transcript; nothing here calls a model or reaches a provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.report import UNCATEGORIZED, Check, FirstError

TRACE_CATEGORY = "tool-contract"
REQUIRED = "required"
FORBIDDEN = "forbidden"
MAX_TURNS = "max_turns"


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call the model made, at its model-turn step."""

    name: str
    step: int


@dataclass(frozen=True, slots=True)
class TraceEvidence:
    """What a finished run left in its transcript, for the trace dimension."""

    tools: tuple[ToolCall, ...] = ()
    model_turns: int = 0


@dataclass(frozen=True, slots=True)
class TraceViolation:
    """One way the actual tool path departed from the declared trace."""

    kind: str
    name: str
    step: int | None
    expected: str
    actual: str
    category: str = TRACE_CATEGORY

    @property
    def where(self) -> str:
        return "" if self.step is None else f" at step {self.step}"

    def detail(self) -> str:
        return f"{self.expected}; got {self.actual}{self.where}"


@dataclass(frozen=True, slots=True)
class TraceSpec:
    """The tool path a case expects: an ordered subsequence, a deny list, a bound."""

    required: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    max_turns: int | None = None

    def violations(self, evidence: TraceEvidence) -> tuple[TraceViolation, ...]:
        found: list[TraceViolation] = []
        names = [call.name for call in evidence.tools]
        for call in evidence.tools:
            if call.name in self.forbidden:
                found.append(
                    TraceViolation(
                        FORBIDDEN,
                        call.name,
                        call.step,
                        f"no {call.name} call",
                        f"{call.name} was called",
                    )
                )
        if self.required:
            index = 0
            for call in evidence.tools:
                if index < len(self.required) and call.name == self.required[index]:
                    index += 1
            if index < len(self.required):
                missing = self.required[index]
                previous = self.required[index - 1] if index else None
                expected = (
                    f"required tool {missing} after {previous}"
                    if previous
                    else f"required tool {missing}"
                )
                found.append(
                    TraceViolation(
                        REQUIRED,
                        missing,
                        None,
                        expected,
                        f"the tool path was {', '.join(names) or 'empty'}",
                    )
                )
        if self.max_turns is not None and evidence.model_turns > self.max_turns:
            found.append(
                TraceViolation(
                    MAX_TURNS,
                    "",
                    self.max_turns + 1,
                    f"at most {self.max_turns} model turns",
                    f"{evidence.model_turns} model turns",
                )
            )
        return tuple(found)

    def checks(self, evidence: TraceEvidence) -> tuple[Check, ...]:
        """One failed Check per violation, so the case fails like any other check."""
        return tuple(
            Check(
                f"trace: {item.kind} {item.name}".strip(),
                False,
                item.detail(),
                item.category,
            )
            for item in self.violations(evidence)
        )


def first_error(
    checks: Iterable[Check],
    evidence: TraceEvidence | None = None,
    trace: TraceSpec | None = None,
    error: str = "",
) -> FirstError | None:
    """The earliest trace violation, else the first failed check.

    A stepped trace violation is a known location. A missing required tool has
    no step but is still a trace error, so it beats a step-less failed check; a
    case error is the last resort.
    """
    checks = tuple(checks)
    violations = (
        trace.violations(evidence)
        if trace is not None and evidence is not None
        else ()
    )
    stepped = [item for item in violations if item.step is not None]
    if stepped:
        return _trace_failure(
            min(stepped, key=lambda item: int(item.step or 0))
        )
    if violations:
        return _trace_failure(violations[0])
    for check in checks:
        if not check.passed:
            return _check_failure(check)
    if error:
        return _error_failure(error)
    return None


def _trace_failure(violation: TraceViolation) -> FirstError:
    return FirstError(
        check="",
        category=violation.category,
        step=violation.step,
        kind="trace",
        event=violation.name or "model",
        expected=violation.expected,
        actual=violation.actual,
    )


def _check_failure(check: Check) -> FirstError:
    return FirstError(
        check=check.name,
        category=check.category,
        step=None,
        kind="check",
        event=check.location,
        expected="check to pass",
        actual=check.detail or "failed",
    )


def _error_failure(error: str) -> FirstError:
    return FirstError(
        check="",
        category=UNCATEGORIZED,
        step=None,
        kind="error",
        event="",
        expected="the case to finish",
        actual=_last_line(error),
    )


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def evidence_from_entries(
    entries: Iterable[Mapping[str, Any]],
) -> TraceEvidence:
    """Read the tool path and model turns from a session's transcript."""
    tools: list[ToolCall] = []
    turns = 0
    for entry in entries:
        if entry.get("type") != "message" or entry.get("role") != "assistant":
            continue
        turns += 1
        calls = entry.get("toolCalls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            name = call.get("name")
            if isinstance(name, str) and name:
                tools.append(ToolCall(name, turns))
    return TraceEvidence(tuple(tools), turns)


__all__ = [
    "FirstError",
    "ToolCall",
    "TraceEvidence",
    "TraceSpec",
    "TraceViolation",
    "evidence_from_entries",
    "first_error",
    "FORBIDDEN",
    "MAX_TURNS",
    "REQUIRED",
    "TRACE_CATEGORY",
]
