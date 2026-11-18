"""The abstention gate: per-query signals and offline threshold curves.

Retrieval runs once per query: BM25 for the lexical coverage signal, vector
similarity for the cosine and margin signals, and the same two sides fused for
the hybrid ranking. Thresholds are then swept in memory, so the gate table
never changes the ranking tables and never re-embeds a query.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from evals.rag.base_index import BaseIndex
from evals.rag.dataset import Dataset, RagQuery, check_corpus
from evals.rag.metrics import recall_at_k
from evals.rag.report import index_row, write_json_markdown
from evals.rag.runner import RECALL_K, queries_for_scope
from evals.rag.variants import (
    CHANNEL_INDEX_KEY,
    DEPTH,
    IndexBuildRecord,
    IndexCache,
    IndexKey,
    Scope,
    base_index_builder,
    hybrid_retrieval,
    retrieval_from_hits,
    vector_retrieval,
)

from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import LexicalHit, tokenize
from noteagent.rag.chunker import KIND_WIKI
from noteagent.rag.embedding import EmbeddingClient
from noteagent.rag.vector import VectorHit

__all__ = [
    "COVERAGE_THRESHOLDS",
    "GateCurve",
    "GatePoint",
    "GateQuery",
    "GateReport",
    "SCORE_THRESHOLDS",
    "gate_markdown",
    "gate_payload",
    "run_gate",
    "write_gate",
]

#: Coverage is a ratio; the curve only needs a coarse grid.
COVERAGE_THRESHOLDS = tuple(index / 10 for index in range(11))
#: Cosine and margin are continuous; step 0.05 keeps the table readable.
SCORE_THRESHOLDS = tuple(index / 20 for index in range(21))

_SIGNAL_LABELS = {
    "coverage": "term coverage",
    "cosine": "top cosine",
    "margin": "top1−top2 margin",
}
_VARIANT_LABELS = {
    "bm25": "BM25-only",
    "vector": "Vector-only",
    "hybrid": "Hybrid",
}


@dataclass(frozen=True, slots=True)
class GateQuery:
    """One query's rankings and raw signals, before any threshold."""

    query: RagQuery
    rankings: dict[str, tuple[str, ...]]
    signals: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.query.id,
            "type": self.query.type,
            "gap": self.query.is_gap,
            "rankings": {
                name: list(ranked)
                for name, ranked in self.rankings.items()
            },
            "signals": {
                name: round(value, 6)
                for name, value in self.signals.items()
            },
        }


@dataclass(frozen=True, slots=True)
class GatePoint:
    """One threshold's trade-off point."""

    threshold: float
    answerable_recall_at_5: float
    gap_abstention: float
    answerable_gated: int

    def as_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "answerableRecallAt5": self.answerable_recall_at_5,
            "gapAbstention": self.gap_abstention,
            "answerableGated": self.answerable_gated,
        }


@dataclass(frozen=True, slots=True)
class GateCurve:
    """One variant's signal, swept across its thresholds."""

    variant: str
    signal: str
    points: tuple[GatePoint, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "signal": self.signal,
            "points": [point.as_dict() for point in self.points],
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    """A gate run: the raw per-query signals and the swept curves."""

    dataset: Dataset
    corpus: Path
    scope: Scope
    records: tuple[GateQuery, ...]
    curves: tuple[GateCurve, ...]
    index_builds: tuple[IndexBuildRecord, ...]


def run_gate(
    dataset: Dataset,
    corpus_root: Path | str,
    *,
    embedder: EmbeddingClient,
    index_key: IndexKey | None = None,
) -> GateReport:
    """Run retrieval once per query, record signals, then sweep thresholds."""
    scope = Scope.WIKI
    corpus = Path(corpus_root).resolve()
    check_corpus(dataset, corpus)
    layout = ProjectLayout(root=corpus)
    cache = IndexCache(base_index_builder(embedder))
    key = index_key or CHANNEL_INDEX_KEY
    build = cache.get(key, layout)
    base = cast(BaseIndex, build.index)
    queries = queries_for_scope(dataset.queries, scope)

    records: list[GateQuery] = []
    for query in queries:
        lexical = base.bm25.search(query.query, limit=DEPTH)
        vector = base.vector_hits(
            query.query,
            embedder,
            limit=max(DEPTH, len(base.chunks)),
        )
        lexical_wiki = [
            hit for hit in lexical if hit.kind == KIND_WIKI
        ]
        vector_wiki = [
            hit for hit in vector if hit.kind == KIND_WIKI
        ]
        records.append(
            GateQuery(
                query=query,
                rankings={
                    "bm25": retrieval_from_hits(lexical).for_type(
                        query.type
                    ),
                    "vector": vector_retrieval(
                        base, vector, query.query
                    ).for_type(query.type),
                    "hybrid": hybrid_retrieval(
                        lexical, vector
                    ).for_type(query.type),
                },
                signals={
                    "coverage": _coverage(
                        base, query.query, lexical_wiki
                    ),
                    **_vector_signals(vector_wiki),
                },
            )
        )

    curves = (
        _curve(records, "bm25", "coverage", COVERAGE_THRESHOLDS),
        _curve(records, "vector", "cosine", SCORE_THRESHOLDS),
        _curve(records, "vector", "margin", SCORE_THRESHOLDS),
        _curve(records, "hybrid", "cosine", SCORE_THRESHOLDS),
        _curve(records, "hybrid", "margin", SCORE_THRESHOLDS),
    )
    return GateReport(
        dataset=dataset,
        corpus=corpus,
        scope=scope,
        records=tuple(records),
        curves=curves,
        index_builds=cache.records(),
    )


def gate_payload(report: GateReport) -> dict[str, Any]:
    """The gate run as JSON: signals per query, curves, and the build cost."""
    gaps = [record for record in report.records if record.query.is_gap]
    return {
        "dataset": {
            "path": report.dataset.path.as_posix(),
            "sha256": report.dataset.sha256,
            "queries": len(report.dataset.queries),
            "byType": report.dataset.counts,
        },
        "corpus": report.corpus.as_posix(),
        "scope": report.scope.value,
        "gapQueries": len(gaps),
        "exploratory": True,
        "curves": [curve.as_dict() for curve in report.curves],
        "queries": [record.as_dict() for record in report.records],
        "indexBuilds": [
            build.as_dict() for build in report.index_builds
        ],
    }


def gate_markdown(report: GateReport) -> str:
    """The gate curves as one table per variant and signal."""
    gaps = [record for record in report.records if record.query.is_gap]
    resolution = f"1/{len(gaps)}" if gaps else "n/a"
    lines = [
        "# Retrieval ablation — gate curves",
        "",
        (
            f"- Dataset: `{report.dataset.path.as_posix()}` — "
            f"{len(report.dataset.queries)} queries, "
            f"sha256 `{report.dataset.sha256}`"
        ),
        f"- Corpus: `{report.corpus.as_posix()}`",
        f"- Scope: **{report.scope.value}**",
        (
            f"- Gap queries: {len(gaps)}; the abstention resolution is "
            f"{resolution}. **Exploratory**; no operating point is "
            "validated."
        ),
        "",
    ]
    for curve in report.curves:
        lines.extend(
            [
                (
                    f"## {_VARIANT_LABELS[curve.variant]} — "
                    f"{_SIGNAL_LABELS[curve.signal]}"
                ),
                "",
                (
                    "| Threshold | Answerable Recall@5 | Gap abstention |"
                    " Answerable gated |"
                ),
                "|---:|---:|---:|---:|",
            ]
        )
        for point in curve.points:
            lines.append(
                f"| {point.threshold:.2f} | "
                f"{point.answerable_recall_at_5:.3f} | "
                f"{point.gap_abstention:.3f} | "
                f"{point.answerable_gated} |"
            )
        lines.append("")
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
    return "\n".join(lines).rstrip() + "\n"


def write_gate(report: GateReport, path: Path) -> Path:
    """Write the gate JSON and its Markdown summary; return the JSON."""
    return write_json_markdown(
        Path(path), gate_payload(report), gate_markdown(report)
    )


def _curve(
    records: list[GateQuery],
    variant: str,
    signal: str,
    thresholds: tuple[float, ...],
) -> GateCurve:
    answerable = [
        record for record in records if not record.query.is_gap
    ]
    gaps = [record for record in records if record.query.is_gap]
    points: list[GatePoint] = []
    for threshold in thresholds:
        correct = 0.0
        gated = 0
        for record in answerable:
            if record.signals[signal] < threshold:
                gated += 1
                continue
            correct += recall_at_k(
                record.rankings[variant],
                record.query.expected,
                RECALL_K,
            )
        recall = correct / len(answerable) if answerable else 0.0
        abstained = sum(
            1 for record in gaps if record.signals[signal] < threshold
        )
        rate = abstained / len(gaps) if gaps else 0.0
        points.append(
            GatePoint(
                threshold=round(threshold, 2),
                answerable_recall_at_5=round(recall, 4),
                gap_abstention=round(rate, 4),
                answerable_gated=gated,
            )
        )
    return GateCurve(
        variant=variant,
        signal=signal,
        points=tuple(points),
    )


def _coverage(
    base: BaseIndex,
    query: str,
    lexical: Sequence[LexicalHit],
) -> float:
    """The share of query tokens in the whole top BM25 page."""
    terms = set(tokenize(query))
    if not terms or not lexical:
        return 0.0
    top = lexical[0]
    page_chunks = [
        chunk
        for chunk in base.chunks
        if chunk.kind == top.kind
        and chunk.page_id == top.page_id
    ]
    if page_chunks:
        text = " ".join(
            f"{chunk.title} {chunk.heading_path} {chunk.chunk_text}"
            for chunk in page_chunks
        )
    else:
        text = " ".join(
            (
                str(top.page_id),
                str(top.heading_path),
                str(top.chunk_text),
            )
        )
    return len(terms & set(tokenize(text))) / len(terms)


def _vector_signals(
    hits: Sequence[VectorHit],
) -> dict[str, float]:
    if not hits:
        return {"cosine": 0.0, "margin": 0.0}
    cosines = sorted(
        (hit.best.similarity for hit in hits), reverse=True
    )
    cosine = cosines[0]
    margin = cosine - cosines[1] if len(cosines) > 1 else cosine
    return {"cosine": cosine, "margin": margin}
