"""The abstention gate: signals, offline curves, and the separate report."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from evals.rag.base_index import BaseIndex, EvalChunk
from evals.rag.cli import main
from evals.rag.dataset import RagQuery, load_dataset
from evals.rag.gate import (
    GateQuery,
    GateReport,
    _coverage,
    _curve,
    _vector_signals,
    gate_markdown,
    gate_payload,
    run_gate,
    write_gate,
)
from evals.rag.variants import IndexKey
from rag_eval_support import full_rows, make_project, write_dataset

from noteagent.rag.bm25 import Bm25Index, LexicalHit
from noteagent.rag.faux import FakeEmbedder
from noteagent.rag.vector import ChunkScore, VectorHit

INDEX_KEY = IndexKey(
    chunking="heading", embedding_model="fake", embedding_dim=8
)


def hit(page_id: str, text: str) -> LexicalHit:
    return LexicalHit(
        chunk_id=f"wiki:{page_id}#0",
        page_id=page_id,
        kind="wiki",
        chunk_index=0,
        chunk_text=text,
        heading_path="",
        score=1.0,
        matched=(),
        exact=False,
    )


def vector_hit(page_id: str, score: float) -> VectorHit:
    return VectorHit(
        page_id=page_id,
        kind="wiki",
        score=score,
        best=ChunkScore(
            chunk_id=f"wiki:{page_id}#0",
            page_id=page_id,
            kind="wiki",
            chunk_index=0,
            chunk_text="",
            heading_path="",
            similarity=score,
        ),
    )


def page_index(*texts: str) -> BaseIndex:
    chunks = tuple(
        EvalChunk(
            chunk_id=f"wiki:p#{index}",
            page_id="p",
            kind="wiki",
            chunk_index=index,
            chunk_text=text,
            heading_path="",
            title="P",
            embed_text="",
            vector=(1.0,),
        )
        for index, text in enumerate(texts)
    )
    return BaseIndex(
        chunks=chunks,
        bm25=Bm25Index([]),
        pages=(("p", "P"),),
        dim=1,
        size_bytes=0,
    )


class SignalTests(unittest.TestCase):
    def test_coverage_is_the_share_of_query_terms_in_the_top_page(
        self,
    ) -> None:
        index = page_index("entropy", "foo")
        self.assertEqual(
            _coverage(index, "entropy", [hit("p", "entropy")]),
            1.0,
        )
        self.assertEqual(
            _coverage(
                index, "entropy foo", [hit("p", "entropy")]
            ),
            1.0,
        )
        self.assertEqual(
            _coverage(
                index, "entropy bar", [hit("p", "entropy")]
            ),
            0.5,
        )

    def test_coverage_is_zero_without_terms_or_hits(self) -> None:
        self.assertEqual(
            _coverage(page_index("x"), "", [hit("p", "x")]),
            0.0,
        )
        self.assertEqual(
            _coverage(page_index("x"), "entropy", []),
            0.0,
        )

    def test_vector_signals_are_top_and_margin(self) -> None:
        signals = _vector_signals(
            [vector_hit("a", 0.8), vector_hit("b", 0.5)]
        )
        self.assertAlmostEqual(signals["cosine"], 0.8)
        self.assertAlmostEqual(signals["margin"], 0.3)
        self.assertEqual(
            _vector_signals([vector_hit("a", 0.8)]),
            {"cosine": 0.8, "margin": 0.8},
        )
        self.assertEqual(
            _vector_signals([]),
            {"cosine": 0.0, "margin": 0.0},
        )

    def test_the_cosine_is_the_raw_chunk_similarity(self) -> None:
        aggregated = VectorHit(
            page_id="a",
            kind="wiki",
            score=0.9,
            best=ChunkScore(
                chunk_id="wiki:a#0",
                page_id="a",
                kind="wiki",
                chunk_index=0,
                chunk_text="",
                heading_path="",
                similarity=0.7,
            ),
        )

        self.assertEqual(
            _vector_signals([aggregated])["cosine"],
            0.7,
        )

    def test_the_margin_uses_the_two_highest_raw_cosines(self) -> None:
        first = VectorHit(
            page_id="a",
            kind="wiki",
            score=0.9,
            best=ChunkScore(
                chunk_id="wiki:a#0",
                page_id="a",
                kind="wiki",
                chunk_index=0,
                chunk_text="",
                heading_path="",
                similarity=0.5,
            ),
        )
        second = VectorHit(
            page_id="b",
            kind="wiki",
            score=0.8,
            best=ChunkScore(
                chunk_id="wiki:b#0",
                page_id="b",
                kind="wiki",
                chunk_index=0,
                chunk_text="",
                heading_path="",
                similarity=0.7,
            ),
        )

        signals = _vector_signals([first, second])

        self.assertEqual(signals["cosine"], 0.7)
        self.assertAlmostEqual(signals["margin"], 0.2)


class CurveTests(unittest.TestCase):
    def test_a_gated_answerable_query_counts_as_zero(self) -> None:
        answerable = GateQuery(
            query=RagQuery("a", "q", "exact", ("a",), ()),
            rankings={"bm25": ("page:a",)},
            signals={"coverage": 0.5},
        )
        gap = GateQuery(
            query=RagQuery("g", "q", "gap", (), ()),
            rankings={"bm25": ()},
            signals={"coverage": 0.2},
        )

        curve = _curve(
            [answerable, gap],
            "bm25",
            "coverage",
            (0.0, 0.6),
        )
        first, second = curve.points

        self.assertEqual(first.answerable_recall_at_5, 1.0)
        self.assertEqual(first.gap_abstention, 0.0)
        self.assertEqual(second.answerable_recall_at_5, 0.0)
        self.assertEqual(second.answerable_gated, 1)
        self.assertEqual(second.gap_abstention, 1.0)


class GateTests(unittest.TestCase):
    def build(self, root: Path) -> GateReport:
        corpus = make_project(root)
        dataset = load_dataset(write_dataset(root), require_full=False)
        report = run_gate(
            dataset,
            corpus,
            embedder=FakeEmbedder(dim=8),
            index_key=INDEX_KEY,
        )
        return report

    def test_the_wiki_scope_and_the_five_curves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self.build(Path(tmp))

        self.assertEqual(len(report.records), 4)
        self.assertNotIn("notes-01", [r.query.id for r in report.records])
        self.assertEqual(
            {(curve.variant, curve.signal) for curve in report.curves},
            {
                ("bm25", "coverage"),
                ("vector", "cosine"),
                ("vector", "margin"),
                ("hybrid", "cosine"),
                ("hybrid", "margin"),
            },
        )
        self.assertTrue(report.index_builds)

    def test_the_curve_starts_ungated_and_ends_fully_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self.build(Path(tmp))

        coverage = next(
            curve
            for curve in report.curves
            if curve.variant == "bm25"
        )
        self.assertEqual(coverage.points[0].answerable_gated, 0)
        self.assertEqual(coverage.points[0].gap_abstention, 0.0)
        self.assertEqual(coverage.points[-1].gap_abstention, 1.0)

    def test_the_payload_and_markdown_are_exploratory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self.build(Path(tmp))

        payload = gate_payload(report)
        text = gate_markdown(report)

        self.assertTrue(payload["exploratory"])
        self.assertEqual(payload["gapQueries"], 1)
        self.assertEqual(len(payload["queries"]), 4)
        first = payload["queries"][0]
        self.assertIn("signals", first)
        self.assertIn("rankings", first)
        self.assertIn("coverage", first["signals"])
        self.assertIn("Exploratory", text)
        self.assertIn("BM25-only", text)
        self.assertIn("Vector-only", text)
        self.assertIn("Hybrid", text)
        self.assertIn("top1−top2 margin", text)

    def test_write_gate_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = self.build(root)
            target = root / "out" / "gate.json"

            written = write_gate(report, target)

            stored = json.loads(written.read_text(encoding="utf-8"))
            summary = written.with_suffix(".md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(written, target)
        self.assertEqual(len(stored["curves"]), 5)
        self.assertIn("# Retrieval ablation — gate curves", summary)


class CliGateTests(unittest.TestCase):
    def run_main(
        self, *args: str, **kwargs: object
    ) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args), **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_the_gate_matrix_success_path_writes_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())
            report = root / "gate.json"

            code, out, err = self.run_main(
                "--matrix", "gate",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(report),
                "--embedding-model", "fake",
                "--embedding-dim", "8",
                embedder=FakeEmbedder(dim=8),
            )
            stored = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual((code, err), (0, ""))
        self.assertIn("gate curves", out)
        self.assertEqual(len(stored["curves"]), 5)
        self.assertTrue(stored["exploratory"])

    def test_the_gate_matrix_needs_an_embedding_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())

            code, _out, err = self.run_main(
                "--matrix", "gate",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(root / "gate.json"),
            )

        self.assertEqual(code, 2)
        self.assertIn("embedding", err.lower())


if __name__ == "__main__":
    unittest.main()
