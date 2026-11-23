"""Read what a golden run did, and check it against the case's expected trace.

The evidence comes from three places: the session entries (tool calls, tool
results, wait transitions), the session view (the derived wait), and the
content-free `.events.jsonl` trace (control-action failures). Nothing here
calls a model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.golden.dataset import GoldenQuery
from evals.golden.dataset import JOB_SOURCES

_VALID_PAIRS = frozenset(
    (job, interface)
    for job, interfaces in JOB_SOURCES.items()
    for interface in interfaces
)
_CONTROL_CODES = frozenset(
    {"handler_error", "propose_not_alone", "invalid_proposal"}
)


@dataclass(frozen=True, slots=True)
class Evidence:
    """What one finished run says about its own behaviour."""

    tools: tuple[str, ...] = ()
    read_pages: tuple[str, ...] = ()
    tool_rounds: int = 0
    wait_status: str | None = None
    wait_job: str | None = None
    wait_interface: str | None = None
    propose_calls: tuple[tuple[str, str], ...] = ()
    errors: tuple[str, ...] = ()
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0


@dataclass(frozen=True, slots=True)
class TraceCheck:
    """One expected-trace check, with enough detail to explain a failure."""

    name: str
    passed: bool
    expected: object
    actual: object
    detail: str = ""


def evidence_from(
    *,
    entries: Iterable[Mapping[str, Any]] = (),
    trace: Iterable[Mapping[str, Any]] = (),
    session: Mapping[str, Any] | None = None,
    events: Iterable[tuple[str, Mapping[str, Any]]] = (),
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_s: float = 0.0,
) -> Evidence:
    """Read the evidence a finished run left in its transcript and trace."""
    entry_list = list(entries)
    trace_list = list(trace)
    tools = tuple(
        str(record["tool"])
        for record in trace_list
        if record.get("type") == "tool_result"
        and isinstance(record.get("tool"), str)
    )
    read_pages: list[str] = []
    propose_calls: list[tuple[str, str]] = []
    rounds = 0
    for entry in entry_list:
        if entry.get("role") != "assistant":
            continue
        calls = entry.get("toolCalls")
        if not isinstance(calls, list) or not calls:
            continue
        rounds += 1
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            name = call.get("name")
            arguments = call.get("arguments")
            if name == "wiki_read":
                slug = _argument(arguments, "slug")
                if slug:
                    read_pages.append(slug)
            elif name == "propose":
                propose_calls.append(
                    (
                        _argument(arguments, "job"),
                        _argument(arguments, "interface"),
                    )
                )
    wait_status, wait_job, wait_interface = _wait(entry_list, session)
    return Evidence(
        tools=tools,
        read_pages=tuple(read_pages),
        tool_rounds=rounds,
        wait_status=wait_status,
        wait_job=wait_job,
        wait_interface=wait_interface,
        propose_calls=tuple(propose_calls),
        errors=_errors(trace_list, events),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_s=latency_s,
    )


def evaluate(
    case: GoldenQuery, evidence: Evidence
) -> tuple[TraceCheck, ...]:
    """Check the run against the expected trace, in a fixed order."""
    expected = case.expected_trace
    checks: list[TraceCheck] = []
    seen_tools = sorted(set(evidence.tools))

    if expected.tools_any:
        passed = any(name in evidence.tools for name in expected.tools_any)
        checks.append(
            TraceCheck(
                "tools_any",
                passed,
                list(expected.tools_any),
                seen_tools,
                ""
                if passed
                else f"none of {list(expected.tools_any)} ran; "
                f"tools seen: {seen_tools}",
            )
        )

    # ``read_pages`` stays evidence, not a check: the golden record says which
    # tools it expects, and a gap question may be answered from search alone.
    # The report still shows every page the agent opened.

    actual_wait = evidence.wait_status or "absent"
    checks.append(
        TraceCheck(
            "wait",
            actual_wait == expected.wait,
            expected.wait,
            actual_wait,
            ""
            if actual_wait == expected.wait
            else f"expected wait {expected.wait!r}, got {actual_wait!r}",
        )
    )

    if expected.propose_job is not None:
        checks.append(
            TraceCheck(
                "propose_job",
                evidence.wait_job == expected.propose_job,
                expected.propose_job,
                evidence.wait_job or "",
                ""
                if evidence.wait_job == expected.propose_job
                else f"expected job {expected.propose_job!r}, "
                f"got {evidence.wait_job!r}",
            )
        )
        checks.append(
            TraceCheck(
                "propose_interface",
                evidence.wait_interface == expected.propose_interface,
                expected.propose_interface,
                evidence.wait_interface or "",
                ""
                if evidence.wait_interface == expected.propose_interface
                else f"expected interface "
                f"{expected.propose_interface!r}, "
                f"got {evidence.wait_interface!r}",
            )
        )

    if expected.propose_valid:
        pair = (
            evidence.wait_job or "",
            evidence.wait_interface or "",
        )
        passed = pair in _VALID_PAIRS
        checks.append(
            TraceCheck(
                "propose_valid",
                passed,
                "a legal (job, interface)",
                list(pair),
                ""
                if passed
                else f"({pair[0]!r}, {pair[1]!r}) is not a legal proposal",
            )
        )

    # Tool rounds stay in the evidence and the report, but are not a
    # pass/fail gate: the benchmark measures completion and grounding, not a
    # fixed exploration budget. The kernel's own safety cap remains separate.
    if expected.no_error:
        passed = not evidence.errors
        checks.append(
            TraceCheck(
                "no_error",
                passed,
                "no errors",
                list(evidence.errors),
                "" if passed else "; ".join(evidence.errors),
            )
        )

    return tuple(checks)


def _argument(arguments: object, key: str) -> str:
    if not isinstance(arguments, str):
        return ""
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    if not isinstance(value, Mapping):
        return ""
    item = value.get(key)
    return str(item) if isinstance(item, str) else ""


def _wait(
    entries: list[Mapping[str, Any]],
    session: Mapping[str, Any] | None,
) -> tuple[str | None, str | None, str | None]:
    if session is not None and "wait" in session:
        wait = session.get("wait")
        if isinstance(wait, Mapping):
            return (
                _text(wait.get("status")),
                _text(wait.get("job")),
                _text(wait.get("interface")),
            )
        return None, None, None
    status: str | None = None
    job: str | None = None
    interface: str | None = None
    for entry in entries:
        kind = entry.get("type")
        if kind == "wait_proposed":
            status = "proposed"
            job = _text(entry.get("job"))
            interface = _text(entry.get("interface"))
        elif kind == "wait_entered":
            status = "entered"
        elif kind == "wait_completed":
            status = _text(entry.get("status")) or "completed"
        elif kind == "wait_cancelled":
            status = "cancelled"
    return status, job, interface


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
            continue
        message = str(data.get("message") or "").strip()
        found.append(f"{code}: {message}" if message else code)
    for record in trace:
        kind = record.get("type")
        if kind == "run_failed":
            code = str(record.get("code") or "")
            found.append(f"run_failed: {code}" if code else "run_failed")
        elif kind == "tool_result":
            tool = str(record.get("tool") or "unknown")
            codes = record.get("codes")
            if isinstance(codes, list):
                for code in codes:
                    if code in _CONTROL_CODES:
                        found.append(f"{code}: {tool}")
            if record.get("error"):
                found.append(f"tool_error: {tool}")
    return _dedupe(found)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


__all__ = ["Evidence", "TraceCheck", "evaluate", "evidence_from"]
