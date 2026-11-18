"""The first-round orchestrator: three matrices, deltas, and the report."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from evals.rag.cli import main
from evals.rag.dataset import load_dataset
from evals.rag.first_round import (
    FirstRoundReport,
    ablation_markdown,
    first_round_payload,
    run_first_round,
    write_first_round,
)
from evals.rag.variants import IndexKey
from rag_eval_support import full_rows, make_project, write_dataset

from noteagent.rag.faux import FakeEmbedder

INDEX_KEY = IndexKey(
    chunking="heading", embedding_model="fake", embedding_dim=8
)


def build(root: Path) -> FirstRoundReport:
    corpus = make_project(root)
    dataset = load_dataset(write_dataset(root), require_full=False)
    report = run_first_round(
        dataset,
        corpus,
        embedder=FakeEmbedder(dim=8),
        query_embedder=FakeEmbedder(dim=8),
        index_key=INDEX_KEY,
    )
    return report


class FirstRoundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.report = build(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_the_three_matrices_share_two_index_builds(self) -> None:
        report = self.report

        self.assertEqual(len(report.channel), 8)
        self.assertEqual(len(report.swap), 12)
        self.assertEqual(len(report.gate.curves), 5)
        self.assertEqual(
            [build.key.chunking for build in report.index_builds],
            ["heading", "whole-page"],
        )
        self.assertTrue(report.deltas)

    def test_the_payload_has_matrices_deltas_and_bootstrap(self) -> None:
        report = self.report

        payload = first_round_payload(report)

        self.assertEqual(payload["seed"], report.seed)
        self.assertTrue(payload["corpusSha256"])
        self.assertFalse(payload["bootstrap"]["pValues"])
        self.assertEqual(payload["bootstrap"]["resamples"], 10_000)
        self.assertEqual(len(payload["channel"]["main"]), 4)
        self.assertEqual(len(payload["swaps"]["main"]), 6)
        self.assertEqual(len(payload["gate"]["curves"]), 5)
        self.assertEqual(len(payload["indexBuilds"]), 2)
        self.assertIn("queries", payload["channel"]["main"][0])
        self.assertNotIn(
            "lexical",
            {delta["variant"] for delta in payload["deltas"]},
        )
        self.assertTrue(
            any(
                delta["metric"] == "recall"
                and delta["scope"] == "wiki"
                for delta in payload["deltas"]
            )
        )
        first = payload["deltas"][0]
        self.assertIn("byType", first)

    def test_the_markdown_is_the_protocol_and_summary(self) -> None:
        report = self.report

        text = ablation_markdown(report)

        self.assertIn("# Retrieval ablation — first round", text)
        self.assertIn("Channel rows — main (wiki-only)", text)
        self.assertIn("Channel rows — secondary (wiki+notes)", text)
        self.assertIn("Swap rows — main (wiki-only)", text)
        self.assertIn("Gate curves (exploratory)", text)
        self.assertIn("Δ Recall@5 [95% CI]", text)
        self.assertIn("Hit@5", text)
        self.assertIn("MRR", text)
        self.assertIn("per-bucket Recall@5", text)
        self.assertIn("10,000 resamples", text)
        self.assertIn("S5 tokenizer is skipped", text)
        self.assertIn("Exploratory", text)

    def test_write_first_round_writes_all_artifacts(self) -> None:
        report = self.report
        out = self.root / "out"
        ablation = out / "ABLATION.md"
        raw = out / "raw"
        combined = out / "first-round.json"

        written = write_first_round(
            report,
            ablation_path=ablation,
            raw_dir=raw,
            report_path=combined,
        )

        stored = json.loads(written.read_text(encoding="utf-8"))
        text = ablation.read_text(encoding="utf-8")

        self.assertEqual(written, combined)
        self.assertTrue((raw / "channel.json").is_file())
        self.assertTrue((raw / "swaps.json").is_file())
        self.assertTrue((raw / "gate.json").is_file())
        self.assertEqual(len(stored["deltas"]), len(report.deltas))
        self.assertIn("Channel rows", text)


class CliFirstRoundTests(unittest.TestCase):
    def run_main(
        self, *args: str, **kwargs: object
    ) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args), **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_the_first_round_success_path_writes_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())
            ablation = root / "ABLATION.md"
            raw = root / "raw"
            combined = root / "first-round.json"

            code, out, err = self.run_main(
                "--matrix", "first-round",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(combined),
                "--ablation", str(ablation),
                "--raw-dir", str(raw),
                "--embedding-model", "fake",
                "--embedding-dim", "8",
                embedder=FakeEmbedder(dim=8),
                query_embedder=FakeEmbedder(dim=8),
            )
            stored = json.loads(combined.read_text(encoding="utf-8"))
            text = ablation.read_text(encoding="utf-8")

        self.assertEqual((code, err), (0, ""))
        self.assertIn("first round", out)
        self.assertEqual(len(stored["channel"]["main"]), 4)
        self.assertIn("Channel rows — main", text)

    def test_the_first_round_needs_both_embedding_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())

            code, _out, err = self.run_main(
                "--matrix", "first-round",
                "--dataset", str(dataset),
                "--corpus", str(corpus),
                "--report", str(root / "first-round.json"),
            )

        self.assertEqual(code, 2)
        self.assertIn("embedding", err.lower())


if __name__ == "__main__":
    unittest.main()
