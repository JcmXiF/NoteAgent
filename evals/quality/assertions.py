"""Turn a run's evidence into per-assertion results.

The assertions read only what the run recorded: the tool names in the trace,
the assistant turns that asked for tools, and the errors
the run reported. Nothing here calls a model or touches the project.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.quality.cases import AssertionSpec

@dataclass(frozen=True, slots=True)
class Evidence:
    """What one finished run says about its own behaviour."""

    tools: tuple[str, ...] = ()
    tool_rounds: int = 0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """One assertion's verdict, with enough detail to explain a failure."""

    name: str
    passed: bool
    expected: object
    actual: object
    detail: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


def evidence_from(
    *,
    entries: Iterable[Mapping[str, Any]] = (),
    trace: Iterable[Mapping[str, Any]] = (),
    events: Iterable[tuple[str, Mapping[str, Any]]] = (),
) -> Evidence:
    """Read the evidence a finished run left in its transcript and trace.

    ``tools`` holds tool results.
    """
    entry_list = list(entries)
    trace_list = list(trace)
    tools = tuple(
        str(record["tool"])
        for record in trace_list
        if record.get("type") == "tool_result"
        and isinstance(record.get("tool"), str)
    )
    rounds = sum(
        1
        for entry in entry_list
        if entry.get("role") == "assistant"
        and isinstance(entry.get("toolCalls"), list)
        and bool(entry["toolCalls"])
    )
    return Evidence(
        tools=tools,
        tool_rounds=rounds,
        errors=_errors(trace_list, events),
    )


def _errors(
    trace: list[Mapping[str, Any]],
    events: Iterable[tuple[str, Mapping[str, Any]]],
) -> tuple[str, ...]:
    found: list[str] = []
    for kind, data in events:
        if kind != "error":
            continue
        code = str(data.get("code") or "error")
        if code == "cancelled":
            # A cancel is a deliberate stop, not a failure of the run.
            continue
        message = str(data.get("message") or "").strip()
        found.append(f"{code}: {message}" if message else code)
    for record in trace:
        kind = record.get("type")
        if kind == "run_failed":
            code = str(record.get("code") or "")
            found.append(f"run_failed: {code}" if code else "run_failed")
        elif kind == "tool_result":
            codes = record.get("codes")
            if isinstance(codes, list) and "handler_error" in codes:
                found.append(f"handler_error: {record.get('tool') or 'unknown'}")
    return _dedupe(found)


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


def evaluate(
    spec: AssertionSpec, evidence: Evidence
) -> tuple[AssertionResult, ...]:
    """Evaluate every assertion the case declared, in a fixed order."""
    results: list[AssertionResult] = []
    actual_tools = tuple(sorted(set(evidence.tools)))

    if spec.tools_any is not None:
        expected = list(spec.tools_any)
        passed = any(name in evidence.tools for name in spec.tools_any)
        results.append(
            AssertionResult(
                "tools_any",
                passed,
                expected,
                list(actual_tools),
                ""
                if passed
                else f"none of {expected} ran; tools seen: {list(actual_tools)}",
            )
        )

    if spec.tools_all is not None:
        expected = list(spec.tools_all)
        missing = [
            name for name in spec.tools_all if name not in evidence.tools
        ]
        results.append(
            AssertionResult(
                "tools_all",
                not missing,
                expected,
                list(actual_tools),
                "" if not missing else f"missing tools: {missing}",
            )
        )

    if spec.tools_none is not None:
        expected = list(spec.tools_none)
        present = [name for name in spec.tools_none if name in evidence.tools]
        results.append(
            AssertionResult(
                "tools_none",
                not present,
                expected,
                list(actual_tools),
                "" if not present else f"forbidden tools ran: {present}",
            )
        )

    if spec.max_tool_rounds is not None:
        passed = evidence.tool_rounds <= spec.max_tool_rounds
        results.append(
            AssertionResult(
                "max_tool_rounds",
                passed,
                spec.max_tool_rounds,
                evidence.tool_rounds,
                ""
                if passed
                else (
                    f"expected at most {spec.max_tool_rounds} tool rounds, "
                    f"got {evidence.tool_rounds}"
                ),
            )
        )

    if spec.no_error is not None:
        passed = not (spec.no_error and evidence.errors)
        results.append(
            AssertionResult(
                "no_error",
                passed,
                "no errors" if spec.no_error else "errors allowed",
                list(evidence.errors),
                "" if passed else "; ".join(evidence.errors),
            )
        )

    return tuple(results)


__all__ = ["AssertionResult", "Evidence", "evaluate", "evidence_from"]
