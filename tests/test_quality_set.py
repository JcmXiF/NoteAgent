"""The frozen V3 quality set: the rubric, the hashes, and the diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evals.golden.dataset import write_manifest as write_golden_manifest
from evals.quality.v3.set import (
    ESSENTIAL,
    GOLDEN_ROOT,
    IMPORTANT,
    RUBRIC_PATH,
    VETO,
    QualitySetError,
    Rubric,
    RubricDimension,
    load_quality_set,
    load_rubric,
    write_manifest,
)

_QUERY = """
- id: fixture-01
  type: fact
  difficulty: easy
  question: "X 是什么？"
  corpus_pages: [concepts/x]
  reference_answer: "X 是 X。"
  key_points: ["要点一", "要点二"]
  expected_trace:
    tools_any: [search]
    wait: absent
    max_tool_rounds: 4
    no_error: true
  forbidden: ["编造"]
  raw_sources: []
  notes: "fixture"
"""

_CASE = """
name: one
title: One
turns:
  - "X 是什么？"
assertions:
  tools_any: [search]
  max_tool_rounds: 4
  no_error: true
judge:
  grounding:
    must: ["每个事实断言都能在 seeded wiki 中找到依据"]
    must_not: ["编造"]
  completeness:
    must: ["覆盖 expected point"]
  uncertainty:
    must: ["wiki 没有时说明"]
"""

_PAGE = "---\ntitle: X\n---\n\nX body.\n"


def _write_golden(root: Path) -> None:
    corpus = root / "corpus"
    page = corpus / "wiki" / "concepts" / "x.md"
    page.parent.mkdir(parents=True)
    (root / "queries.yaml").write_text(_QUERY, encoding="utf-8")
    (corpus / "purpose.md").write_text("Fixture corpus.\n", encoding="utf-8")
    (corpus / "schema.md").write_text("Fixture schema.\n", encoding="utf-8")
    page.write_text(_PAGE, encoding="utf-8")
    write_golden_manifest(root)


def _write_case(root: Path) -> None:
    case = root / "one"
    page = case / "wiki" / "concepts" / "x.md"
    page.parent.mkdir(parents=True)
    (case / "case.yaml").write_text(_CASE, encoding="utf-8")
    page.write_text(_PAGE, encoding="utf-8")


class RubricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rubric = load_rubric()

    def _passing(self) -> dict[str, str]:
        return {
            "grounding": "pass",
            "completeness": "pass",
            "uncertainty": "pass",
            "hallucination": "pass",
        }

    def test_all_required_and_the_veto_must_pass(self) -> None:
        self.assertTrue(self.rubric.passes(self._passing()))

    def test_an_essential_failure_fails(self) -> None:
        verdicts = self._passing()
        verdicts["grounding"] = "fail"

        self.assertFalse(self.rubric.passes(verdicts))

    def test_an_important_failure_fails(self) -> None:
        verdicts = self._passing()
        verdicts["uncertainty"] = "fail"

        self.assertFalse(self.rubric.passes(verdicts))

    def test_a_veto_failure_fails(self) -> None:
        verdicts = self._passing()
        verdicts["hallucination"] = "fail"

        self.assertFalse(self.rubric.passes(verdicts))

    def test_a_missing_verdict_fails(self) -> None:
        self.assertFalse(self.rubric.passes({"grounding": "pass"}))

    def test_a_bad_weight_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rubric.yaml"
            path.write_text(
                "version: v3\n"
                "dimensions:\n"
                "  - name: grounding\n"
                "    weight: optional\n"
                "    must: [x]\n"
                "    must_not: [y]\n"
                "veto:\n"
                "  name: hallucination\n"
                "  must: [x]\n"
                "  must_not: [y]\n",
                encoding="utf-8",
            )

            with self.assertRaises(QualitySetError):
                load_rubric(path)


class WeightedRubricTests(unittest.TestCase):
    """The pass rule follows each weight, whatever the dimension is called."""

    def setUp(self) -> None:
        self.rubric = Rubric(
            "v-test",
            (
                RubricDimension("alpha", ESSENTIAL, ("a",), ("b",)),
                RubricDimension("beta", IMPORTANT, ("c",), ("d",)),
            ),
            RubricDimension("gamma", VETO, ("e",), ("f",)),
        )

    def _verdicts(self, **overrides: str) -> dict[str, str]:
        verdicts = {name: "pass" for name in self.rubric.verdict_keys()}
        verdicts.update(overrides)
        return verdicts

    def test_the_keys_follow_the_rubric(self) -> None:
        self.assertEqual(
            self.rubric.verdict_keys(), ("alpha", "beta", "gamma")
        )

    def test_every_gate_passes(self) -> None:
        self.assertTrue(self.rubric.passes(self._verdicts()))

    def test_an_essential_gate_fails_the_item(self) -> None:
        self.assertFalse(self.rubric.passes(self._verdicts(alpha="fail")))

    def test_an_important_gate_fails_the_item(self) -> None:
        self.assertFalse(self.rubric.passes(self._verdicts(beta="fail")))

    def test_the_veto_fails_the_item(self) -> None:
        self.assertFalse(self.rubric.passes(self._verdicts(gamma="fail")))

    def test_a_missing_verdict_fails(self) -> None:
        verdicts = self._verdicts()
        del verdicts["beta"]

        self.assertFalse(self.rubric.passes(verdicts))

    def test_a_direct_rubric_refuses_a_weight_that_cannot_gate(self) -> None:
        with self.assertRaises(QualitySetError):
            Rubric(
                "v-test",
                (RubricDimension("alpha", "optional"),),
                RubricDimension("gamma", VETO),
            )


class RealQualitySetTests(unittest.TestCase):
    def test_every_task_is_a_direct_write(self) -> None:
        quality = load_quality_set()

        tasks = [
            query
            for query in quality.single_turn
            if query.type == "task"
        ]
        self.assertEqual(len(tasks), 9)
        for query in tasks:
            with self.subTest(query=query.id):
                self.assertEqual(query.expected_trace.wait, "absent")
                self.assertIsNone(query.expected_trace.propose_valid)

    def test_the_real_set_is_frozen(self) -> None:
        quality = load_quality_set()

        self.assertEqual(quality.version, "v3.1")
        self.assertEqual(len(quality.single_turn), 24)
        self.assertEqual(
            dict(quality.golden.counts["by_type"]),
            {"fact": 5, "synthesis": 5, "gap": 5, "task": 9},
        )
        self.assertEqual(
            [case.name for case in quality.multi_turn],
            ["cross-page-gap", "single-page-qa"],
        )
        self.assertEqual(len(quality.rubric.dimensions), 3)
        self.assertEqual(quality.rubric.veto.name, "hallucination")
        self.assertTrue(quality.diagnostics.tools)
        self.assertTrue(quality.diagnostics.trace)
        self.assertTrue(quality.content_hash)
        self.assertEqual(
            Path(quality.golden.root).resolve(), GOLDEN_ROOT.resolve()
        )
        for case in quality.multi_turn:
            self.assertTrue(quality.expected_points(case))
            self.assertTrue(quality.diagnostic_assertions(case))
        for query in quality.single_turn:
            self.assertTrue(quality.diagnostic_trace(query))


class FrozenSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _write_golden(self.root / "golden")
        _write_case(self.root / "cases")
        write_manifest(
            self.root / "manifest.yaml",
            version="v3.0-test",
            golden_root=self.root / "golden",
            cases_root=self.root / "cases",
            rubric_path=RUBRIC_PATH,
            require_counts=False,
        )

    def _load(self):
        return load_quality_set(
            self.root / "manifest.yaml",
            cases_root=self.root / "cases",
            rubric_path=RUBRIC_PATH,
            require_counts=False,
        )

    def test_the_fixture_set_loads_with_diagnostics(self) -> None:
        quality = self._load()

        self.assertEqual(quality.version, "v3.0-test")
        self.assertEqual(len(quality.single_turn), 1)
        self.assertEqual([case.name for case in quality.multi_turn], ["one"])
        self.assertTrue(quality.diagnostics.tools)
        self.assertTrue(quality.diagnostics.trace)
        self.assertTrue(quality.content_hash)
        self.assertTrue(quality.expected_points(quality.multi_turn[0]))
        self.assertTrue(quality.diagnostic_assertions(quality.multi_turn[0]))
        self.assertTrue(quality.diagnostic_trace(quality.single_turn[0]))

    def test_a_changed_case_is_refused(self) -> None:
        case = self.root / "cases" / "one" / "case.yaml"
        case.write_text(
            case.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

        with self.assertRaises(QualitySetError):
            self._load()

    def test_a_changed_rubric_is_refused(self) -> None:
        rubric = self.root / "rubric.yaml"
        rubric.write_text(
            RUBRIC_PATH.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(QualitySetError):
            load_quality_set(
                self.root / "manifest.yaml",
                cases_root=self.root / "cases",
                rubric_path=rubric,
                require_counts=False,
            )

    def test_a_changed_golden_is_refused(self) -> None:
        queries = self.root / "golden" / "queries.yaml"
        queries.write_text(
            queries.read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(QualitySetError):
            self._load()


if __name__ == "__main__":
    unittest.main()
