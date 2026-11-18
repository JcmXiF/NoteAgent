"""The first-round orchestrator: channel rows, swaps, gate, and bootstrap.

One run shares an index cache across the three reports, so the heading index is
built once and the whole-page index once. The tracked ablation document is the
protocol and summary; the raw JSON and the full curves stay in the build area.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.rag.bootstrap import (
    BootstrapDelta,
    DEFAULT_LEVEL,
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    paired_bootstrap_delta,
)
from evals.rag.dataset import Dataset, QUERY_TYPES, corpus_sha256
from evals.rag.gate import (
    GateReport,
    SIGNAL_LABELS,
    VARIANT_LABELS,
    gate_payload,
    run_gate,
    write_gate,
)
from evals.rag.matrix import (
    MatrixRun,
    matrix_payload,
    run_channel_matrix,
    write_matrix,
)
from evals.rag.report import index_row, write_json_markdown
from evals.rag.runner import Evaluation
from evals.rag.swap import (
    SwapRun,
    run_swap_matrix,
    swap_payload,
    write_swap_matrix,
)
from evals.rag.variants import (
    IndexBuildRecord,
    IndexCache,
    IndexKey,
    Scope,
    base_index_builder,
)

from noteagent.rag.embedding import EmbeddingClient

__all__ = [
    "FirstRoundReport",
    "RowDelta",
    "ablation_markdown",
    "first_round_payload",
    "run_first_round",
    "write_first_round",
]

METRICS = ("recall", "hit", "ndcg", "mrr")
BUCKETS = tuple(name for name in QUERY_TYPES if name != "gap")
GATE_SAMPLE = (0.1, 0.2, 0.3, 0.4, 0.5)
DEFAULT_REPORT_PATH = Path("build/evals/rag/report.json")
DEFAULT_RAW_DIR = Path("build/evals/rag/ablation")
CHANNEL_LABELS = {
    "lexical": "lexical (historical)",
    "bm25": "BM25-only",
    "vector": "Vector-only",
    "hybrid": "Hybrid (reference)",
}


@dataclass(frozen=True, slots=True)
class _Measure:
    """One row's evaluation, normalized across the channel and swap reports."""

    variant: str
    label: str
    scope: Scope
    evaluation: Evaluation

    def scores(self) -> dict[str, Any]:
        return _scores(self.evaluation)

    def types(self) -> dict[str, str]:
        return _types(self.evaluation)


@dataclass(frozen=True, slots=True)
class RowDelta:
    """One row's paired delta from the hybrid reference for one metric."""

    table: str
    variant: str
    label: str
    scope: Scope
    metric: str
    delta: BootstrapDelta
    by_type: tuple[tuple[str, BootstrapDelta], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "variant": self.variant,
            "label": self.label,
            "scope": self.scope.value,
            "metric": self.metric,
            "delta": self.delta.as_dict(),
            "byType": {
                name: delta.as_dict()
                for name, delta in self.by_type
            },
        }


@dataclass(frozen=True, slots=True)
class FirstRoundReport:
    """Everything one first-round run produced."""

    dataset: Dataset
    corpus: Path
    channel: tuple[MatrixRun, ...]
    swap: tuple[SwapRun, ...]
    gate: GateReport
    deltas: tuple[RowDelta, ...]
    index_builds: tuple[IndexBuildRecord, ...]
    corpus_sha256: str
    seed: int


def run_first_round(
    dataset: Dataset,
    corpus_root: Path | str,
    *,
    embedder: EmbeddingClient,
    query_embedder: EmbeddingClient,
    index_key: IndexKey | None = None,
    seed: int = DEFAULT_SEED,
) -> FirstRoundReport:
    """Run the channel matrix, the swap matrix, and the gate on one cache."""
    corpus = Path(corpus_root).resolve()
    cache = IndexCache(base_index_builder(embedder))
    channel = run_channel_matrix(
        dataset,
        corpus,
        embedder=embedder,
        index_key=index_key,
        indexes=cache,
    )
    swap = run_swap_matrix(
        dataset,
        corpus,
        embedder=embedder,
        query_embedder=query_embedder,
        index_key=index_key,
        indexes=cache,
    )
    gate = run_gate(
        dataset,
        corpus,
        embedder=embedder,
        index_key=index_key,
        indexes=cache,
    )
    deltas = _row_deltas(
        _channel_measures(channel), "channel", seed
    ) + _row_deltas(_swap_measures(swap), "swap", seed)
    return FirstRoundReport(
        dataset=dataset,
        corpus=corpus,
        channel=channel,
        swap=swap,
        gate=gate,
        deltas=deltas,
        index_builds=cache.records(),
        corpus_sha256=corpus_sha256(corpus),
        seed=seed,
    )


def first_round_payload(report: FirstRoundReport) -> dict[str, Any]:
    """The full run as JSON: three matrices, paired deltas, and build cost."""
    return {
        "dataset": {
            "path": report.dataset.path.as_posix(),
            "sha256": report.dataset.sha256,
            "queries": len(report.dataset.queries),
            "byType": report.dataset.counts,
        },
        "corpus": report.corpus.as_posix(),
        "corpusSha256": report.corpus_sha256,
        "seed": report.seed,
        "bootstrap": {
            "resamples": DEFAULT_RESAMPLES,
            "level": DEFAULT_LEVEL,
            "pValues": False,
            "byTypeExploratory": True,
        },
        "channel": matrix_payload(report.channel),
        "swaps": swap_payload(report.swap),
        "gate": gate_payload(report.gate),
        "deltas": [delta.as_dict() for delta in report.deltas],
        "indexBuilds": [
            build.as_dict() for build in report.index_builds
        ],
    }


def ablation_markdown(
    report: FirstRoundReport,
    *,
    report_path: Path = DEFAULT_REPORT_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> str:
    """The tracked protocol and summary: two tables per matrix plus the gate."""
    lines = [
        "# Retrieval ablation — first round",
        "",
        (
            f"- Dataset: `{report.dataset.path.as_posix()}` — "
            f"{len(report.dataset.queries)} queries, "
            f"sha256 `{report.dataset.sha256}`"
        ),
        (
            f"- Corpus: `{_display_path(report.corpus)}` — "
            f"sha256 `{report.corpus_sha256}`"
        ),
        (
            "- Scope: main **wiki-only**; secondary **wiki+notes**. "
            "The notes queries are excluded from the main table."
        ),
        (
            f"- Bootstrap: paired, {DEFAULT_RESAMPLES:,} resamples, "
            f"{int(DEFAULT_LEVEL * 100)}% percentile CI, seed "
            f"{report.seed}; no p-values."
        ),
        (
            "- Exploratory: per-bucket CIs and gate curves; no "
            "operating point or decision rule is validated."
        ),
        "",
    ]
    channel = _channel_measures(report.channel)
    swap = _swap_measures(report.swap)
    channel_deltas = [
        delta for delta in report.deltas if delta.table == "channel"
    ]
    swap_deltas = [
        delta for delta in report.deltas if delta.table == "swap"
    ]
    _matrix_table(
        lines,
        "Channel rows",
        channel,
        channel_deltas,
    )
    _matrix_table(lines, "Swap rows", swap, swap_deltas)
    _gate_summary(lines, report.gate)
    if report.index_builds:
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
        for build in report.index_builds:
            lines.append(index_row(build))
        lines.append("")
    lines.extend(
        [
            "## Notes",
            "",
            (
                f"- Per-bucket CIs are in `{_display_path(report_path)}` "
                "under `deltas[].byType`; they are exploratory."
            ),
            (
                "- Gate curves are exploratory; their full points are in "
                f"`{_display_path(Path(raw_dir) / 'gate.json')}`."
            ),
            "- S5 tokenizer is skipped; `hybrid+rerank` is deferred.",
            "- No p-values; no decision rule is pre-registered.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_first_round(
    report: FirstRoundReport,
    *,
    ablation_path: Path,
    raw_dir: Path,
    report_path: Path,
) -> Path:
    """Write the tracked summary, the raw matrices, and the combined JSON."""
    raw = Path(raw_dir)
    raw.mkdir(parents=True, exist_ok=True)
    write_matrix(report.channel, raw / "channel.json")
    write_swap_matrix(report.swap, raw / "swaps.json")
    write_gate(report.gate, raw / "gate.json")
    text = ablation_markdown(
        report, report_path=Path(report_path), raw_dir=raw
    )
    ablation = Path(ablation_path)
    ablation.parent.mkdir(parents=True, exist_ok=True)
    ablation.write_text(text, encoding="utf-8")
    return write_json_markdown(
        Path(report_path), first_round_payload(report), text
    )


def _measure(
    run: MatrixRun | SwapRun, label: str
) -> _Measure:
    return _Measure(
        variant=run.variant,
        label=label,
        scope=run.scope,
        evaluation=run.evaluation,
    )


def _channel_measures(
    runs: tuple[MatrixRun, ...],
) -> tuple[_Measure, ...]:
    return tuple(
        _measure(run, CHANNEL_LABELS[run.variant])
        for run in runs
    )


def _swap_measures(
    runs: tuple[SwapRun, ...],
) -> tuple[_Measure, ...]:
    return tuple(_measure(run, run.label) for run in runs)


def _scores(evaluation: Evaluation) -> dict[str, Any]:
    return {
        result.query.id: result.score()
        for result in evaluation.scored
    }


def _types(evaluation: Evaluation) -> dict[str, str]:
    return {
        result.query.id: result.query.type
        for result in evaluation.results
    }


def _metric(score: Any, metric: str) -> float:
    return float(getattr(score, metric))


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def _paired(
    ids: list[str],
    row_scores: dict[str, Any],
    reference_scores: dict[str, Any],
    metric: str,
    seed: int,
) -> BootstrapDelta:
    return paired_bootstrap_delta(
        [_metric(row_scores[item], metric) for item in ids],
        [_metric(reference_scores[item], metric) for item in ids],
        seed=seed,
    )


def _row_deltas(
    measures: tuple[_Measure, ...],
    table: str,
    seed: int,
) -> tuple[RowDelta, ...]:
    deltas: list[RowDelta] = []
    for scope in (Scope.WIKI, Scope.WIKI_NOTES):
        scoped = [
            measure for measure in measures if measure.scope is scope
        ]
        reference = next(
            (
                measure
                for measure in scoped
                if measure.variant == "hybrid"
            ),
            None,
        )
        if reference is None:
            continue
        reference_scores = reference.scores()
        for measure in scoped:
            if measure.variant == "lexical":
                continue
            row_scores = measure.scores()
            ids = sorted(set(reference_scores) & set(row_scores))
            types = measure.types()
            for metric in METRICS:
                delta = _paired(
                    ids, row_scores, reference_scores, metric, seed
                )
                by_type: list[
                    tuple[str, BootstrapDelta]
                ] = []
                for query_type in QUERY_TYPES:
                    type_ids = [
                        item
                        for item in ids
                        if types.get(item) == query_type
                    ]
                    by_type.append(
                        (
                            query_type,
                            _paired(
                                type_ids,
                                row_scores,
                                reference_scores,
                                metric,
                                seed,
                            ),
                        )
                    )
                deltas.append(
                    RowDelta(
                        table=table,
                        variant=measure.variant,
                        label=measure.label,
                        scope=scope,
                        metric=metric,
                        delta=delta,
                        by_type=tuple(by_type),
                    )
                )
    return tuple(deltas)


def _matrix_table(
    lines: list[str],
    title: str,
    measures: tuple[_Measure, ...],
    deltas: tuple[RowDelta, ...],
) -> None:
    _table(
        lines,
        f"{title} — main (wiki-only)",
        measures,
        deltas,
        Scope.WIKI,
    )
    _bucket_table(
        lines,
        f"{title} — per-bucket Recall@5",
        measures,
        Scope.WIKI,
    )
    _table(
        lines,
        f"{title} — secondary (wiki+notes)",
        measures,
        deltas,
        Scope.WIKI_NOTES,
    )
    _bucket_table(
        lines,
        f"{title} — per-bucket Recall@5",
        measures,
        Scope.WIKI_NOTES,
    )


def _bucket_table(
    lines: list[str],
    title: str,
    measures: tuple[_Measure, ...],
    scope: Scope,
) -> None:
    scope_label = (
        "main (wiki-only)"
        if scope is Scope.WIKI
        else "secondary (wiki+notes)"
    )
    lines.extend(
        [
            f"## {title} — {scope_label}",
            "",
            "| Row | exact | paraphrase | cross_page | notes |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for measure in measures:
        if measure.scope is not scope:
            continue
        buckets = measure.evaluation.metrics_by_type()
        cells = [
            (
                f"{buckets[name].recall_at_5:.3f}"
                if name in buckets
                else "—"
            )
            for name in BUCKETS
        ]
        lines.append(
            f"| {measure.label} | " + " | ".join(cells) + " |"
        )
    lines.append("")


def _table(
    lines: list[str],
    title: str,
    measures: tuple[_Measure, ...],
    deltas: tuple[RowDelta, ...],
    scope: Scope,
) -> None:
    lookup = {
        (delta.variant, delta.metric): delta.delta
        for delta in deltas
        if delta.scope is scope
    }
    lines.extend(
        [
            f"## {title}",
            "",
            (
                "| Row | Queries | Recall@5 | Δ Recall@5 [95% CI] |"
                " Hit@5 | nDCG@10 | Δ nDCG@10 [95% CI] | MRR |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for measure in measures:
        if measure.scope is not scope:
            continue
        metrics = measure.evaluation.metrics
        recall = lookup.get((measure.variant, "recall"))
        ndcg = lookup.get((measure.variant, "ndcg"))
        lines.append(
            f"| {measure.label} | "
            f"{len(measure.evaluation.results)} | "
            f"{metrics.recall_at_5:.3f} | "
            f"{recall.cell() if recall else '—'} | "
            f"{metrics.hit_at_5:.3f} | "
            f"{metrics.ndcg_at_10:.3f} | "
            f"{ndcg.cell() if ndcg else '—'} | "
            f"{metrics.mrr:.3f} |"
        )
    lines.append("")


def _gate_summary(lines: list[str], gate: GateReport) -> None:
    lines.extend(
        [
            "## Gate curves (exploratory)",
            "",
            (
                "| Variant | Signal | Threshold | Answerable Recall@5 |"
                " Gap abstention | Answerable gated |"
            ),
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for curve in gate.curves:
        for point in curve.points:
            if round(point.threshold, 2) not in GATE_SAMPLE:
                continue
            lines.append(
                f"| {VARIANT_LABELS[curve.variant]} | "
                f"{SIGNAL_LABELS[curve.signal]} | "
                f"{point.threshold:.2f} | "
                f"{point.answerable_recall_at_5:.3f} | "
                f"{point.gap_abstention:.3f} | "
                f"{point.answerable_gated} |"
            )
    gap_count = sum(
        1 for record in gate.records if record.query.is_gap
    )
    lines.extend(
        [
            "",
            (
                f"Threshold sample; full points in the raw gate report; "
                f"{gap_count} gap queries; exploratory."
            ),
            "",
        ]
    )
