"""The module ablation rows: E04–E08, with E09 recorded as skipped."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from argparse import Namespace

from evals.rag.base_index import BaseIndex, EvalChunk, build_base_index
from evals.rag.cli import _embedding_config, main
from evals.rag.dataset import load_dataset
from evals.rag.runner import evaluate
from evals.rag.swap import (
    run_swap_matrix,
    swap_markdown,
    swap_payload,
)
from evals.rag.variants import (
    DEPTH,
    IndexKey,
    Scope,
    _weighted_keys,
    get_variant,
)
from rag_eval_support import full_rows, make_project, write, write_dataset

from noteagent.project.layout import ProjectLayout
from noteagent.rag.bm25 import Bm25Index, LexicalDoc, LexicalHit
from noteagent.rag.config import DEFAULT_QUERY_PREFIX
from noteagent.rag.faux import FakeEmbedder
from noteagent.rag.fusion import DEFAULT_K, fuse
from noteagent.rag.vector import ChunkScore, VectorHit

INDEX_KEY = IndexKey(
    chunking="heading", embedding_model="fake", embedding_dim=8
)


def hit(
    page_id: str,
    score: float,
    *,
    exact: bool = False,
    kind: str = "wiki",
) -> LexicalHit:
    return LexicalHit(
        chunk_id=f"{kind}:{page_id}#0",
        page_id=page_id,
        kind=kind,
        chunk_index=0,
        chunk_text="",
        heading_path="",
        score=score,
        matched=(),
        exact=exact,
    )


def vector_hit(
    page_id: str, score: float, *, kind: str = "wiki"
) -> VectorHit:
    return VectorHit(
        page_id=page_id,
        kind=kind,
        score=score,
        best=ChunkScore(
            chunk_id=f"{kind}:{page_id}#0",
            page_id=page_id,
            kind=kind,
            chunk_index=0,
            chunk_text="",
            heading_path="",
            similarity=score,
        ),
    )


class WholePageTests(unittest.TestCase):
    def test_whole_page_makes_one_chunk_where_heading_chunking_makes_many(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(
                root / "wiki" / "concepts" / "long.md",
                '---\ntitle: "Long"\n---\n\n'
                "## A\n"
                + "a" * 800
                + "\n\n## B\n"
                + "b" * 800
                + "\n\n## C\n"
                + "c" * 800
                + "\n",
            )
            layout = ProjectLayout(root=root)
            heading = build_base_index(
                layout, FakeEmbedder(dim=8), chunking="heading"
            )
            whole = build_base_index(
                layout, FakeEmbedder(dim=8), chunking="whole-page"
            )

        heading_page = [
            chunk
            for chunk in heading.chunks
            if chunk.page_id == "concepts/long"
        ]
        whole_page = [
            chunk
            for chunk in whole.chunks
            if chunk.page_id == "concepts/long"
        ]
        self.assertGreater(len(heading_page), 1)
        self.assertTrue(
            any(chunk.heading_path for chunk in heading_page)
        )
        self.assertEqual(len(whole_page), 1)
        self.assertEqual(whole_page[0].heading_path, "")


class SwapMatrixTests(unittest.TestCase):
    def build(self, root: Path):
        corpus = make_project(root)
        dataset = load_dataset(write_dataset(root), require_full=False)
        runs = run_swap_matrix(
            dataset,
            corpus,
            embedder=FakeEmbedder(dim=8),
            query_embedder=FakeEmbedder(dim=8),
            index_key=INDEX_KEY,
        )
        return runs

    def test_the_swap_matrix_has_six_rows_in_both_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = self.build(Path(tmp))

        self.assertEqual(len(runs), 12)
        main = [run for run in runs if run.scope is Scope.WIKI]
        secondary = [
            run for run in runs if run.scope is Scope.WIKI_NOTES
        ]
        self.assertEqual(len(main), 6)
        self.assertEqual(len(secondary), 6)
        self.assertEqual(
            {run.variant for run in main},
            {
                "hybrid",
                "hybrid_s1_whole_page",
                "hybrid_s2_max",
                "hybrid_s3_minmax",
                "hybrid_s4_no_prefix",
                "hybrid_s6_no_exact",
            },
        )

    def test_s1_uses_a_separate_whole_page_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = self.build(Path(tmp))

        by_key = {(run.variant, run.scope): run for run in runs}
        reference = by_key[("hybrid", Scope.WIKI)]
        whole = by_key[("hybrid_s1_whole_page", Scope.WIKI)]
        self.assertEqual(
            [build.key.chunking for build in reference.evaluation.index_builds],
            ["heading"],
        )
        self.assertEqual(
            [build.key.chunking for build in whole.evaluation.index_builds],
            ["whole-page"],
        )

    def test_s1_standalone_uses_the_whole_page_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )

            evaluation = evaluate(
                dataset,
                corpus,
                get_variant("hybrid_s1_whole_page"),
                scope=Scope.WIKI,
                embedder=FakeEmbedder(dim=8),
                index_key=INDEX_KEY,
            )

        self.assertEqual(
            [
                build.key.chunking
                for build in evaluation.index_builds
            ],
            ["whole-page"],
        )

    def test_the_payload_and_markdown_name_the_skipped_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs = self.build(Path(tmp))

        payload = swap_payload(runs)
        text = swap_markdown(runs)

        self.assertEqual(len(payload["main"]), 6)
        self.assertEqual(len(payload["secondary"]), 6)
        self.assertEqual(
            payload["skipped"],
            [{"row": "E09 tokenizer", "status": "skipped"}],
        )
        self.assertIn("E09 tokenizer", text)
        self.assertIn("Skipped", text)


class SwapVariantTests(unittest.TestCase):
    def test_s2_removes_the_tail_term(self) -> None:
        chunks = (
            EvalChunk(
                chunk_id="wiki:p#0",
                page_id="p",
                kind="wiki",
                chunk_index=0,
                chunk_text="a",
                heading_path="",
                title="P",
                embed_text="",
                vector=(0.8, 0.6),
            ),
            EvalChunk(
                chunk_id="wiki:p#1",
                page_id="p",
                kind="wiki",
                chunk_index=1,
                chunk_text="b",
                heading_path="",
                title="P",
                embed_text="",
                vector=(0.5, 0.8660254),
            ),
        )
        index = BaseIndex(
            chunks=chunks,
            bm25=Bm25Index([]),
            pages=(("p", "P"),),
            dim=2,
            size_bytes=0,
        )

        class UnitEmbedder:
            dim = 2

            def embed(
                self, texts: Sequence[str], *, query: bool = False
            ) -> list[tuple[float, float]]:
                return [(1.0, 0.0) for _ in texts]

        default = index.vector_hits("q", UnitEmbedder(), limit=10)
        maxed = index.vector_hits(
            "q", UnitEmbedder(), limit=10, tail_weight=0.0
        )

        self.assertGreater(default[0].score, maxed[0].score)

    def test_s3_equal_weight_keeps_pages_found_by_both_sides(self) -> None:
        lexical = [
            hit("a", 0.9),
            hit("b", 0.8),
            hit("c", 0.1),
        ]
        vector = [vector_hit("a", 0.9), vector_hit("b", 0.8)]

        keys = _weighted_keys(lexical, vector)

        self.assertEqual({key[1] for key in keys[:2]}, {"a", "b"})
        self.assertEqual(keys[-1][1], "c")

    def test_s3_applies_the_exact_tier_once(self) -> None:
        lexical = [
            hit("a", 0.1, exact=True),
            hit("b", 0.9),
        ]
        vector = [vector_hit("a", 0.1), vector_hit("b", 0.9)]

        without = _weighted_keys(lexical, vector)
        with_exact = _weighted_keys(
            lexical, vector, exact_pages={"a"}
        )

        self.assertEqual(without[0][1], "b")
        self.assertEqual(with_exact[0][1], "a")

    def test_s6_removes_the_whole_exact_tier(self) -> None:
        lexical = [
            hit("plain", 0.9),
            hit("named", 0.1, exact=True),
        ]

        with_exact = fuse(
            lexical, [], k=DEFAULT_K, candidates=DEPTH
        ).hits
        without_exact = fuse(
            lexical,
            [],
            k=DEFAULT_K,
            candidates=DEPTH,
            exact_tier=False,
        ).hits

        self.assertEqual(with_exact[0].page_id, "named")
        self.assertEqual(without_exact[0].page_id, "plain")

    def test_bm25_can_turn_the_exact_tier_off(self) -> None:
        index = Bm25Index(
            [
                LexicalDoc(
                    chunk_id="wiki:concepts/target#0",
                    page_id="concepts/target",
                    kind="wiki",
                    chunk_text="",
                    heading_path="",
                    title="target",
                    chunk_index=0,
                ),
                LexicalDoc(
                    chunk_id="wiki:concepts/other#0",
                    page_id="concepts/other",
                    kind="wiki",
                    chunk_text="target " * 40,
                    heading_path="",
                    title="other",
                    chunk_index=0,
                ),
            ]
        )

        with_exact = index.search("target", limit=10)
        without_exact = index.search(
            "target", limit=10, exact_tier=False
        )

        self.assertEqual(
            with_exact[0].page_id, "concepts/target"
        )
        self.assertEqual(
            without_exact[0].page_id, "concepts/other"
        )


class PrefixSwapTests(unittest.TestCase):
    def test_s4_uses_the_no_prefix_query_embedder(self) -> None:
        class TaggingEmbedder:
            dim = 2

            def __init__(self, query_vector: tuple[float, float]) -> None:
                self.query_vector = query_vector
                self.calls: list[tuple[tuple[str, ...], bool]] = []

            def embed(
                self, texts: Sequence[str], *, query: bool = False
            ) -> list[tuple[float, float]]:
                items = tuple(texts)
                self.calls.append((items, query))
                if query:
                    return [self.query_vector for _ in items]
                return [(0.0, 1.0) for _ in items]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )
            prefix = TaggingEmbedder((1.0, 0.0))
            no_prefix = TaggingEmbedder((0.0, 1.0))

            evaluation = evaluate(
                dataset,
                corpus,
                get_variant("hybrid_s4_no_prefix"),
                scope=Scope.WIKI,
                embedder=prefix,
                query_embedder=no_prefix,
                index_key=IndexKey(
                    chunking="heading",
                    embedding_model="tagging",
                    embedding_dim=2,
                ),
            )

        self.assertTrue(any(query for _, query in no_prefix.calls))
        self.assertFalse(any(query for _, query in prefix.calls))
        self.assertTrue(evaluation.results)


class CliConfigTests(unittest.TestCase):
    def test_the_query_embedder_config_strips_the_prefix(self) -> None:
        args = Namespace(
            embedding_endpoint="http://fake",
            embedding_model="fake",
            embedding_dim=8,
            embedding_api_key="",
        )

        self.assertEqual(
            _embedding_config(args).query_prefix,
            DEFAULT_QUERY_PREFIX,
        )
        self.assertEqual(
            _embedding_config(
                args, query_prefix=""
            ).query_prefix,
            "",
        )


class CliSwapTests(unittest.TestCase):
    def run_main(
        self, *args: str, **kwargs: object
    ) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args), **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_the_swap_matrix_success_path_writes_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())
            report = root / "swaps.json"

            code, out, err = self.run_main(
                "--matrix", "swaps",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(report),
                "--embedding-model", "fake",
                "--embedding-dim", "8",
                embedder=FakeEmbedder(dim=8),
                query_embedder=FakeEmbedder(dim=8),
            )
            stored = json.loads(report.read_text(encoding="utf-8"))
            summary = report.with_suffix(".md").read_text(
                encoding="utf-8"
            )

        self.assertEqual((code, err), (0, ""))
        self.assertIn("swap matrix", out)
        self.assertEqual(len(stored["main"]), 6)
        self.assertIn("E09 tokenizer", summary)


if __name__ == "__main__":
    unittest.main()
