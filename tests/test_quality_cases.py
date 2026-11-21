"""The quality eval cases: a case directory becomes a validated case."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evals.quality.cases import CaseError, load_case, load_cases


CASE = """\
name: sample-case
title: 样例
turns:
  - "熵是什么？"
assertions:
  tools_any: [wiki_read, search]
  max_tool_rounds: 4
  no_error: true
judge:
  grounding:
    must: ["有 wiki 依据"]
    must_not: ["编造"]
  completeness:
    must: ["说清定义"]
  uncertainty:
    must: ["不把猜测说成事实"]
"""

WIKI = """\
---
title: "熵"
---

熵衡量不确定性。
"""


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_case(root: Path, name: str = "sample-case", case: str = CASE) -> Path:
    case_dir = root / name
    write(case_dir, "case.yaml", case.replace("name: sample-case", f"name: {name}"))
    write(case_dir, "wiki/concepts/entropy.md", WIKI)
    return case_dir


class LoadCaseTests(unittest.TestCase):
    def test_a_valid_case_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = make_case(Path(tmp))

            spec = load_case(case_dir)

            self.assertEqual(spec.name, "sample-case")
            self.assertEqual(spec.title, "样例")
            self.assertEqual(spec.turns, ("熵是什么？",))
            self.assertEqual(spec.assertions.tools_any, ("wiki_read", "search"))
            self.assertEqual(spec.assertions.max_tool_rounds, 4)
            self.assertIs(spec.assertions.no_error, True)
            self.assertEqual(spec.judge.grounding.must, ("有 wiki 依据",))
            self.assertEqual(spec.judge.grounding.must_not, ("编造",))
            self.assertEqual(len(spec.wiki), 1)
            self.assertEqual(spec.wiki[0].slug, "concepts/entropy")
            self.assertEqual(spec.wiki[0].title, "熵")
            self.assertEqual(spec.wiki[0].body.strip(), "熵衡量不确定性。")

    def test_an_unknown_assertion_key_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = CASE.replace("  no_error: true", "  nope: true")
            case_dir = make_case(Path(tmp), case=case)

            with self.assertRaises(CaseError) as caught:
                load_case(case_dir)

            self.assertIn("nope", str(caught.exception))

    def test_a_missing_wiki_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "sample-case"
            write(case_dir, "case.yaml", CASE)

            with self.assertRaises(CaseError) as caught:
                load_case(case_dir)

            self.assertIn("wiki", str(caught.exception))

    def test_bad_yaml_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = make_case(Path(tmp), case="name: [unclosed")

            with self.assertRaises(CaseError):
                load_case(case_dir)

    def test_the_name_must_match_the_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "sample-case"
            write(
                case_dir,
                "case.yaml",
                CASE.replace("name: sample-case", "name: another-case"),
            )
            write(case_dir, "wiki/concepts/entropy.md", WIKI)

            with self.assertRaises(CaseError) as caught:
                load_case(case_dir)

            self.assertIn("another-case", str(caught.exception))

    def test_empty_turns_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = CASE.replace('  - "熵是什么？"', '  - ""')
            case_dir = make_case(Path(tmp), case=case)

            with self.assertRaises(CaseError) as caught:
                load_case(case_dir)

            self.assertIn("turns", str(caught.exception))

    def test_an_unknown_judge_dimension_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = CASE.replace("  uncertainty:", "  style:")
            case_dir = make_case(Path(tmp), case=case)

            with self.assertRaises(CaseError) as caught:
                load_case(case_dir)

            self.assertIn("style", str(caught.exception))


class LoadCasesTests(unittest.TestCase):
    def test_cases_are_sorted_and_non_cases_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_case(root, "b-case")
            make_case(root, "a-case")
            (root / "notes").mkdir()

            cases = load_cases(root)

            self.assertEqual([case.name for case in cases], ["a-case", "b-case"])


class ShippedCaseTests(unittest.TestCase):
    def test_the_shipped_cases_load(self) -> None:
        root = (
            Path(__file__).resolve().parent.parent
            / "evals"
            / "quality"
            / "cases"
        )

        cases = load_cases(root)

        self.assertEqual(
            [case.name for case in cases],
            ["cross-page-gap", "single-page-qa"],
        )
        by_name = {case.name: case for case in cases}
        self.assertEqual(
            by_name["single-page-qa"].assertions.no_error, True
        )
        for case in cases:
            self.assertTrue(case.wiki)
