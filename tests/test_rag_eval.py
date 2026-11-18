"""The offline retrieval eval: the lexical variant, its run, and its report."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from evals.rag.cli import main
from evals.rag.dataset import load_dataset
from evals.rag.report import markdown, payload, write_report
from evals.rag.runner import evaluate, run_queries
from evals.rag.variants import (
    EvalContext,
    IndexCache,
    Scope,
    UnknownVariant,
    get_variant,
)
from rag_eval_support import (
    NOTE_ID,
    ROWS,
    full_rows,
    make_project,
    write_dataset,
)

from noteagent.project.layout import ProjectLayout


class LexicalVariantTests(unittest.TestCase):
    def test_an_exact_title_outranks_a_body_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_project(Path(tmp))
            variant = get_variant("lexical")
            context = EvalContext(
                layout=ProjectLayout(root=corpus),
                scope=Scope.WIKI_NOTES,
                indexes=IndexCache(),
            )

            got = variant.prepare(context).retrieve("entropy")

        self.assertEqual(got.pages[0], "concepts/entropy")

    def test_an_unknown_variant_is_refused(self) -> None:
        with self.assertRaises(UnknownVariant) as caught:
            get_variant("semantic")

        self.assertIn("semantic", str(caught.exception))
        self.assertIn("lexical", str(caught.exception))


class RunTests(unittest.TestCase):
    def test_each_query_is_scored_in_its_own_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = load_dataset(
                write_dataset(root), require_full=False
            )

            results = run_queries(
                dataset.queries, corpus, get_variant("lexical")
            )

        by_id = {result.query.id: result for result in results}
        self.assertEqual(
            by_id["exact-01"].ranked[0], "page:concepts/entropy"
        )
        self.assertEqual(by_id["exact-01"].outcome, "hit")
        self.assertEqual(by_id["notes-01"].ranked[0], f"note:{NOTE_ID}")
        self.assertEqual(by_id["notes-01"].outcome, "hit")
        self.assertEqual(by_id["gap-01"].ranked, ())
        self.assertTrue(by_id["gap-01"].abstained)
        self.assertEqual(by_id["gap-01"].outcome, "abstained")
        self.assertGreaterEqual(by_id["exact-01"].latency_ms, 0.0)

    def test_a_gap_query_that_returns_a_result_is_a_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            row = {
                "id": "gap-01",
                "query": "不确定性",
                "type": "gap",
                "expected_pages": [],
                "expected_notes": [],
            }
            path = root / "queries.jsonl"
            path.write_text(
                json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            dataset = load_dataset(path, require_full=False)

            (result,) = run_queries(
                dataset.queries, corpus, get_variant("lexical")
            )

        self.assertFalse(result.abstained)
        self.assertEqual(result.outcome, "false_positive")


class EvaluationTests(unittest.TestCase):
    def build(self, root: Path):
        corpus = make_project(root)
        dataset = load_dataset(write_dataset(root), require_full=False)
        return dataset, evaluate(dataset, corpus, "lexical")

    def test_the_report_scores_every_non_gap_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dataset, evaluation = self.build(Path(tmp))

            report = payload(evaluation)

        self.assertEqual(report["variant"]["name"], "lexical")
        self.assertTrue(report["baseline"])
        self.assertEqual(report["dataset"]["sha256"], dataset.sha256)
        self.assertEqual(report["dataset"]["queries"], 5)
        self.assertEqual(report["metrics"]["overall"]["count"], 4)
        self.assertEqual(report["metrics"]["byType"]["notes"]["count"], 1)
        self.assertEqual(report["metrics"]["byType"]["notes"]["mrr"], 1.0)
        self.assertEqual(report["metrics"]["gap"]["count"], 1)
        self.assertEqual(report["metrics"]["gap"]["abstained"], 1)
        self.assertEqual(report["metrics"]["gap"]["rate"], 1.0)
        self.assertGreaterEqual(report["latency"]["p50Ms"], 0.0)
        self.assertGreaterEqual(report["latency"]["p95Ms"], 0.0)
        self.assertEqual(
            [item["id"] for item in report["queries"]],
            [row["id"] for row in ROWS],
        )
        gap = next(
            item for item in report["queries"] if item["id"] == "gap-01"
        )
        self.assertEqual(gap["outcome"], "abstained")
        self.assertIsNone(gap["recallAt5"])

    def test_the_markdown_names_the_metrics_and_the_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, evaluation = self.build(Path(tmp))

            text = markdown(evaluation)

        self.assertIn("Recall@5", text)
        self.assertIn("Hit@5", text)
        self.assertIn("nDCG@10", text)
        self.assertIn("MRR", text)
        self.assertIn("exact-01", text)
        self.assertIn("abstained", text)

    def test_the_report_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, evaluation = self.build(root)
            target = root / "out" / "report.json"

            written = write_report(evaluation, target)

            stored = json.loads(written.read_text(encoding="utf-8"))
            summary = written.with_suffix(".md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(written, target)
        self.assertEqual(stored["variant"]["name"], "lexical")
        self.assertIn("# Retrieval eval", summary)


class CommandTests(unittest.TestCase):
    def run_main(self, *args: str) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_the_command_writes_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())
            target = root / "report.json"

            code, out, err = self.run_main(
                "--dataset",
                str(dataset),
                "--corpus",
                str(corpus),
                "--report",
                str(target),
                "--variant",
                "lexical",
            )

            self.assertEqual((code, err), (0, ""))
            self.assertIn("lexical", out)
            self.assertIn("Recall@5", out)
            stored = json.loads(target.read_text(encoding="utf-8"))

            self.assertTrue(target.with_suffix(".md").is_file())
            self.assertEqual(stored["variant"]["name"], "lexical")

    def test_the_default_variant_is_lexical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())

            code, out, _err = self.run_main(
                "--dataset",
                str(dataset),
                "--corpus",
                str(corpus),
                "--report",
                str(root / "report.json"),
            )

        self.assertEqual(code, 0)
        self.assertIn("lexical", out)

    def test_an_unknown_variant_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = make_project(root)
            dataset = write_dataset(root, full_rows())

            code, _out, err = self.run_main(
                "--dataset",
                str(dataset),
                "--corpus",
                str(corpus),
                "--variant",
                "semantic",
            )

        self.assertEqual(code, 2)
        self.assertIn("semantic", err)

    def test_a_missing_dataset_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, _out, err = self.run_main(
                "--dataset",
                str(Path(tmp) / "absent.jsonl"),
            )

        self.assertEqual(code, 2)
        self.assertIn("absent", err)


if __name__ == "__main__":
    unittest.main()
