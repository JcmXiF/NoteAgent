"""Render one quality run as a self-contained HTML page.

The page is the local debugging surface: one file, inline CSS, native
``<details>`` disclosure, and relative links to the run's artifacts. No
template engine, no JavaScript, no network.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from evals.quality.baseline import BaselineDelta, CaseDelta
from evals.quality.judge import JudgeVerdict
from evals.quality.report import (
    CaseSummary,
    QualityReport,
    public_base_url,
)

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 2rem auto; max-width: 1100px; padding: 0 1rem;
  color: #1f2328; background: #fff; }
h1 { margin-bottom: .25rem; }
h2 { margin-top: 1.5rem; }
h3 { margin-bottom: .25rem; }
.meta, .muted { color: #656d76; }
.config { display: grid; grid-template-columns: max-content 1fr;
  gap: .1rem 1rem; margin: .75rem 0; }
.config dt { font-weight: 600; }
.config dd { margin: 0; }
.baseline-note { background: #f6f8fa; padding: .5rem .75rem;
  border-radius: 6px; }
.summary { display: flex; gap: .75rem; align-items: center;
  flex-wrap: wrap; margin: 1rem 0; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
  color: #fff; font-weight: 600; font-size: 12px; }
.badge.pass, .badge.fixed { background: #1a7f37; }
.badge.fail, .badge.regressed { background: #cf222e; }
.badge.new { background: #0969da; }
.badge.unchanged { background: #6e7781; }
.warning { background: #fff8c5; border-left: 4px solid #d4a72c;
  padding: .5rem .75rem; margin: .5rem 0; }
details.case { border: 1px solid #d0d7de; border-radius: 6px;
  margin: 1rem 0; }
details.case > summary { padding: .5rem .75rem; cursor: pointer;
  background: #f6f8fa; border-radius: 6px; }
details.case[open] > summary { border-bottom: 1px solid #d0d7de;
  border-radius: 6px 6px 0 0; }
.case-body { padding: .75rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { text-align: left; padding: .3rem .5rem;
  border-bottom: 1px solid #d0d7de; vertical-align: top; }
th { background: #f6f8fa; }
pre { white-space: pre-wrap; word-break: break-word; background: #f6f8fa;
  padding: .75rem; border-radius: 6px; }
code { background: #f6f8fa; padding: .1rem .3rem; border-radius: 4px; }
.timeline { list-style: none; padding-left: 0; }
.timeline li { border-left: 3px solid #d0d7de; padding: .25rem .75rem;
  margin: .25rem 0; }
.timeline li.user { border-color: #0969da; }
.timeline li.assistant { border-color: #1a7f37; }
.timeline li.tool { border-color: #8250df; }
.timeline li.wait { border-color: #d4a72c; }
.timeline li.error { border-color: #cf222e; }
.who { font-weight: 600; margin-right: .5rem; }
.dimension { border: 1px solid #d0d7de; border-radius: 6px;
  padding: .5rem .75rem; margin: .5rem 0; }
.dimension h4 { margin: 0 0 .25rem; }
.evidence { margin: .25rem 0 0; padding-left: 1.25rem; }
.evidence li { color: #656d76; }
details.error { border-left: 4px solid #cf222e; padding-left: .75rem;
  margin-top: 1rem; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  details.case > summary, th, pre, code, .baseline-note { background: #161b22; }
  th, td, details.case, .dimension { border-color: #30363d; }
  .muted, .meta, .evidence li { color: #8b949e; }
}
"""


@dataclass(frozen=True, slots=True)
class Counts:
    """The run-level numbers the summary line shows."""

    passed: int
    failed: int
    failed_assertions: int
    failed_dimensions: int
    judge_errors: int


def write_html(
    report: QualityReport,
    path: Path,
    *,
    artifacts_root: Path,
    delta: BaselineDelta | None = None,
    baseline_note: str = "",
) -> Path:
    """Write *report* as one HTML file beside its per-case artifacts."""
    path = Path(path)
    details = _load_case_details(artifacts_root)
    page = _page(
        report,
        details=details,
        delta=delta,
        baseline_note=baseline_note,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def _load_case_details(root: Path) -> dict[str, dict[str, Any]]:
    details: dict[str, dict[str, Any]] = {}
    cases_dir = Path(root) / "cases"
    if not cases_dir.is_dir():
        return details
    for case_dir in sorted(cases_dir.iterdir()):
        path = case_dir / "case.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            details[case_dir.name] = payload
    return details


def _page(
    report: QualityReport,
    *,
    details: dict[str, dict[str, Any]],
    delta: BaselineDelta | None,
    baseline_note: str,
) -> str:
    passed = sum(1 for case in report.cases if case.passed)
    counts = Counts(
        passed=passed,
        failed=len(report.cases) - passed,
        failed_assertions=sum(
            1
            for case in report.cases
            for item in case.assertions
            if not item.passed
        ),
        failed_dimensions=sum(
            1
            for case in report.cases
            if case.judge is not None
            and not case.judge.error
            and not case.judge.skipped
            for item in case.judge.dimensions
            if item.verdict != "pass"
        ),
        judge_errors=sum(
            1
            for case in report.cases
            if case.judge is not None
            and (case.judge.error or case.judge.skipped)
        ),
    )
    delta_cases = {item.name: item for item in delta.cases} if delta else {}
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>Quality eval — {_escape(report.started_at)}</title>",
            f"<style>{CSS}</style>",
            "</head>",
            "<body>",
            _header(report, baseline_note),
            _summary(counts),
            _delta_section(delta),
            '<section class="cases"><h2>Cases</h2>',
            *[
                _case_section(
                    case,
                    details.get(case.name),
                    delta_cases.get(case.name),
                )
                for case in report.cases
            ],
            "</section>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _header(report: QualityReport, baseline_note: str) -> str:
    agent = report.agent
    judge = report.judge
    lines = [
        '<header class="page-header">',
        "<h1>Quality eval</h1>",
        f'<p class="meta">Run at <strong>{_escape(report.started_at)}</strong>'
        f" · {report.duration_s:.1f}s</p>",
        '<dl class="config">',
        f"<dt>Agent model</dt><dd>{_escape(agent.model)}</dd>",
        f"<dt>Base URL</dt><dd>{_escape(public_base_url(agent.base_url))}</dd>",
        f"<dt>Agent temperature</dt><dd>{_escape(agent.temperature)}</dd>",
    ]
    if judge is not None:
        lines.extend(
            [
                f"<dt>Judge model</dt><dd>{_escape(judge.model)}</dd>",
                f"<dt>Judge temperature</dt>"
                f"<dd>{_escape(judge.temperature)}</dd>",
            ]
        )
        hashes = {
            case.judge.prompt_sha256
            for case in report.cases
            if case.judge is not None and case.judge.prompt_sha256
        }
        if len(hashes) == 1:
            prompt_hash = next(iter(hashes))
        elif hashes:
            prompt_hash = "per case"
        else:
            prompt_hash = "—"
        lines.append(
            f"<dt>Judge prompt sha256</dt>"
            f"<dd>{_escape(prompt_hash)}</dd>"
        )
    lines.append("</dl>")
    if baseline_note:
        lines.append(
            f'<p class="baseline-note">{_escape(baseline_note)}</p>'
        )
    lines.append("</header>")
    return "\n".join(lines)


def _summary(counts: Counts) -> str:
    return (
        '<section class="summary">'
        f'<span class="badge pass">{counts.passed} passed</span>'
        f'<span class="badge fail">{counts.failed} failed</span>'
        f'<span class="muted">failed assertions: '
        f"{counts.failed_assertions}</span>"
        f'<span class="muted">failed dimensions: '
        f"{counts.failed_dimensions}</span>"
        f'<span class="muted">judge errors/skipped: '
        f"{counts.judge_errors}</span>"
        "</section>"
    )


def _delta_section(delta: BaselineDelta | None) -> str:
    if delta is None:
        return (
            '<section class="delta"><h2>Baseline</h2>'
            '<p class="muted">No baseline to compare with.</p></section>'
        )
    parts = [
        '<section class="delta"><h2>Baseline</h2>',
        f'<p class="muted">Compared with {_escape(delta.created_at)}.</p>',
    ]
    if delta.config_changes:
        parts.append(
            '<p class="warning">Configuration changed: '
            f'{_escape(", ".join(delta.config_changes))}. '
            "Deltas may come from that change.</p>"
        )
    if delta.prompt_changes:
        parts.append(
            '<p class="warning">Judge prompt changed for: '
            f'{_escape(", ".join(delta.prompt_changes))}.</p>'
        )
    rows = []
    for item in delta.cases:
        rows.append(
            "<tr>"
            f"<td>{_escape(item.name)}</td>"
            f'<td><span class="badge {_escape(item.status)}">'
            f"{_escape(item.status)}</span></td>"
            f"<td>{_escape(', '.join(item.assertions)) or '—'}</td>"
            f"<td>{_escape(', '.join(item.judge)) or '—'}</td>"
            "</tr>"
        )
    parts.append(
        "<table><thead><tr><th>Case</th><th>Status</th>"
        "<th>Assertions changed</th><th>Judge changed</th></tr></thead>"
        "<tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )
    return "\n".join(parts)


def _case_section(
    case: CaseSummary,
    detail: dict[str, Any] | None,
    delta: CaseDelta | None,
) -> str:
    status = ""
    if delta is not None:
        status = (
            f' <span class="badge {_escape(delta.status)}">'
            f"{_escape(delta.status)}</span>"
        )
    open_attr = " open" if not case.passed else ""
    badge = "pass" if case.passed else "fail"
    verdict = "PASS" if case.passed else "FAIL"
    parts = [
        f'<details class="case"{open_attr}>',
        f'<summary><span class="badge {badge}">{verdict}</span> '
        f'{_escape(case.name)} <span class="muted">{_escape(case.title)}</span> '
        f'<span class="muted">{case.duration_s:.1f}s</span>{status}</summary>',
        '<div class="case-body">',
        _assertions(case),
        _judge(case.judge),
        _timeline_section(detail),
        _repro(case.name),
    ]
    if case.error:
        parts.append(
            '<details class="error"><summary>Run error</summary>'
            f"<pre>{_escape(case.error)}</pre></details>"
        )
    parts.append("</div></details>")
    return "\n".join(parts)


def _assertions(case: CaseSummary) -> str:
    rows = []
    for item in case.assertions:
        badge = "pass" if item.passed else "fail"
        verdict = "PASS" if item.passed else "FAIL"
        rows.append(
            "<tr>"
            f"<td>{_escape(item.name)}</td>"
            f'<td><span class="badge {badge}">{verdict}</span></td>'
            f"<td>{_value(item.expected)}</td>"
            f"<td>{_value(item.actual)}</td>"
            f"<td>{_escape(item.detail)}</td>"
            "</tr>"
        )
    return (
        '<section class="assertions"><h3>Assertions</h3>'
        "<table><thead><tr><th>Assertion</th><th>Result</th>"
        "<th>Expected</th><th>Actual</th><th>Detail</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></section>"
    )


def _judge(judge: JudgeVerdict | None) -> str:
    if judge is None:
        return (
            '<section class="judge"><h3>Judge</h3>'
            '<p class="muted">No judge result.</p></section>'
        )
    if judge.skipped:
        return (
            '<section class="judge"><h3>Judge</h3>'
            f'<p class="muted">Skipped: {_escape(judge.skipped)}</p></section>'
        )
    if judge.error:
        raw = (
            "<details><summary>Raw judge response</summary>"
            f"<pre>{_escape(judge.raw)}</pre></details>"
            if judge.raw
            else ""
        )
        return (
            '<section class="judge"><h3>Judge</h3>'
            f'<p class="warning">{_escape(judge.error)}</p>{raw}</section>'
        )
    cards = []
    for item in judge.dimensions:
        badge = "pass" if item.verdict == "pass" else "fail"
        evidence = ""
        if item.evidence:
            evidence = (
                '<ul class="evidence">'
                + "".join(
                    f"<li>{_escape(evidence_quote)}</li>"
                    for evidence_quote in item.evidence
                )
                + "</ul>"
            )
        cards.append(
            f'<div class="dimension {badge}">'
            f'<h4>{_escape(item.dimension)} '
            f'<span class="badge {badge}">{_escape(item.verdict)}</span></h4>'
            f"<p>{_escape(item.reason)}</p>{evidence}</div>"
        )
    return (
        '<section class="judge"><h3>Judge</h3>'
        f'<p class="muted">prompt sha256: '
        f"{_escape(judge.prompt_sha256)}</p>"
        + "".join(cards)
        + "<details><summary>Raw judge response</summary>"
        + f"<pre>{_escape(judge.raw)}</pre></details></section>"
    )


def _timeline_section(detail: dict[str, Any] | None) -> str:
    if not detail:
        return (
            '<section class="timeline-section"><h3>Timeline</h3>'
            '<p class="muted">Case details were not kept for this run.</p>'
            "</section>"
        )
    entries = detail.get("entries") or []
    trace = detail.get("trace") or []
    items = _timeline(entries, trace)
    return (
        '<section class="timeline-section"><h3>Timeline</h3>'
        f'<ul class="timeline">{items}</ul></section>'
    )


def _timeline(entries, trace) -> str:
    if not isinstance(entries, list):
        entries = []
    if not isinstance(trace, list):
        trace = []
    items: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("type")
        role = entry.get("role")
        content = entry.get("content")
        if kind == "message":
            if role == "user" and isinstance(content, str) and content.strip():
                items.append(_item("user", "user", _short(content)))
            elif role == "assistant":
                calls = entry.get("toolCalls")
                if not isinstance(calls, list):
                    calls = []
                text = (
                    _short(content)
                    if isinstance(content, str) and content.strip()
                    else ""
                )
                if calls:
                    names = ", ".join(
                        f"{_escape(call.get('name'))}"
                        f"({_short(str(call.get('arguments') or ''), 160)})"
                        for call in calls
                        if isinstance(call, dict)
                    )
                    items.append(
                        _item(
                            "assistant",
                            "assistant",
                            f'{text} <span class="muted">→ '
                            f"tool calls: {names}</span>",
                        )
                    )
                elif text:
                    items.append(_item("assistant", "assistant", text))
            elif role == "toolResult" and isinstance(content, str):
                items.append(_item("tool", "tool result", _short(content)))
        elif kind == "wait_proposed":
            items.append(
                _item(
                    "wait",
                    "wait",
                    f"proposed: {_escape(entry.get('job'))}",
                )
            )
        elif kind == "wait_completed":
            summary = entry.get("summary")
            detail = _short(summary) if isinstance(summary, str) else ""
            items.append(
                _item(
                    "wait",
                    "wait",
                    f"completed: {_escape(entry.get('job'))} / "
                    f"{_escape(entry.get('status'))} {detail}",
                )
            )
        elif kind == "wait_cancelled":
            items.append(_item("wait", "wait", "cancelled"))
        elif kind == "resume_started":
            items.append(_item("wait", "resume", "started"))
        elif kind == "compaction":
            summary = entry.get("summary")
            items.append(
                _item(
                    "wait",
                    "compaction",
                    _short(summary) if isinstance(summary, str) else "",
                )
            )
    for record in trace:
        if not isinstance(record, dict):
            continue
        if record.get("type") in ("run_failed", "run_cancelled"):
            items.append(
                _item(
                    "error",
                    str(record.get("type")),
                    _escape(record.get("code") or ""),
                )
            )
    if not items:
        return '<li class="muted">No timeline entries.</li>'
    return "\n".join(items)


def _repro(name: str) -> str:
    quoted = quote(name, safe="")
    return (
        '<section class="repro"><h3>Repro</h3>'
        f"<p><code>python -m evals.quality --case {_escape(name)}</code></p>"
        '<ul class="artifacts">'
        f'<li><a href="cases/{quoted}/session.jsonl">session.jsonl</a></li>'
        f'<li><a href="cases/{quoted}/events.jsonl">events.jsonl</a></li>'
        f'<li><a href="cases/{quoted}/case.json">case.json</a></li>'
        f'<li><a href="cases/{quoted}/judge.json">judge.json</a></li>'
        f'<li><a href="cases/{quoted}/project/">project/</a></li>'
        "</ul></section>"
    )


def _item(kind: str, who: str, body: str) -> str:
    return (
        f'<li class="{_escape(kind)}"><span class="who">{_escape(who)}</span>'
        f"{body}</li>"
    )


def _short(text: str, limit: int = 700) -> str:
    clipped = text.strip()
    if len(clipped) > limit:
        clipped = clipped[:limit] + "…"
    return _escape(clipped)


def _value(value: object) -> str:
    if isinstance(value, str):
        return _escape(value)
    try:
        return _escape(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _escape(value)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


__all__ = ["write_html"]
