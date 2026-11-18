"""The ``python -m evals.rag`` command: run one variant and write the report."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evals.rag.dataset import DatasetError, load_dataset
from evals.rag.matrix import run_channel_matrix, write_matrix
from evals.rag.report import write_report
from evals.rag.runner import evaluate
from evals.rag.variants import (
    IndexKey,
    Scope,
    UnknownVariant,
    get_variant,
)

from noteagent.rag.config import (
    DEFAULT_DIM,
    DEFAULT_MODEL,
    EmbeddingConfig,
    EmbeddingError,
)
from noteagent.rag.embedding import (
    EmbeddingClient,
    OpenAIEmbeddingClient,
)

__all__ = ["main"]

DEFAULT_DATASET = ".scratch/rag/dataset/queries.jsonl"
DEFAULT_REPORT = "build/evals/rag/report.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.rag",
        description=(
            "Measure a retrieval variant against the annotated query set. "
            "Lexical and BM25 are offline; vector and hybrid use the "
            "configured embedding endpoint."
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
        "--matrix",
        choices=["channel"],
        default=None,
        help=(
            "run the channel matrix: lexical, BM25-only, vector-only, and "
            "hybrid in both scopes"
        ),
    )
    parser.add_argument(
        "--embedding-endpoint",
        default=os.environ.get("NOTEAGENT_RAG_EMBEDDING_ENDPOINT", ""),
        help="OpenAI-compatible embedding endpoint for channel variants",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get(
            "NOTEAGENT_RAG_EMBEDDING_MODEL", DEFAULT_MODEL
        ),
        help=f"embedding model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--embedding-dim",
        type=int,
        default=DEFAULT_DIM,
        help=f"embedding dimension (default: {DEFAULT_DIM})",
    )
    parser.add_argument(
        "--embedding-api-key",
        default=os.environ.get("NOTEAGENT_RAG_EMBEDDING_API_KEY", ""),
        help="embedding endpoint API key, if it asks for one",
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


def _embedder(
    args: argparse.Namespace,
) -> OpenAIEmbeddingClient | None:
    endpoint = args.embedding_endpoint.strip()
    if not endpoint:
        return None
    return OpenAIEmbeddingClient(
        EmbeddingConfig(
            endpoint=endpoint,
            model=args.embedding_model,
            dim=args.embedding_dim,
            api_key=args.embedding_api_key,
        )
    )


def main(
    argv: list[str] | None = None,
    *,
    embedder: EmbeddingClient | None = None,
) -> int:
    args = _parser().parse_args(argv)
    dataset_path = Path(args.dataset)
    corpus = (
        Path(args.corpus)
        if args.corpus
        else dataset_path.parent / "corpus"
    )
    try:
        dataset = load_dataset(dataset_path)
        selected_embedder = embedder or _embedder(args)
        index_key = (
            IndexKey(
                chunking="heading",
                embedding_model=args.embedding_model,
                embedding_dim=args.embedding_dim,
            )
            if selected_embedder is not None
            else None
        )
        if args.matrix == "channel":
            if selected_embedder is None:
                print(
                    "the channel matrix needs an embedding endpoint",
                    file=sys.stderr,
                )
                return 2
            runs = run_channel_matrix(
                dataset,
                corpus,
                embedder=selected_embedder,
                index_key=index_key,
            )
            written = write_matrix(runs, Path(args.report))
            print(f"channel matrix: {len(runs)} runs")
            print(f"report: {written}")
            return 0
        variant = get_variant(args.variant)
        if variant.index_key is not None and selected_embedder is None:
            print(
                f"variant {args.variant!r} needs an embedding endpoint",
                file=sys.stderr,
            )
            return 2
        evaluation = evaluate(
            dataset,
            corpus,
            variant,
            scope=Scope(args.scope),
            embedder=selected_embedder,
            index_key=index_key,
        )
    except (DatasetError, UnknownVariant, EmbeddingError) as err:
        print(err, file=sys.stderr)
        return 2

    written = write_report(evaluation, Path(args.report))
    metrics = evaluation.metrics
    latency = evaluation.latency
    print(
        f"{evaluation.variant.name}: {len(evaluation.results)} queries | "
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
