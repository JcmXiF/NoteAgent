"""Render one golden run as JSON and a self-contained HTML page.

The page is the local review surface: inline CSS, native ``<details>``
disclosure, relative links to the run's artifacts. No template engine, no
JavaScript, no network. The JSON beside it carries the same reading for a tool.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from evals.golden.manifest import (
    DIMENSIONS,
    RunRecord,
    counts,
    distribution_by_dimension,
    manifest_payload,
    means_by_dimension,
    public_base_url,
)
from evals.golden.runner import (
    PairedSummary,
    QueryRun,
    VariantRun,
)

SCHEMA = 1
TREND_ROWS = 8

CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 2rem auto; max-width: 1180px; padding: 0 1rem;
  color: #1f2328; background: #fff; }
h1 { margin-bottom: .25rem; }
h2 { margin-top: 1.75rem; }
h3, h4 { margin-bottom: .25rem; }
.meta, .muted { color: #656d76; }
.config { display: grid; grid-template-columns: max-content 1fr;
  gap: .1rem 1rem; margin: .75rem 0; }
.config dt { font-weight: 600; }
.config dd { margin: 0; }
.summary { display: flex; gap: .5rem; align-items: center;
  flex-wrap: wrap; margin: 1rem 0; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
  color: #fff; font-weight: 600; font-size: 12px; }
.badge.pass, .badge.strict { background: #1a7f37; }
.badge.fail, .badge.base { background: #cf222e; }
.badge.tie { background: #6e7781; }
.badge.warn { background: #9a6700; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { text-align: left; padding: .3rem .5rem;
  border-bottom: 1px solid #d0d7de; vertical-align: top; }
th { background: #f6f8fa; }
pre { white-space: pre-wrap; word-break: break-word; background: #f6f8fa;
  padding: .75rem; border-radius: 6px; }
code { background: #f6f8fa; padding: .1rem .3rem; border-radius: 4px; }
details.case { border: 1px solid #d0d7de; border-radius: 6px;
  margin: 1rem 0; }
details.case > summary { padding: .5rem .75rem; cursor: pointer;
  background: #f6f8fa; border-radius: 6px; }
details.case[open] > summary { border-bottom: 1px solid #d0d7de;
  border-radius: 6px 6px 0 0; }
.case-body { padding: .75rem; }
.dimension { border: 1px solid #d0d7de; border-radius: 6px;
  padding: .5rem .75rem; margin: .5rem 0; }
.dimension h4 { margin: 0 0 .25rem; }
.score { font-weight: 600; }
.warning { background: #fff8c5; border-left: 4px solid #d4a72c;
  padding: .5rem .75rem; margin: .5rem 0; }
details.error { border-left: 4px solid #cf222e; padding-left: .75rem;
  margin-top: 1rem; }
.answer { max-height: 24rem; overflow: auto; }
.timeline { list-style: none; padding-left: 0; }
.timeline li { border-left: 3px solid #d0d7de; padding: .25rem .75rem;
  margin: .25rem 0; }
.timeline li.user { border-color: #0969da; }
.timeline li.assistant { border-color: #1a7f37; }
.timeline li.tool { border-color: #8250df; }
.timeline li.wait { border-color: #d4a72c; }
.timeline li.error { border-color: #cf222e; }
.who { font-weight: 600; margin-right: .5rem; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  details.case > summary, th, pre, code { background: #161b22; }
  th, td, details.case, .dimension { border-color: #30363d; }
  .muted, .meta { color: #8b949e; }
}
"""


def write_variant_report(
    run: VariantRun,
    record: RunRecord,
    history: Sequence[Mapping[str, str]],
    *,
    json_path: Path,
    html_path: Path,
) -> dict[str, object]:
    """Write one variant's ``report.json`` and ``report.html``."""
    payload = _variant_payload(run, record, history)
    _write_json(Path(json_path), payload)
    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(html_path).write_text(
        _variant_page(run, record, history), encoding="utf-8"
    )
    return payload


def write_ab_report(
    base: VariantRun,
    strict: VariantRun,
    base_record: RunRecord,
    strict_record: RunRecord,
    paired: PairedSummary,
    history: Sequence[Mapping[str, str]],
    *,
    pair_id: str,
    json_path: Path,
    html_path: Path,
) -> dict[str, object]:
    """Write the combined paired report for ``--ab``."""
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "kind": "ab",
        "runId": pair_id,
        "startedAt": base_record.started_at,
        "durationSeconds": round(
            base_record.duration_s + strict_record.duration_s, 3
        ),
        "agent": {
            "model": base_record.agent_model,
            "baseUrl": public_base_url(base_record.agent_base_url),
            "temperature": base_record.agent_temperature,
        },
        "judge": {
            "model": base_record.judge_model,
            "temperature": base_record.judge_temperature,
            "promptSha256": base_record.judge_prompt_sha256,
        },
        "golden": {
            "corpusSha256": base_record.corpus_sha256,
            "queriesSha256": base_record.queries_sha256,
            "queryCount": len(base_record.queries),
        },
        "variants": [
            manifest_payload(base_record),
            manifest_payload(strict_record),
        ],
        "paired": paired.to_payload(),
        "trend": _trend(history),
    }
    _write_json(Path(json_path), payload)
    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(html_path).write_text(
        _ab_page(
            base,
            strict,
            base_record,
            strict_record,
            paired,
            history,
            pair_id,
        ),
        encoding="utf-8",
    )
    return payload


def _variant_payload(
    run: VariantRun,
    record: RunRecord,
    history: Sequence[Mapping[str, str]],
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "kind": "variant",
        "runId": record.run_id,
        "startedAt": record.started_at,
        "durationSeconds": round(record.duration_s, 3),
        "variant": record.variant,
        "agent": {
            "model": record.agent_model,
            "baseUrl": public_base_url(record.agent_base_url),
            "temperature": record.agent_temperature,
        },
        "judge": {
            "model": record.judge_model,
            "temperature": record.judge_temperature,
            "promptSha256": record.judge_prompt_sha256,
        },
        "golden": {
            "corpusSha256": record.corpus_sha256,
            "queriesSha256": record.queries_sha256,
            "queryCount": len(record.queries),
        },
        "promptSha256": record.prompt_sha256,
        "gitCommit": record.git_commit,
        "counts": counts(record.queries),
        "qualityWarnings": sum(
            1 for query in run.queries if query.quality_warning
        ),
        "means": means_by_dimension(record.queries),
        "distribution": distribution_by_dimension(record.queries),
        "byType": _by_slice(run.queries, "type"),
        "byDifficulty": _by_slice(run.queries, "difficulty"),
        "queries": [_query_payload(query) for query in run.queries],
        "trend": _trend(history),
    }


def _query_payload(query: QueryRun) -> dict[str, object]:
    return {
        "id": query.case.id,
        "type": query.case.type,
        "difficulty": query.case.difficulty,
        "question": query.case.question,
        "answer": query.answer,
        "passed": query.passed,
        "tracePassed": query.trace_passed,
        "judgeOk": query.judge_ok,
        "qualityWarning": query.quality_warning,
        "error": query.error,
        "scores": [
            {
                "dimension": item.dimension,
                "score": item.score,
                "soft": round(item.soft, 4),
                "reason": _clip(item.reason, 2_000),
                "error": item.error,
            }
            for item in query.verdict.scores
        ],
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "expected": check.expected,
                "actual": check.actual,
                "detail": check.detail,
            }
            for check in query.checks
        ],
        "evidence": {
            "tools": list(query.evidence.tools),
            "readPages": list(query.evidence.read_pages),
            "toolRounds": query.evidence.tool_rounds,
            "wait": query.evidence.wait_status or "absent",
            "waitJob": query.evidence.wait_job or "",
            "waitInterface": query.evidence.wait_interface or "",
            "proposeCalls": [
                list(pair) for pair in query.evidence.propose_calls
            ],
            "errors": list(query.evidence.errors),
        },
        "latencySeconds": round(query.latency_s, 3),
        "inputTokens": query.input_tokens,
        "outputTokens": query.output_tokens,
        "artifacts": {
            "session": f"queries/{quote(query.case.id, safe='')}/session.jsonl",
            "events": f"queries/{quote(query.case.id, safe='')}/events.jsonl",
            "case": f"queries/{quote(query.case.id, safe='')}/case.json",
            "judge": f"queries/{quote(query.case.id, safe='')}/judge.json",
        },
    }


def _by_slice(
    queries: Sequence[QueryRun], field: str
) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for key in sorted({str(getattr(query.case, field)) for query in queries}):
        group = [
            query
            for query in queries
            if str(getattr(query.case, field)) == key
        ]
        out[key] = {
            "count": len(group),
            "passed": sum(1 for query in group if query.passed),
            "tracePassed": sum(
                1 for query in group if query.trace_passed
            ),
            "means": _soft_means(group),
        }
    return out


def _soft_means(queries: Sequence[QueryRun]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for dimension in DIMENSIONS:
        values = [
            query.soft_scores[dimension]
            for query in queries
            if dimension in query.soft_scores
        ]
        out[dimension] = (
            round(sum(values) / len(values), 4) if values else None
        )
    return out


def _trend(
    history: Sequence[Mapping[str, str]]
) -> list[dict[str, str]]:
    return [dict(row) for row in history[-TREND_ROWS:]]


def _variant_page(
    run: VariantRun,
    record: RunRecord,
    history: Sequence[Mapping[str, str]],
) -> str:
    totals = counts(record.queries)
    means = means_by_dimension(record.queries)
    warnings = sum(
        1 for query in run.queries if query.quality_warning
    )
    parts = [
        _header(
            f"Golden eval — {run.variant}",
            record,
            extra=[("Variant", run.variant)],
        ),
        _summary_badges(totals, means, warnings),
        "<h2>By type</h2>",
        _slice_table(_by_slice(run.queries, "type"), "Type"),
        "<h2>By difficulty</h2>",
        _slice_table(
            _by_slice(run.queries, "difficulty"), "Difficulty"
        ),
        "<h2>Queries</h2>",
        *[_case_section(query) for query in run.queries],
        "<h2>Trend</h2>",
        _trend_table(history),
        "</body></html>",
    ]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>Golden eval — {_escape(run.variant)}"
            f" — {_escape(record.started_at)}</title>",
            f"<style>{CSS}</style>",
            "</head>",
            "<body>",
            *parts,
        ]
    )


def _ab_page(
    base: VariantRun,
    strict: VariantRun,
    base_record: RunRecord,
    strict_record: RunRecord,
    paired: PairedSummary,
    history: Sequence[Mapping[str, str]],
    pair_id: str,
) -> str:
    parts = [
        _header(
            "Golden eval — base vs strict",
            base_record,
            extra=[("Pair", pair_id)],
        ),
        "<h2>Variants</h2>",
        _variant_table([base, strict], [base_record, strict_record]),
        "<h2>Paired</h2>",
        _paired_section(paired),
        "<h2>Paired by type</h2>",
        _paired_slice_table(paired.by_type, "Type"),
        "<h2>Paired by difficulty</h2>",
        _paired_slice_table(paired.by_difficulty, "Difficulty"),
        "<h2>Artifacts</h2>",
        '<p class="muted">Per-query details live in each variant’s '
        "report:</p>",
        "<ul>"
        '<li><a href="base/report.html">base/report.html</a></li>'
        '<li><a href="strict/report.html">strict/report.html</a></li>'
        "</ul>",
        "<h2>Trend</h2>",
        _trend_table(history),
        "</body></html>",
    ]
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>Golden eval — A/B — {_escape(pair_id)}</title>",
            f"<style>{CSS}</style>",
            "</head>",
            "<body>",
            *parts,
        ]
    )


def _header(
    title: str,
    record: RunRecord,
    *,
    extra: Sequence[tuple[str, str]] = (),
) -> str:
    rows = [
        ("Run", record.run_id),
        ("Started", record.started_at),
        ("Duration", f"{record.duration_s:.1f}s"),
        ("Agent model", record.agent_model),
        ("Base URL", public_base_url(record.agent_base_url)),
        ("Temperature", str(record.agent_temperature)),
        ("Judge model", record.judge_model),
        ("Corpus sha256", record.corpus_sha256),
        ("Queries sha256", record.queries_sha256),
        ("Prompt sha256", record.prompt_sha256),
        ("Git commit", record.git_commit or "—"),
        *extra,
    ]
    body = "".join(
        f"<dt>{_escape(label)}</dt><dd>{_escape(value)}</dd>"
        for label, value in rows
    )
    return (
        f"<h1>{_escape(title)}</h1>"
        f'<dl class="config">{body}</dl>'
    )


def _summary_badges(
    totals: Mapping[str, int],
    means: Mapping[str, float | None],
    quality_warnings: int = 0,
) -> str:
    return (
        '<section class="summary">'
        f'<span class="badge pass">{totals["passed"]} ran</span>'
        f'<span class="badge fail">{totals["queries"] - totals["passed"]}'
        " failed</span>"
        f'<span class="muted">trace: {totals["tracePassed"]} passed / '
        f'{totals["traceFailed"]} failed</span>'
        f'<span class="muted">judge errors: {totals["judgeErrors"]}'
        "</span>"
        f'<span class="badge warn">{quality_warnings} quality warnings'
        "</span>"
        f'<span class="muted">means: '
        f'{_mean_text(means)}</span>'
        '<span class="muted">ran = trace + judge completed; quality '
        "warning = any dimension ≤2</span>"
        "</section>"
    )


def _mean_text(means: Mapping[str, float | None]) -> str:
    return ", ".join(
        f"{dimension[0]}={_fmt(means.get(dimension))}"
        for dimension in DIMENSIONS
    )


def _slice_table(
    slices: Mapping[str, Mapping[str, object]], title: str
) -> str:
    rows = []
    for key, data in slices.items():
        means = data.get("means")
        assert isinstance(means, Mapping)
        rows.append(
            "<tr>"
            f"<td>{_escape(key)}</td>"
            f"<td>{_escape(data.get('count'))}</td>"
            f"<td>{_escape(data.get('passed'))}</td>"
            f"<td>{_escape(data.get('tracePassed'))}</td>"
            f"<td>{_escape(_mean_text(means))}</td>"
            "</tr>"
        )
    return _table(
        [title, "Queries", "Passed", "Trace passed", "Means"], rows
    )


def _variant_table(
    runs: Sequence[VariantRun],
    records: Sequence[RunRecord],
) -> str:
    rows = []
    for run, record in zip(runs, records, strict=True):
        totals = counts(record.queries)
        warnings = sum(
            1 for query in run.queries if query.quality_warning
        )
        rows.append(
            "<tr>"
            f"<td>{_escape(run.variant)}</td>"
            f"<td>{totals['queries']}</td>"
            f"<td>{totals['passed']}</td>"
            f"<td>{totals['tracePassed']}/{totals['traceFailed']}</td>"
            f"<td>{totals['judgeErrors']}</td>"
            f"<td>{warnings}</td>"
            f"<td>{_escape(_mean_text(means_by_dimension(record.queries)))}"
            "</td>"
            "</tr>"
        )
    return _table(
        [
            "Variant",
            "Queries",
            "Ran",
            "Trace pass/fail",
            "Judge errors",
            "Quality warnings",
            "Means",
        ],
        rows,
    )


def _paired_section(paired: PairedSummary) -> str:
    badges = (
        '<section class="summary">'
        f'<span class="badge strict">{paired.strict_wins} strict wins</span>'
        f'<span class="badge base">{paired.base_wins} base wins</span>'
        f'<span class="badge tie">{paired.ties} ties</span>'
        f'<span class="muted">mean delta '
        f"{paired.mean_delta:+.3f} (strict − base)</span>"
        "</section>"
    )
    delta_rows = [
        "<tr>"
        f"<td>{_escape(dimension)}</td>"
        f"<td>{paired.dimension_deltas.get(dimension, 0.0):+.3f}</td>"
        "</tr>"
        for dimension in DIMENSIONS
    ]
    rows = []
    for case in paired.cases:
        rows.append(
            "<tr>"
            f"<td>{_escape(case.id)}</td>"
            f"<td>{_escape(case.type)}</td>"
            f"<td>{_escape(case.difficulty)}</td>"
            f"<td>{_escape(_score_text(case.base_scores))}</td>"
            f"<td>{_escape(_score_text(case.strict_scores))}</td>"
            f"<td>{case.mean_delta:+.3f}</td>"
            f'<td><span class="badge {_escape(case.winner)}">'
            f"{_escape(case.winner)}</span></td>"
            f"<td>{'RAN' if case.base_passed else 'FAIL'}</td>"
            f"<td>{'RAN' if case.strict_passed else 'FAIL'}</td>"
            f"<td>{_quality_badge(case.base_quality_warning)}</td>"
            f"<td>{_quality_badge(case.strict_quality_warning)}</td>"
            "</tr>"
        )
    return "\n".join(
        [
            badges,
            "<h3>Dimension deltas</h3>",
            _table(
                ["Dimension", "Mean delta"], delta_rows
            ),
            "<h3>Per query</h3>",
            _table(
                [
                    "Query",
                    "Type",
                    "Difficulty",
                    "Base",
                    "Strict",
                    "Delta",
                    "Winner",
                    "Base ran",
                    "Strict ran",
                    "Base quality",
                    "Strict quality",
                ],
                rows,
            ),
        ]
    )


def _paired_slice_table(
    slices: Mapping[str, Mapping[str, float | int]], title: str
) -> str:
    rows = []
    for key, data in slices.items():
        rows.append(
            "<tr>"
            f"<td>{_escape(key)}</td>"
            f"<td>{_escape(data.get('count'))}</td>"
            f"<td>{_escape(data.get('strictWins'))}</td>"
            f"<td>{_escape(data.get('baseWins'))}</td>"
            f"<td>{_escape(data.get('ties'))}</td>"
            f"<td>{float(data.get('meanDelta', 0.0)):+.3f}</td>"
            "</tr>"
        )
    return _table(
        [
            title,
            "Queries",
            "Strict wins",
            "Base wins",
            "Ties",
            "Mean delta",
        ],
        rows,
    )


def _case_section(query: QueryRun) -> str:
    verdict = "RAN" if query.passed else "FAIL"
    badge = "pass" if query.passed else "fail"
    scores = " ".join(
        f"{item.dimension[0]}={item.soft:.2f}"
        for item in query.verdict.scores
        if not item.error
    )
    warning = (
        ' <span class="badge warn">quality warning</span>'
        if query.quality_warning
        else ""
    )
    open_attr = "" if query.passed else " open"
    parts = [
        f'<details class="case"{open_attr}>',
        f'<summary><span class="badge {badge}">{verdict}</span> '
        f"{_escape(query.case.id)} "
        f'<span class="muted">{_escape(query.case.question)}</span> '
        f'<span class="muted">{_escape(scores)}</span>{warning}'
        "</summary>",
        '<div class="case-body">',
        f"<p><strong>Question</strong> {_escape(query.case.question)}</p>",
        '<h4>Answer</h4>',
        f'<pre class="answer">{_escape(_clip(query.answer))}</pre>',
        "<h4>G-Eval</h4>",
        *[_dimension_card(item) for item in query.verdict.scores],
        _verdict_error(query),
        _run_error(query),
        "<h4>Trace checks</h4>",
        _checks_table(query.checks),
        "<h4>Evidence</h4>",
        _evidence_list(query),
        "<h4>Timeline</h4>",
        _timeline_section(query),
        "<h4>Artifacts</h4>",
        _artifact_links(query),
        "</div></details>",
    ]
    return "\n".join(parts)


def _dimension_card(item: Any) -> str:
    if item.error:
        return (
            '<div class="dimension">'
            f"<h4>{_escape(item.dimension)} "
            '<span class="badge fail">error</span></h4>'
            f'<p class="warning">{_escape(item.error)}</p>'
            "</div>"
        )
    return (
        '<div class="dimension">'
        f"<h4>{_escape(item.dimension)} "
        f'<span class="score">{item.score} ({item.soft:.2f})</span></h4>'
        f"<p>{_escape(_clip(item.reason, 2_000))}</p>"
        "</div>"
    )


def _verdict_error(query: QueryRun) -> str:
    if not query.verdict.error:
        return ""
    return (
        '<p class="warning">'
        f"{_escape(query.verdict.error)}</p>"
    )


def _run_error(query: QueryRun) -> str:
    if not query.error:
        return ""
    return (
        '<details class="error"><summary>Run error</summary>'
        f"<pre>{_escape(query.error)}</pre></details>"
    )


def _checks_table(checks: Sequence[Any]) -> str:
    rows = []
    for check in checks:
        badge = "pass" if check.passed else "fail"
        verdict = "PASS" if check.passed else "FAIL"
        rows.append(
            "<tr>"
            f"<td>{_escape(check.name)}</td>"
            f'<td><span class="badge {badge}">{verdict}</span></td>'
            f"<td>{_value(check.expected)}</td>"
            f"<td>{_value(check.actual)}</td>"
            f"<td>{_escape(check.detail)}</td>"
            "</tr>"
        )
    if not rows:
        return '<p class="muted">No trace expectations for this case.</p>'
    return _table(
        ["Check", "Result", "Expected", "Actual", "Detail"], rows
    )


def _evidence_list(query: QueryRun) -> str:
    evidence = query.evidence
    propose = ", ".join(
        f"{job}/{interface}" for job, interface in evidence.propose_calls
    )
    errors = "; ".join(evidence.errors) or "—"
    rows = [
        ("Tools", ", ".join(evidence.tools) or "—"),
        ("Pages read", ", ".join(evidence.read_pages) or "—"),
        ("Tool rounds", str(evidence.tool_rounds)),
        (
            "Wait",
            "—"
            if evidence.wait_status is None
            else f"{evidence.wait_status} "
            f"{evidence.wait_job or ''}/{evidence.wait_interface or ''}".strip(),
        ),
        ("Propose calls", propose or "—"),
        ("Errors", errors),
        ("Tokens", f"{query.input_tokens} in / {query.output_tokens} out"),
        ("Latency", f"{query.latency_s:.1f}s"),
    ]
    body = "".join(
        f"<dt>{_escape(label)}</dt><dd>{_escape(value)}</dd>"
        for label, value in rows
    )
    return f'<dl class="config">{body}</dl>'


def _artifact_links(query: QueryRun) -> str:
    base = f"queries/{quote(query.case.id, safe='')}"
    return (
        "<ul>"
        f'<li><a href="{base}/session.jsonl">session.jsonl</a></li>'
        f'<li><a href="{base}/events.jsonl">events.jsonl</a></li>'
        f'<li><a href="{base}/case.json">case.json</a></li>'
        f'<li><a href="{base}/judge.json">judge.json</a></li>'
        "</ul>"
    )


def _trend_table(history: Sequence[Mapping[str, str]]) -> str:
    if not history:
        return '<p class="muted">No history yet.</p>'
    rows = []
    for row in history[-TREND_ROWS:]:
        rows.append(
            "<tr>"
            f"<td>{_escape(row.get('timestamp', ''))}</td>"
            f"<td>{_escape(row.get('variant', ''))}</td>"
            f"<td>{_escape(row.get('run_id', ''))}</td>"
            f"<td>{_escape(row.get('trace_passed', ''))}/"
            f"{_escape(row.get('trace_failed', ''))}</td>"
            f"<td>{_escape(row.get('grounding_mean', ''))}</td>"
            f"<td>{_escape(row.get('coverage_mean', ''))}</td>"
            f"<td>{_escape(row.get('uncertainty_mean', ''))}</td>"
            "</tr>"
        )
    return _table(
        [
            "Timestamp",
            "Variant",
            "Run",
            "Trace pass/fail",
            "Grounding",
            "Coverage",
            "Uncertainty",
        ],
        rows,
    )


def _timeline_section(query: QueryRun) -> str:
    items: list[str] = []
    for entry in query.entries:
        if not isinstance(entry, Mapping):
            continue
        kind = entry.get("type")
        role = entry.get("role")
        content = entry.get("content")
        if kind == "message":
            if role == "user" and isinstance(content, str) and content.strip():
                items.append(
                    _timeline_item("user", "user", _clip(content, 700))
                )
            elif role == "assistant":
                calls = entry.get("toolCalls")
                text = (
                    _clip(content, 700)
                    if isinstance(content, str) and content.strip()
                    else ""
                )
                if isinstance(calls, list) and calls:
                    names = ", ".join(
                        f"{call.get('name')}("
                        f"{_clip(str(call.get('arguments') or ''), 120)})"
                        for call in calls
                        if isinstance(call, Mapping)
                    )
                    body = (
                        f"{text} → tool calls: {names}"
                        if text
                        else f"tool calls: {names}"
                    )
                    items.append(
                        _timeline_item("assistant", "assistant", body)
                    )
                elif text:
                    items.append(
                        _timeline_item("assistant", "assistant", text)
                    )
            elif role == "toolResult" and isinstance(content, str):
                items.append(
                    _timeline_item(
                        "tool", "tool result", _clip(content, 500)
                    )
                )
        elif kind == "wait_proposed":
            items.append(
                _timeline_item(
                    "wait",
                    "wait",
                    "proposed: {}/{}".format(
                        entry.get("job"), entry.get("interface")
                    ),
                )
            )
        elif kind == "wait_completed":
            items.append(
                _timeline_item(
                    "wait",
                    "wait",
                    "completed: {}/{}".format(
                        entry.get("job"), entry.get("status")
                    ),
                )
            )
        elif kind == "wait_cancelled":
            items.append(_timeline_item("wait", "wait", "cancelled"))
        elif kind == "resume_started":
            items.append(_timeline_item("wait", "resume", "started"))
    for record in query.trace:
        if not isinstance(record, Mapping):
            continue
        if record.get("type") in ("run_failed", "run_cancelled"):
            items.append(
                _timeline_item(
                    "error",
                    str(record.get("type")),
                    str(record.get("code") or ""),
                )
            )
    if not items:
        return '<p class="muted">No timeline entries.</p>'
    return '<ul class="timeline">' + "".join(items) + "</ul>"


def _timeline_item(kind: str, who: str, body: str) -> str:
    return (
        f'<li class="{_escape(kind)}">'
        f'<span class="who">{_escape(who)}</span>{_escape(body)}</li>'
    )


def _table(headers: Sequence[str], rows: Sequence[str]) -> str:
    head = "".join(f"<th>{_escape(item)}</th>" for item in headers)
    return (
        f"<table><thead><tr>{head}</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _score_text(scores: Mapping[str, float]) -> str:
    return ", ".join(
        f"{dimension[0]}={scores[dimension]:.2f}"
        for dimension in DIMENSIONS
        if dimension in scores
    ) or "—"


def _quality_badge(warning: bool) -> str:
    if warning:
        return '<span class="badge warn">warning</span>'
    return "ok"


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _clip(text: str, limit: int = 4000) -> str:
    clipped = text.strip()
    if len(clipped) > limit:
        clipped = clipped[:limit] + "…"
    return clipped


def _value(value: object) -> str:
    if isinstance(value, str):
        return _escape(value)
    try:
        return _escape(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _escape(value)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = ["TREND_ROWS", "write_ab_report", "write_variant_report"]
