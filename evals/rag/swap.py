"""The module-ablation matrix: the reference and its single-module ablations.

Every row keeps the reference pipeline except the one module named by the row.
E04 needs its own whole-page index; E05–E08 are query-time and share the base
index. E09 is recorded as skipped rather than omitted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    CHANNEL_INDEX_KEY,
    IndexCache,
    IndexKey,
    Scope,
    base_index_builder,
    get_variant,
)

from noteagent.rag.embedding import EmbeddingClient

__all__ = [
    "SKIPPED_SWAPS",
    "SWAP_VARIANTS",
    "SwapRun",
    "run_swap_matrix",
    "swap_markdown",
    "swap_payload",
    "write_swap_matrix",
]

SWAP_VARIANTS = (
    ("hybrid", "E01 reference"),
    ("hybrid_e04_whole_page", "E04 whole-page"),
    ("hybrid_e05_max", "E05 max aggregation"),
    ("hybrid_e06_minmax", "E06 min-max + equal weight"),
    ("hybrid_e07_no_prefix", "E07 prefix off"),
    ("hybrid_e08_no_exact", "E08 exact tier off"),
)

SKIPPED_SWAPS = (
    ("E09 tokenizer", "skipped"),
)


@dataclass(frozen=True, slots=True)
class SwapRun:
    """One swap row in one scope."""

    variant: str
    label: str
    scope: Scope
    evaluation: Evaluation


def run_swap_matrix(
    dataset: Dataset,
    corpus_root: Path | str,
    *,
    embedder: EmbeddingClient,
    query_embedder: EmbeddingClient | None = None,
    index_key: IndexKey | None = None,
    indexes: IndexCache | None = None,
) -> tuple[SwapRun, ...]:
    """Run the reference and the five swap rows in both scopes."""
    if query_embedder is None:
        raise ValueError(
            "the module-ablation matrix needs a no-prefix query "
            "embedder for E07"
        )
    base_key = index_key or CHANNEL_INDEX_KEY
    cache = indexes or IndexCache(base_index_builder(embedder))
    runs: list[SwapRun] = []
    for name, label in SWAP_VARIANTS:
        variant = get_variant(name)
        key = (
            replace(base_key, chunking=variant.index_key.chunking)
            if variant.index_key is not None
            else base_key
        )
        for scope in (Scope.WIKI, Scope.WIKI_NOTES):
            runs.append(
                SwapRun(
                    variant=name,
                    label=label,
                    scope=scope,
                    evaluation=evaluate(
                        dataset,
                        corpus_root,
                        variant,
                        scope=scope,
                        indexes=cache,
                        embedder=embedder,
                        query_embedder=query_embedder,
                        index_key=key,
                    ),
                )
            )
    return tuple(runs)


def swap_payload(runs: tuple[SwapRun, ...]) -> dict[str, Any]:
    """The whole swap matrix as JSON, with the skipped row named."""
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
        "skipped": [
            {"row": row, "status": status}
            for row, status in SKIPPED_SWAPS
        ],
    }


def swap_markdown(runs: tuple[SwapRun, ...]) -> str:
    """The swap matrix as two tables plus the skipped row."""
    first = runs[0].evaluation
    lines = [
        "# Retrieval ablation — module ablation rows",
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
            "| Row | Variant | Queries | Recall@5 | Hit@5 | nDCG@10 |"
            " MRR | Build s | p50 ms | p95 ms |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines.extend(_table_rows(runs, Scope.WIKI))
    lines.extend(
        [
            "",
            "## Secondary (wiki+notes)",
            "",
            (
                "| Row | Variant | Queries | Recall@5 | Hit@5 | nDCG@10 |"
                " MRR | Build s | p50 ms | p95 ms |"
            ),
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    lines.extend(_table_rows(runs, Scope.WIKI_NOTES))
    lines.extend(["", "## Skipped", ""])
    for row, status in SKIPPED_SWAPS:
        lines.append(f"- {row}: {status}")
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


def write_swap_matrix(runs: tuple[SwapRun, ...], path: Path) -> Path:
    """Write the swap JSON and its Markdown summary; return the JSON."""
    return write_json_markdown(
        Path(path), swap_payload(runs), swap_markdown(runs)
    )


def _row(run: SwapRun) -> dict[str, Any]:
    evaluation = run.evaluation
    return {
        "variant": run.variant,
        "row": run.label,
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
    runs: tuple[SwapRun, ...], scope: Scope
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
            f"| {run.label} | {run.variant} | "
            f"{len(evaluation.results)} | "
            f"{metrics.recall_at_5:.3f} | {metrics.hit_at_5:.3f} | "
            f"{metrics.ndcg_at_10:.3f} | {metrics.mrr:.3f} | "
            f"{seconds:.3f} | {evaluation.latency.p50_ms:.1f} | "
            f"{evaluation.latency.p95_ms:.1f} |"
        )
    return rows
