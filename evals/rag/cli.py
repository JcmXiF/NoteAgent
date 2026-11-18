"""The ``python -m evals.rag`` command: run one variant and write the report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.rag.dataset import DatasetError, load_dataset
from evals.rag.report import write_report
from evals.rag.runner import evaluate
from evals.rag.variants import Scope, UnknownVariant

__all__ = ["main"]

DEFAULT_DATASET = ".scratch/rag/dataset/queries.jsonl"
DEFAULT_REPORT = "build/evals/rag/report.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.rag",
        description=(
            "Measure a retrieval variant against the annotated query set. "
            "Offline: no model, no network, no key."
        ),
    )
    parser.add_argument(
        "--variant",
        default="lexical",
        help="retrieval variant to run (default: lexical)",
    )
    parser.add_argument(
        "--scope",
        choices=[scope.value for scope in Scope],
        default=Scope.WIKI_NOTES.value,
        help="result kinds to allow (default: wiki+notes)",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help=f"JSONL eval set (default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--corpus",
        default="",
        help="project to search (default: the dataset's sibling corpus/)",
    )
    parser.add_argument(
        "--report",
        default=DEFAULT_REPORT,
        help=f"where to write JSON and Markdown (default: {DEFAULT_REPORT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dataset_path = Path(args.dataset)
    corpus = (
        Path(args.corpus)
        if args.corpus
        else dataset_path.parent / "corpus"
    )
    try:
        dataset = load_dataset(dataset_path)
        evaluation = evaluate(
            dataset,
            corpus,
            args.variant,
            scope=Scope(args.scope),
        )
    except (DatasetError, UnknownVariant) as err:
        print(err, file=sys.stderr)
        return 2

    written = write_report(evaluation, Path(args.report))
    metrics = evaluation.metrics
    latency = evaluation.latency
    print(
        f"{evaluation.variant.name}: {len(dataset.queries)} queries | "
        f"scope {evaluation.scope.value} | "
        f"Recall@5 {metrics.recall_at_5:.3f} | "
        f"Hit@5 {metrics.hit_at_5:.3f} | "
        f"nDCG@10 {metrics.ndcg_at_10:.3f} | "
        f"MRR {metrics.mrr:.3f}"
    )
    print(
        f"p50 {latency.p50_ms:.1f} ms | "
        f"p95 {latency.p95_ms:.1f} ms"
    )
    if evaluation.gap.count:
        gap = evaluation.gap
        print(
            f"gap: {gap.abstained}/{gap.count} abstained "
            f"({gap.rate:.0%})"
        )
    print(f"report: {written}")
    return 0
