"""The channel matrix: four variants in both scopes.

The main table is wiki-only (the production scope); the secondary table is
wiki+notes (the experiment scope). One index cache is shared across the eight
runs, so the base index is built once and its cost is attributed to the run
that built it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.rag.dataset import Dataset
from evals.rag.report import (
    index_row,
    metrics_payload,
    query_results,
    write_json_markdown,
)
from evals.rag.runner import Evaluation, evaluate
from evals.rag.variants import (
    IndexCache,
    IndexKey,
    Scope,
    base_index_builder,
    get_variant,
)

from noteagent.rag.embedding import EmbeddingClient

__all__ = [
    "CHANNEL_VARIANTS",
    "MatrixRun",
    "matrix_markdown",
    "matrix_payload",
    "run_channel_matrix",
    "write_matrix",
]

CHANNEL_VARIANTS = ("lexical", "bm25", "vector", "hybrid")


@dataclass(frozen=True, slots=True)
class MatrixRun:
    """One variant in one scope."""

    variant: str
    scope: Scope
    evaluation: Evaluation


def run_channel_matrix(
    dataset: Dataset,
    corpus_root: Path | str,
    *,
    embedder: EmbeddingClient,
    index_key: IndexKey | None = None,
    indexes: IndexCache | None = None,
) -> tuple[MatrixRun, ...]:
    """Run lexical, BM25-only, vector-only, and hybrid in both scopes."""
    cache = indexes or IndexCache(base_index_builder(embedder))
    runs: list[MatrixRun] = []
    for name in CHANNEL_VARIANTS:
        variant = get_variant(name)
        for scope in (Scope.WIKI, Scope.WIKI_NOTES):
            runs.append(
                MatrixRun(
                    variant=name,
                    scope=scope,
                    evaluation=evaluate(
                        dataset,
                        corpus_root,
                        variant,
                        scope=scope,
                        indexes=cache,
                        embedder=embedder,
                        index_key=index_key,
                    ),
                )
            )
    return tuple(runs)


def matrix_payload(runs: tuple[MatrixRun, ...]) -> dict[str, Any]:
    """The whole matrix as JSON, with one row per variant and scope."""
    first = runs[0].evaluation
    return {
        "dataset": {
            "path": first.dataset.path.as_posix(),
            "sha256": first.dataset.sha256,
            "queries": len(first.dataset.queries),
            "byType": first.dataset.counts,
        },
        "corpus": first.corpus.as_posix(),
        "main": [
            _row(run) for run in runs if run.scope is Scope.WIKI
        ],
        "secondary": [
            _row(run)
            for run in runs
            if run.scope is Scope.WIKI_NOTES
        ],
        "indexBuilds": [
            build.as_dict()
            for run in runs
            for build in run.evaluation.index_builds
        ],
    }


def matrix_markdown(runs: tuple[MatrixRun, ...]) -> str:
    """The matrix as two tables a person can read side by side."""
    first = runs[0].evaluation
    lines = [
        "# Retrieval ablation — channel rows",
        "",
        (
            f"- Dataset: `{first.dataset.path.as_posix()}` — "
            f"{len(first.dataset.queries)} queries, "
            f"sha256 `{first.dataset.sha256}`"
        ),
        f"- Corpus: `{first.corpus.as_posix()}`",
        "",
        "## Main (wiki-only)",
        "",
        (
            "| Variant | Queries | Recall@5 | Hit@5 | nDCG@10 | MRR |"
            " Build s | p50 ms | p95 ms |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_table_rows(runs, Scope.WIKI))
    lines.extend(
        [
            "",
            "## Secondary (wiki+notes)",
            "",
            (
                "| Variant | Queries | Recall@5 | Hit@5 | nDCG@10 | MRR |"
                " Build s | p50 ms | p95 ms |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    lines.extend(_table_rows(runs, Scope.WIKI_NOTES))
    builds = [
        build
        for run in runs
        for build in run.evaluation.index_builds
    ]
    if builds:
        lines.extend(
            [
                "",
                "## Index builds",
                "",
                (
                    "| Chunking | Model | Dim | Seconds | Calls |"
                    " Tokens | Chunks | Bytes |"
                ),
                "|---|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for build in builds:
            lines.append(index_row(build))
    return "\n".join(lines) + "\n"


def write_matrix(runs: tuple[MatrixRun, ...], path: Path) -> Path:
    """Write the matrix JSON and its Markdown summary; return the JSON."""
    return write_json_markdown(
        Path(path), matrix_payload(runs), matrix_markdown(runs)
    )


def _row(run: MatrixRun) -> dict[str, Any]:
    evaluation = run.evaluation
    return {
        "variant": run.variant,
        "scope": run.scope.value,
        "queriesRun": len(evaluation.results),
        "metrics": metrics_payload(evaluation),
        "latency": evaluation.latency.as_dict(),
        "queries": query_results(evaluation),
        "indexBuilds": [
            build.as_dict() for build in evaluation.index_builds
        ],
    }


def _table_rows(
    runs: tuple[MatrixRun, ...], scope: Scope
) -> list[str]:
    rows: list[str] = []
    for run in runs:
        if run.scope is not scope:
            continue
        evaluation = run.evaluation
        metrics = evaluation.metrics
        seconds = sum(
            build.seconds for build in evaluation.index_builds
        )
        rows.append(
            f"| {run.variant} | {len(evaluation.results)} | "
            f"{metrics.recall_at_5:.3f} | {metrics.hit_at_5:.3f} | "
            f"{metrics.ndcg_at_10:.3f} | {metrics.mrr:.3f} | "
            f"{seconds:.3f} | {evaluation.latency.p50_ms:.1f} | "
            f"{evaluation.latency.p95_ms:.1f} |"
        )
    return rows
