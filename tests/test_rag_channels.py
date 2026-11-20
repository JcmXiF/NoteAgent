"""The channel ablation rows: base index, BM25, vector, and hybrid."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

from evals.rag.base_index import build_base_index
from evals.rag.cli import main
from evals.rag.dataset import load_dataset
from evals.rag.matrix import (
    MatrixRun,
    matrix_markdown,
    matrix_payload,
    run_channel_matrix,
    write_matrix,
)
from evals.rag.runner import Evaluation, QueryResult, evaluate
from evals.rag.variants import (
    IndexKey,
    Scope,
    Variant,
    get_variant,
)
from rag_eval_support import (
    NOTE_ID,
    full_rows,
    make_project,
    write,
    write_dataset,
)

from noteagent.project.layout import ProjectLayout
from noteagent.rag.faux import FakeEmbedder

INDEX_KEY = IndexKey(
    chunking="heading", embedding_model="fake", embedding_dim=8
)


def channel(name: str) -> Variant:
    return replace(get_variant(name), index_key=INDEX_KEY)


class BaseIndexTests(unittest.TestCase):
    def test_the_index_covers_wiki_pages_and_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(root=make_project(Path(tmp)))
            embedder = FakeEmbedder(dim=8)

            index = build_base_index(layout, embedder)

        self.assertEqual(len(index.chunks), 4)
        self.assertEqual(index.bm25.size, 4)
        self.assertIn(("concepts/entropy", "Entropy"), index.pages)
        self.assertEqual(index.dim, 8)
        note_chunk = next(
            chunk for chunk in index.chunks if chunk.kind == "note"
        )
        self.assertIn("复习计划", note_chunk.chunk_text)
        self.assertEqual(note_chunk.heading_path, "")
        self.assertIn("熵只依赖分布", note_chunk.embed_text)

    def test_bm25_and_exact_pages_find_the_named_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(root=make_project(Path(tmp)))
            index = build_base_index(layout, FakeEmbedder(dim=8))

            hits = index.bm25.search("entropy", limit=10)
            exact = index.exact_pages("entropy")

        self.assertEqual(hits[0].page_id, "concepts/entropy")
        self.assertEqual(exact, {"concepts/entropy"})

    def test_vector_hits_are_page_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = ProjectLayout(root=make_project(Path(tmp)))
            embedder = FakeEmbedder(dim=8)
            index = build_base_index(layout, embedder)

            hits = index.vector_hits("entropy", embedder, limit=10)

        self.assertTrue(hits)
        self.assertTrue(all(hit.kind in {"wiki", "note"} for hit in hits))


class ChannelVariantTests(unittest.TestCase):
    def run_variant(
        self, name: str, scope: Scope
    ) -> Evaluation:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            embedder = FakeEmbedder(dim=8)

            evaluation = evaluate(
                dataset,
                corpus,
                channel(name),
                scope=scope,
                embedder=embedder,
            )

        return evaluation

    def by_id(
        self, evaluation: Evaluation
    ) -> dict[str, QueryResult]:
        return {result.query.id: result for result in evaluation.results}

    def test_bm25_only_finds_the_exact_page_and_reports_the_build(self) -> None:
        evaluation = self.run_variant("bm25", Scope.WIKI)

        by_id = self.by_id(evaluation)
        self.assertEqual(
            by_id["exact-01"].ranked[0], "page:concepts/entropy"
        )
        self.assertEqual(len(evaluation.index_builds), 1)
        build = evaluation.index_builds[0]
        self.assertEqual(build.chunk_count, 4)
        self.assertEqual(build.embedding_calls, 1)

    def test_vector_only_keeps_the_exact_tier(self) -> None:
        evaluation = self.run_variant("vector", Scope.WIKI)

        self.assertEqual(
            self.by_id(evaluation)["exact-01"].ranked[0],
            "page:concepts/entropy",
        )

    def test_hybrid_finds_the_exact_page(self) -> None:
        evaluation = self.run_variant("hybrid", Scope.WIKI)

        self.assertEqual(
            self.by_id(evaluation)["exact-01"].ranked[0],
            "page:concepts/entropy",
        )

    def test_wiki_scope_excludes_notes_queries(self) -> None:
        wiki = self.run_variant("bm25", Scope.WIKI)
        both = self.run_variant("bm25", Scope.WIKI_NOTES)

        self.assertNotIn("notes-01", self.by_id(wiki))
        self.assertEqual(len(wiki.results), 4)
        notes = self.by_id(both)["notes-01"]
        self.assertEqual(notes.ranked[0], f"note:{NOTE_ID}")
        self.assertEqual(len(both.results), 5)


class FixedEmbedder:
    """The named page is orthogonal to the query; the rest align with it."""

    dim = 2

    def embed(
        self, texts: Sequence[str], *, query: bool = False
    ) -> list[tuple[float, float]]:
        if query:
            return [(1.0, 0.0) for _ in texts]
        return [
            (0.0, 1.0) if "target" in text.casefold() else (1.0, 0.0)
            for text in texts
        ]


class VectorExactTierTests(unittest.TestCase):
    def test_an_exact_page_outside_the_top_n_is_still_rescued(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(24):
                write(
                    root / "wiki" / "concepts" / f"page-{index:02d}.md",
                    f'---\ntitle: "Page {index}"\n---\n\nfiller\n',
                )
            write(
                root / "wiki" / "concepts" / "target.md",
                '---\ntitle: "Target"\n---\n\nfiller\n',
            )
            dataset_path = root / "queries.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "id": "exact-target",
                        "query": "target",
                        "type": "exact",
                        "expected_pages": ["concepts/target"],
                        "expected_notes": [],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            dataset = load_dataset(dataset_path, require_full=False)
            variant = replace(
                get_variant("vector"),
                index_key=IndexKey(
                    chunking="heading",
                    embedding_model="fixed",
                    embedding_dim=2,
                ),
            )
            evaluation = evaluate(
                dataset,
                root,
                variant,
                scope=Scope.WIKI,
                embedder=FixedEmbedder(),
            )

        self.assertEqual(
            evaluation.results[0].ranked[0],
            "page:concepts/target",
        )


class MatrixTests(unittest.TestCase):
    def build(self, root: Path) -> tuple[MatrixRun, ...]:
        corpus = make_project(root)
        dataset = load_dataset(write_dataset(root), require_full=False)
        embedder = FakeEmbedder(dim=8)
        return run_channel_matrix(
            dataset,
            corpus,
            embedder=embedder,
            index_key=INDEX_KEY,
        )

    def test_the_matrix_has_four_variants_in_both_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = self.build(Path(tmp))

        self.assertEqual(len(runs), 8)
        main = [run for run in runs if run.scope is Scope.WIKI]
        secondary = [
            run for run in runs if run.scope is Scope.WIKI_NOTES
        ]
        self.assertEqual(
            {run.variant for run in main},
            {"lexical", "bm25", "vector", "hybrid"},
        )
        self.assertTrue(all(len(run.evaluation.results) == 4 for run in main))
        self.assertTrue(
            all(len(run.evaluation.results) == 5 for run in secondary)
        )
        self.assertTrue(
            any(run.evaluation.index_builds for run in main)
        )

    def test_the_payload_and_markdown_have_two_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = self.build(Path(tmp))

        payload = matrix_payload(runs)
        text = matrix_markdown(runs)

        self.assertEqual(len(payload["main"]), 4)
        self.assertEqual(len(payload["secondary"]), 4)
        self.assertIn("Main (wiki-only)", text)
        self.assertIn("Secondary (wiki+notes)", text)

    def test_write_matrix_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = self.build(root)
            target = root / "out" / "matrix.json"

            written = write_matrix(runs, target)

            stored = json.loads(written.read_text(encoding="utf-8"))
            summary = written.with_suffix(".md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(written, target)
        self.assertEqual(len(stored["main"]), 4)
        self.assertEqual(len(stored["indexBuilds"]), 1)
        self.assertIn("Main (wiki-only)", summary)


class CliChannelTests(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def run_main_with_embedder(
        self, embedder: FakeEmbedder, *args: str
    ) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args), embedder=embedder)
        return code, out.getvalue(), err.getvalue()

    def build(self, root: Path) -> tuple[Path, Path]:
        corpus = make_project(root)
        dataset = write_dataset(root, full_rows())
        return corpus, dataset

    def test_a_channel_variant_needs_an_embedding_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus, dataset = self.build(root)

            code, _out, err = self.run_main(
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--variant", "hybrid",
            )

        self.assertEqual(code, 2)
        self.assertIn("embedding", err.lower())

    def test_the_channel_matrix_needs_an_embedding_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus, dataset = self.build(root)

            code, _out, err = self.run_main(
                "--matrix", "channel",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(root / "matrix.json"),
            )

        self.assertEqual(code, 2)
        self.assertIn("embedding", err.lower())

    def test_the_matrix_success_path_writes_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus, dataset = self.build(root)
            report = root / "matrix.json"

            code, out, err = self.run_main_with_embedder(
                FakeEmbedder(dim=8),
                "--matrix", "channel",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(report),
                "--embedding-model", "fake",
                "--embedding-dim", "8",
            )
            stored = json.loads(report.read_text(encoding="utf-8"))
            summary = report.with_suffix(".md").read_text(
                encoding="utf-8"
            )

        self.assertEqual((code, err), (0, ""))
        self.assertIn("channel matrix", out)
        self.assertEqual(len(stored["main"]), 4)
        self.assertEqual(stored["main"][0]["queriesRun"], 19)
        self.assertIn("Secondary (wiki+notes)", summary)


if __name__ == "__main__":
    unittest.main()
