"""The retrieval eval harness: prepared variants, scope, and index cost."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from evals.rag.cli import main
from evals.rag.dataset import load_dataset
from evals.rag.report import payload
from evals.rag.runner import evaluate, run_queries
from evals.rag.variants import (
    FnRetriever,
    IndexBuild,
    IndexCache,
    IndexKey,
    Retrieval,
    Scope,
    Variant,
)
from rag_eval_support import (
    NOTE_ID,
    ROWS,
    full_rows,
    make_project,
    write_dataset,
)

from noteagent.project.layout import ProjectLayout

INDEX_KEY = IndexKey(
    chunking="heading", embedding_model="fake", embedding_dim=8
)


class Builder:
    """A fake index builder that records what it was asked to build."""

    def __init__(self) -> None:
        self.calls: list[tuple[IndexKey, Path]] = []

    def __call__(
        self, key: IndexKey, layout: ProjectLayout
    ) -> IndexBuild:
        self.calls.append((key, layout.root))
        return IndexBuild(
            index={"key": key},
            seconds=0.25,
            embedding_calls=2,
            embedding_tokens=200,
            chunk_count=42,
            size_bytes=1024,
        )


def fake_variant(
    prepare_calls: list[Scope],
    retrieve_calls: list[str],
) -> Variant:
    def prepare(context) -> FnRetriever:
        prepare_calls.append(context.scope)
        assert context.index_key is not None
        context.indexes.get(context.index_key, context.layout)

        def retrieve(query: str) -> Retrieval:
            retrieve_calls.append(query)
            return Retrieval(
                pages=("concepts/entropy",), notes=(NOTE_ID,)
            )

        return FnRetriever(retrieve)

    return Variant(
        name="fake",
        description="a prepared fake variant",
        index_key=INDEX_KEY,
        prepare=prepare,
    )


class IndexCacheTests(unittest.TestCase):
    def test_a_key_is_built_once_and_shared(self) -> None:
        builder = Builder()
        cache = IndexCache(builder)
        layout = ProjectLayout(root=Path("."))
        other = IndexKey(
            chunking="whole-page", embedding_model="fake", embedding_dim=8
        )

        first = cache.get(INDEX_KEY, layout)
        second = cache.get(INDEX_KEY, layout)
        third = cache.get(other, layout)

        self.assertIs(first, second)
        self.assertIsNot(first, third)
        self.assertEqual([key for key, _ in builder.calls], [INDEX_KEY, other])
        self.assertEqual(
            [record.key for record in cache.records()],
            [INDEX_KEY, other],
        )
        self.assertEqual(cache.records()[0].chunk_count, 42)


class PreparedVariantTests(unittest.TestCase):
    def test_prepare_runs_once_and_each_query_runs_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            prepare_calls: list[Scope] = []
            retrieve_calls: list[str] = []
            builder = Builder()

            results = run_queries(
                dataset.queries,
                corpus,
                fake_variant(prepare_calls, retrieve_calls),
                scope=Scope.WIKI,
                indexes=IndexCache(builder),
            )

        self.assertEqual(prepare_calls, [Scope.WIKI])
        self.assertEqual(len(retrieve_calls), len(ROWS))
        self.assertEqual(len(builder.calls), 1)
        self.assertEqual(len(results), len(ROWS))


class ScopeTests(unittest.TestCase):
    def run_scope(self, scope: Scope):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            builder = Builder()
            results = run_queries(
                dataset.queries,
                corpus,
                fake_variant([], []),
                scope=scope,
                indexes=IndexCache(builder),
            )
        return {result.query.id: result for result in results}

    def test_wiki_excludes_notes(self) -> None:
        by_id = self.run_scope(Scope.WIKI)

        self.assertEqual(by_id["exact-01"].ranked[0], "page:concepts/entropy")
        self.assertEqual(by_id["notes-01"].ranked, ())
        self.assertEqual(by_id["notes-01"].outcome, "miss")

    def test_wiki_notes_includes_notes(self) -> None:
        by_id = self.run_scope(Scope.WIKI_NOTES)

        self.assertEqual(
            by_id["notes-01"].ranked[0], f"note:{NOTE_ID}"
        )
        self.assertEqual(by_id["notes-01"].outcome, "hit")

    def test_a_string_scope_is_accepted(self) -> None:
        by_id = self.run_scope("wiki")  # type: ignore[arg-type]

        self.assertEqual(by_id["notes-01"].ranked, ())


class SharedCacheTests(unittest.TestCase):
    def test_a_shared_cache_only_reports_new_builds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            builder = Builder()
            cache = IndexCache(builder)

            first = evaluate(
                dataset,
                corpus,
                fake_variant([], []),
                scope=Scope.WIKI,
                indexes=cache,
            )
            second = evaluate(
                dataset,
                corpus,
                fake_variant([], []),
                scope=Scope.WIKI,
                indexes=cache,
            )

        self.assertEqual(len(payload(first)["indexBuilds"]), 1)
        self.assertEqual(payload(second)["indexBuilds"], [])
        self.assertEqual(len(builder.calls), 1)


class EvaluationReportTests(unittest.TestCase):
    def test_the_report_records_scope_and_index_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            builder = Builder()

            evaluation = evaluate(
                dataset,
                corpus,
                fake_variant([], []),
                scope=Scope.WIKI,
                indexes=IndexCache(builder),
            )
            report = payload(evaluation)

        self.assertEqual(report["scope"], "wiki")
        self.assertEqual(len(report["indexBuilds"]), 1)
        build = report["indexBuilds"][0]
        self.assertEqual(build["chunkCount"], 42)
        self.assertEqual(build["embeddingCalls"], 2)
        self.assertEqual(build["embeddingTokens"], 200)
        self.assertEqual(build["sizeBytes"], 1024)
        self.assertGreaterEqual(build["seconds"], 0.0)


class CliScopeTests(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def build(self, root: Path) -> tuple[Path, Path]:
        corpus = make_project(root)
        dataset = write_dataset(root, full_rows())
        return corpus, dataset

    def test_wiki_scope_hides_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus, dataset = self.build(root)
            report = root / "wiki.json"

            code, _out, err = self.run_main(
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(report),
                "--variant", "lexical",
                "--scope", "wiki",
            )

            stored = json.loads(report.read_text(encoding="utf-8"))
            notes = next(
                item
                for item in stored["queries"]
                if item["id"] == "notes-01"
            )

        self.assertEqual((code, err), (0, ""))
        self.assertEqual(stored["scope"], "wiki")
        self.assertEqual(notes["outcome"], "miss")

    def test_wiki_notes_keeps_the_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus, dataset = self.build(root)
            report = root / "notes.json"

            code, _out, err = self.run_main(
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(report),
                "--variant", "lexical",
                "--scope", "wiki+notes",
            )

            stored = json.loads(report.read_text(encoding="utf-8"))
            notes = next(
                item
                for item in stored["queries"]
                if item["id"] == "notes-01"
            )

        self.assertEqual((code, err), (0, ""))
        self.assertEqual(stored["scope"], "wiki+notes")
        self.assertEqual(notes["outcome"], "hit")


if __name__ == "__main__":
    unittest.main()
