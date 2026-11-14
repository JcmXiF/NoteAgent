"""The v2 golden set: loading, validating and refreshing the local benchmark."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from evals.golden.dataset import (
    GoldenError,
    load_golden,
    main,
    write_manifest,
)

TYPES = ("fact", "synthesis", "gap", "task")
DIFFICULTIES = ("easy", "medium", "hard")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def query(
    *,
    id: str = "fact-easy-01",
    type: str = "fact",
    difficulty: str = "easy",
    question: str = "问题？",
    pages: tuple[str, ...] = ("concepts/x",),
    reference: str = "参考答案。",
    key_points: list[str] | None = None,
    trace: dict | None = None,
    forbidden: list[str] | None = None,
) -> dict:
    if trace is None:
        trace = {
            "tools_any": ["search", "wiki_read"],
            "wait": "absent",
            "max_tool_rounds": 4,
            "no_error": True,
        }
        if type == "task":
            trace = {
                "tools_any": ["search", "wiki_read"],
                "wait": "proposed",
                "propose_valid": True,
                "max_tool_rounds": 4,
                "no_error": True,
            }
    return {
        "id": id,
        "type": type,
        "difficulty": difficulty,
        "question": question,
        "corpus_pages": list(pages),
        "reference_answer": reference,
        "key_points": key_points if key_points is not None else ["要点一", "要点二"],
        "expected_trace": trace,
        "forbidden": forbidden if forbidden is not None else ["编造"],
        "raw_sources": [],
        "notes": "测试用例。",
    }


def make_golden(
    root: Path,
    *,
    queries: list[dict] | None = None,
    page_body: str = "X 的定义。",
    refresh: bool = True,
) -> Path:
    corpus = root / "corpus"
    write(
        corpus / "purpose.md",
        "# Wiki Purpose\n\n测试用信息论 wiki。\n",
    )
    write(corpus / "schema.md", "# Wiki Schema\n\n默认页面类型。\n")
    write(
        corpus / "wiki" / "concepts" / "x.md",
        f'---\ntitle: "X"\n---\n\n{page_body}\n',
    )
    write(
        corpus / "manifest.yaml",
        yaml.safe_dump(
            {
                "version": 1,
                "snapshot": "2026-11-12",
                "source": "test",
                "files": {},
                "excluded": [],
                "raw": {"copied": False, "note": ""},
                "corpus_sha256": "",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    if queries is None:
        queries = [
            query(),
            query(
                id="synthesis-medium-01",
                type="synthesis",
                difficulty="medium",
            ),
            query(id="gap-hard-01", type="gap", difficulty="hard"),
            query(id="task-easy-01", type="task", difficulty="easy"),
        ]
    write(
        root / "queries.yaml",
        yaml.safe_dump(queries, allow_unicode=True, sort_keys=False),
    )
    write(
        root / "manifest.yaml",
        yaml.safe_dump(
            {
                "version": 1,
                "created": "2026-11-12",
                "corpus_sha256": "",
                "queries_sha256": "",
                "counts": {},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    if refresh:
        write_manifest(root)
    return root


def twenty_queries() -> list[dict]:
    result: list[dict] = []
    for type_name in TYPES:
        difficulty_plan = ("easy", "medium", "medium", "hard", "hard")
        for index, difficulty in enumerate(difficulty_plan, 1):
            result.append(
                query(
                    id=f"{type_name}-{difficulty}-{index:02d}",
                    type=type_name,
                    difficulty=difficulty,
                    question=f"{type_name} 问题 {index}？",
                )
            )
    return result


def propose_query(
    id: str,
    job: str,
    interface: str,
    difficulty: str,
    question: str = "发起一次协作？",
) -> dict:
    return query(
        id=id,
        type="task",
        difficulty=difficulty,
        question=question,
        trace={
            "tools_any": ["search", "wiki_read"],
            "wait": "proposed",
            "propose_valid": True,
            "propose_job": job,
            "propose_interface": interface,
            "max_tool_rounds": 4,
            "no_error": True,
        },
    )


def twenty_four_queries() -> list[dict]:
    result = twenty_queries()
    result.extend(
        [
            propose_query(
                "propose-plan-source",
                "plan",
                "source",
                "easy",
            ),
            propose_query(
                "propose-plan-board",
                "plan",
                "plan_board",
                "medium",
            ),
            propose_query(
                "propose-compile-plan-board",
                "compile",
                "plan_board",
                "medium",
            ),
            propose_query(
                "propose-compile-compile-board",
                "compile",
                "compile_board",
                "hard",
            ),
        ]
    )
    return result


class ValidGoldenTests(unittest.TestCase):
    def test_a_valid_set_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp))

            golden = load_golden(root, require_counts=False)

        self.assertEqual(len(golden.queries), 4)
        self.assertEqual(
            {item.type for item in golden.queries},
            {"fact", "synthesis", "gap", "task"},
        )
        page = golden.corpus.page("concepts/x")
        self.assertEqual(page.title, "X")
        self.assertEqual(page.body, "X 的定义。")
        self.assertIn("信息论", golden.corpus.purpose)
        self.assertTrue(golden.corpus_sha256)
        self.assertTrue(golden.queries_sha256)

    def test_the_twenty_four_query_counts_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=twenty_four_queries())

            golden = load_golden(root)

        self.assertEqual(len(golden.queries), 24)
        self.assertEqual(
            golden.counts["by_type"],
            {"fact": 5, "synthesis": 5, "gap": 5, "task": 9},
        )
        self.assertEqual(
            golden.counts["by_difficulty"],
            {"easy": 5, "medium": 10, "hard": 9},
        )

    def test_a_missing_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp))
            (root / "manifest.yaml").unlink()

            with self.assertRaises(GoldenError):
                load_golden(root, require_counts=False)


class InvalidQueryTests(unittest.TestCase):
    def load_bad(self, bad_query: dict) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(
                Path(tmp), queries=[bad_query], refresh=False
            )
            with self.assertRaises(GoldenError) as caught:
                load_golden(
                    root, check_hashes=False, require_counts=False
                )
        self.assertTrue(str(caught.exception))

    def test_an_unknown_key_is_refused(self) -> None:
        bad = query()
        bad["nope"] = True

        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(
                Path(tmp), queries=[bad], refresh=False
            )
            with self.assertRaises(GoldenError) as caught:
                load_golden(root, check_hashes=False, require_counts=False)

        self.assertIn("nope", str(caught.exception))

    def test_a_missing_corpus_page_is_refused(self) -> None:
        self.load_bad(query(pages=("concepts/missing",)))

    def test_key_points_must_be_two_to_five(self) -> None:
        self.load_bad(query(key_points=["one"]))
        self.load_bad(query(key_points=["a", "b", "c", "d", "e", "f"]))

    def test_a_task_must_propose(self) -> None:
        self.load_bad(
            query(
                type="task",
                trace={
                    "tools_any": ["search"],
                    "wait": "absent",
                    "max_tool_rounds": 4,
                    "no_error": True,
                },
            )
        )

    def test_a_non_task_must_not_propose(self) -> None:
        self.load_bad(
            query(
                type="fact",
                trace={
                    "tools_any": ["search"],
                    "wait": "proposed",
                    "max_tool_rounds": 4,
                    "no_error": True,
                },
            )
        )

    def test_a_bad_wait_is_refused(self) -> None:
        self.load_bad(
            query(
                trace={
                    "tools_any": ["search"],
                    "wait": "maybe",
                    "max_tool_rounds": 4,
                    "no_error": True,
                }
            )
        )


class ProposeVariantTests(unittest.TestCase):
    def test_the_four_legal_pairs_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=twenty_four_queries())

            golden = load_golden(root)

        pairs = {
            (
                item.expected_trace.propose_job,
                item.expected_trace.propose_interface,
            )
            for item in golden.queries
            if item.expected_trace.propose_job is not None
        }
        self.assertEqual(
            pairs,
            {
                ("plan", "source"),
                ("plan", "plan_board"),
                ("compile", "plan_board"),
                ("compile", "compile_board"),
            },
        )

    def test_an_invalid_pair_is_refused(self) -> None:
        bad = propose_query(
            "propose-bad", "compile", "source", "easy"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=[bad], refresh=False)
            with self.assertRaises(GoldenError) as caught:
                load_golden(
                    root, check_hashes=False, require_counts=False
                )

        self.assertIn("propose_interface", str(caught.exception))

    def test_a_non_task_cannot_name_a_propose(self) -> None:
        bad = query()
        bad["expected_trace"] = {
            **bad["expected_trace"],
            "propose_job": "plan",
            "propose_interface": "source",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=[bad], refresh=False)
            with self.assertRaises(GoldenError) as caught:
                load_golden(
                    root, check_hashes=False, require_counts=False
                )

        self.assertIn("only for tasks", str(caught.exception))


class ManifestTests(unittest.TestCase):
    def test_a_stale_queries_hash_is_refused_then_refreshed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=twenty_four_queries())
            load_golden(root)
            edited = twenty_four_queries()
            edited[0]["question"] = "改过的问题？"
            write(
                root / "queries.yaml",
                yaml.safe_dump(edited, allow_unicode=True, sort_keys=False),
            )

            with self.assertRaises(GoldenError) as caught:
                load_golden(root)

            self.assertIn("stale", str(caught.exception).lower())
            write_manifest(root)
            golden = load_golden(root)

        self.assertEqual(golden.queries[0].question, "改过的问题？")


class CliTests(unittest.TestCase):
    def test_check_returns_zero_for_a_valid_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=twenty_four_queries())
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["check", "--golden", str(root)])

        self.assertEqual(code, 0)
        self.assertIn("24", out.getvalue())

    def test_check_returns_two_for_a_bad_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(
                Path(tmp), queries=[query(pages=("concepts/missing",))]
            )
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["check", "--golden", str(root)])

        self.assertEqual(code, 2)
        self.assertIn("concepts/missing", out.getvalue())

    def test_write_manifest_refreshes_the_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_golden(Path(tmp), queries=twenty_four_queries())
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(
                    ["check", "--golden", str(root), "--write-manifest"]
                )

            self.assertEqual(code, 0)
            golden = load_golden(root)
            self.assertEqual(len(golden.queries), 24)
