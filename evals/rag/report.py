"""Write a retrieval run for two readers: a machine and a person.

The JSON keeps every ranked list so a difference between two runs can be traced
to a query; the Markdown puts the four metrics, the per-type buckets and the
gap queries where a person can read them without opening the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.rag.metrics import MetricSet
from evals.rag.runner import Evaluation, QueryResult
from evals.rag.variants import IndexBuildRecord

__all__ = [
    "REPORT_VERSION",
    "index_row",
    "markdown",
    "metrics_payload",
    "payload",
    "query_results",
    "write_json_markdown",
    "write_report",
]

REPORT_VERSION = 1


def payload(evaluation: Evaluation) -> dict[str, Any]:
    """The run as JSON: configuration, metrics, and every query's result."""
    return {
        "version": REPORT_VERSION,
        "variant": {
            "name": evaluation.variant.name,
            "description": evaluation.variant.description,
        },
        "scope": evaluation.scope.value,
        "baseline": evaluation.variant.name == "lexical",
        "dataset": {
            "path": evaluation.dataset.path.as_posix(),
            "sha256": evaluation.dataset.sha256,
            "queries": len(evaluation.dataset.queries),
            "byType": evaluation.dataset.counts,
        },
        "queriesRun": len(evaluation.results),
        "corpus": evaluation.corpus.as_posix(),
        "startedAt": evaluation.started_at,
        "durationSeconds": round(evaluation.duration_s, 3),
        "metrics": metrics_payload(evaluation),
        "latency": evaluation.latency.as_dict(),
        "indexBuilds": [
            build.as_dict() for build in evaluation.index_builds
        ],
        "queries": query_results(evaluation),
    }


def query_results(evaluation: Evaluation) -> list[dict[str, Any]]:
    """Every query's ranked result, the part a diff can be traced through."""
    return [
        _query_payload(result) for result in evaluation.results
    ]


def metrics_payload(evaluation: Evaluation) -> dict[str, Any]:
    """The metric block shared by a single run and the matrix rows."""
    return {
        "overall": evaluation.metrics.as_dict(),
        "byType": {
            name: metrics.as_dict()
            for name, metrics in evaluation.metrics_by_type().items()
        },
        "gap": evaluation.gap.as_dict(),
    }


def markdown(evaluation: Evaluation) -> str:
    """The run as a short report: headline, per-type table, per-query table."""
    metrics = evaluation.metrics
    latency = evaluation.latency
    gap = evaluation.gap
    lines = [
        f"# Retrieval eval — {evaluation.variant.name}",
        "",
        (
            f"- Dataset: `{evaluation.dataset.path.as_posix()}` — "
            f"{len(evaluation.dataset.queries)} queries, "
            f"sha256 `{evaluation.dataset.sha256}`"
        ),
        f"- Corpus: `{evaluation.corpus.as_posix()}`",
        (
            f"- Variant: **{evaluation.variant.name}** — "
            f"{evaluation.variant.description}"
        ),
        f"- Scope: **{evaluation.scope.value}**",
        (
            f"- Result: **Recall@5 {metrics.recall_at_5:.3f} · "
            f"Hit@5 {metrics.hit_at_5:.3f} · "
            f"nDCG@10 {metrics.ndcg_at_10:.3f} · "
            f"MRR {metrics.mrr:.3f}**"
        ),
        (
            f"- Latency: p50 {latency.p50_ms:.1f} ms · "
            f"p95 {latency.p95_ms:.1f} ms"
        ),
        (
            f"- Gap queries: {gap.abstained}/{gap.count} abstained "
            f"({gap.rate:.0%})"
        ),
        "",
    ]
    if evaluation.index_builds:
        lines.extend(
            [
                "## Index builds",
                "",
                (
                    "| Chunking | Model | Dim | Seconds | Calls |"
                    " Tokens | Chunks | Bytes |"
                ),
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for build in evaluation.index_builds:
            lines.append(index_row(build))
        lines.append("")
    lines.extend(
        [
            "## By query type",
            "",
            "| Type | Queries | Recall@5 | Hit@5 | nDCG@10 | MRR |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, bucket in evaluation.metrics_by_type().items():
        lines.append(_metric_row(name, bucket))

    lines.extend(
        [
            "",
            "## Queries",
            "",
            "| Query | Type | Expected | Outcome | First rank | Latency |",
            "|---|---|---|---|---:|---:|",
        ]
    )
    for result in evaluation.results:
        lines.append(_query_row(result))
    return "\n".join(lines) + "\n"


def write_json_markdown(
    target: Path, data: dict[str, Any], text: str
) -> Path:
    """Write a JSON document and its Markdown sibling; return the JSON."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    target.with_suffix(".md").write_text(text, encoding="utf-8")
    return target


def write_report(evaluation: Evaluation, path: Path) -> Path:
    """Write the run's JSON and Markdown summary; return the JSON."""
    return write_json_markdown(
        Path(path), payload(evaluation), markdown(evaluation)
    )


def _query_payload(result: QueryResult) -> dict[str, Any]:
    scored = not result.query.is_gap
    score = result.score() if scored else None
    return {
        "id": result.query.id,
        "type": result.query.type,
        "query": result.query.query,
        "expected": list(result.query.expected),
        "ranked": list(result.ranked),
        "firstRelevantRank": result.first_rank,
        "outcome": result.outcome,
        "recallAt5": round(score.recall, 3) if score else None,
        "hitAt5": round(score.hit, 3) if score else None,
        "ndcgAt10": round(score.ndcg, 3) if score else None,
        "mrr": round(score.mrr, 3) if score else None,
        "latencyMs": round(result.latency_ms, 3),
    }


def _metric_row(name: str, metrics: MetricSet) -> str:
    return (
        f"| {name} | {metrics.count} | {metrics.recall_at_5:.3f} | "
        f"{metrics.hit_at_5:.3f} | {metrics.ndcg_at_10:.3f} | "
        f"{metrics.mrr:.3f} |"
    )


def _query_row(result: QueryResult) -> str:
    expected = (
        ", ".join(f"`{item}`" for item in result.query.expected) or "—"
    )
    if result.query.is_gap:
        rank = "—"
        outcome = (
            result.outcome
            if result.abstained
            else f"{result.outcome.replace('_', ' ')} "
            f"({len(result.ranked)})"
        )
    else:
        rank = (
            str(result.first_rank)
            if result.first_rank is not None
            else "—"
        )
        outcome = result.outcome
    return (
        f"| `{result.query.id}` | {result.query.type} | {expected} | "
        f"{outcome} | {rank} | {result.latency_ms:.1f} ms |"
    )


def index_row(build: IndexBuildRecord) -> str:
    record = build.as_dict()
    return (
        f"| {record['chunking']} | {record['embeddingModel']} | "
        f"{record['embeddingDim']} | {record['seconds']:.3f} | "
        f"{record['embeddingCalls']} | {record['embeddingTokens']} | "
        f"{record['chunkCount']} | {record['sizeBytes']} |"
    )
