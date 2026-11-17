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
from evals.rag.variants import UnknownVariant, get_variant

from noteagent.project.layout import ProjectLayout

NOTE_ID = "20261101-090000-abcd"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_project(root: Path) -> Path:
    write(
        root / "wiki" / "concepts" / "entropy.md",
        '---\ntitle: "Entropy"\n---\n\n熵衡量不确定性。\n',
    )
    write(
        root / "wiki" / "concepts" / "kl-divergence.md",
        '---\ntitle: "KL 散度"\n---\n\nKL 散度衡量两个概率分布的差异。\n',
    )
    write(
        root / "wiki" / "agent" / "compaction.md",
        '---\ntitle: "Compaction"\n---\n\n对话太长时，较早的消息会被压缩。\n',
    )
    write(
        root / "notes" / "concepts" / "entropy" / f"{NOTE_ID}.md",
        "---\n"
        "page: wiki/concepts/entropy.md\n"
        f'created: "2026-11-01T09:00:00.000000"\n'
        f'updated: "2026-11-01T09:00:00.000000"\n'
        "---\n"
        "\n复习计划：先读熵，再读交叉熵。\n",
    )
    return root


ROWS = [
    {
        "id": "exact-01",
        "query": "entropy",
        "type": "exact",
        "expected_pages": ["concepts/entropy"],
        "expected_notes": [],
    },
    {
        "id": "para-01",
        "query": "两个概率分布差多少",
        "type": "paraphrase",
        "expected_pages": ["concepts/kl-divergence"],
        "expected_notes": [],
    },
    {
        "id": "cross-01",
        "query": "熵 压缩 对话",
        "type": "cross_page",
        "expected_pages": ["concepts/entropy", "agent/compaction"],
        "expected_notes": [],
    },
    {
        "id": "gap-01",
        "query": "zzzz absent",
        "type": "gap",
        "expected_pages": [],
        "expected_notes": [],
    },
    {
        "id": "notes-01",
        "query": "复习计划",
        "type": "notes",
        "expected_pages": [],
        "expected_notes": [NOTE_ID],
    },
]


def full_rows() -> list[dict]:
    """The five rows plus enough gap queries to satisfy the benchmark shape."""
    rows = list(ROWS)
    for index in range(15):
        rows.append(
            {
                "id": f"gap-{index + 2:02d}",
                "query": f"zzzz absent {index}",
                "type": "gap",
                "expected_pages": [],
                "expected_notes": [],
            }
        )
    return rows


def write_dataset(root: Path, rows: list[dict] | None = None) -> Path:
    path = root / "queries.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (ROWS if rows is None else rows)
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class LexicalVariantTests(unittest.TestCase):
    def test_an_exact_title_outranks_a_body_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_project(Path(tmp))

            got = get_variant("lexical").retrieve(
                ProjectLayout(root=corpus), "entropy"
            )

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
