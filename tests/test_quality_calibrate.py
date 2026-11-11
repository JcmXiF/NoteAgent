"""Calibration: hand-written answers with the verdicts the judge must reach."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evals.quality.calibrate import (
    CalibrationError,
    calibrate,
    load_calibration,
)
from evals.quality.cases import load_case, load_cases
from fakes import ALL_PASS
from noteagent.llm.client import LlmTurn

CASE_DIR = (
    Path(__file__).resolve().parent.parent
    / "evals"
    / "quality"
    / "cases"
    / "single-page-qa"
)

CALIBRATION = """\
- name: good
  case: single-page-qa
  answer: "熵衡量不确定性；交叉熵等于熵加 KL 散度。"
  expected:
    grounding: pass
    completeness: pass
    uncertainty: pass
"""


class SequenceJudge:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[list[object]] = []

    def complete(self, messages, **kwargs) -> LlmTurn:
        self.requests.append(list(messages))
        return LlmTurn(content=self.replies.pop(0))


def load(path: Path):
    path.write_text(CALIBRATION, encoding="utf-8")
    return load_calibration(path)


class LoadCalibrationTests(unittest.TestCase):
    def test_a_valid_file_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = load(Path(tmp) / "answers.yaml")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "good")
        self.assertEqual(entries[0].case, "single-page-qa")
        self.assertEqual(
            entries[0].expected,
            {
                "grounding": "pass",
                "completeness": "pass",
                "uncertainty": "pass",
            },
        )

    def test_an_unknown_dimension_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = CALIBRATION.replace(
                "    uncertainty: pass", "    style: pass"
            )
            path = Path(tmp) / "answers.yaml"
            path.write_text(text, encoding="utf-8")

            with self.assertRaises(CalibrationError) as caught:
                load_calibration(path)

        self.assertIn("style", str(caught.exception))

    def test_an_invalid_verdict_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = CALIBRATION.replace(
                "    grounding: pass", "    grounding: maybe"
            )
            path = Path(tmp) / "answers.yaml"
            path.write_text(text, encoding="utf-8")

            with self.assertRaises(CalibrationError):
                load_calibration(path)

    def test_a_duplicate_name_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answers.yaml"
            path.write_text(CALIBRATION * 2, encoding="utf-8")

            with self.assertRaises(CalibrationError) as caught:
                load_calibration(path)

        self.assertIn("good", str(caught.exception))


class CalibrateTests(unittest.TestCase):
    def cases(self):
        return (load_case(CASE_DIR),)

    def test_the_judge_agrees_with_the_hand_written_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = load(Path(tmp) / "answers.yaml")
        results = calibrate(
            self.cases(), entries, client=SequenceJudge(ALL_PASS)
        )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].passed)
        self.assertEqual(results[0].actual, results[0].expected)

    def test_a_disagreement_fails_the_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = CALIBRATION.replace(
                "    grounding: pass", "    grounding: fail"
            )
            path = Path(tmp) / "answers.yaml"
            path.write_text(text, encoding="utf-8")
            entries = load_calibration(path)

        results = calibrate(
            self.cases(), entries, client=SequenceJudge(ALL_PASS)
        )

        self.assertFalse(results[0].passed)
        self.assertEqual(results[0].actual["grounding"], "pass")

    def test_a_judge_error_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = load(Path(tmp) / "answers.yaml")

        results = calibrate(
            self.cases(), entries, client=SequenceJudge("not json")
        )

        self.assertFalse(results[0].passed)
        self.assertIn("judge_error", results[0].detail)

    def test_an_unknown_case_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text = CALIBRATION.replace(
                "case: single-page-qa", "case: missing"
            )
            path = Path(tmp) / "answers.yaml"
            path.write_text(text, encoding="utf-8")
            entries = load_calibration(path)

        with self.assertRaises(CalibrationError):
            calibrate(
                self.cases(), entries, client=SequenceJudge(ALL_PASS)
            )

    def test_the_judge_sees_the_calibration_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = load(Path(tmp) / "answers.yaml")
        client = SequenceJudge(ALL_PASS)

        calibrate(self.cases(), entries, client=client)

        text = "\n".join(
            message.content for message in client.requests[0]
        )
        self.assertIn("熵衡量不确定性；交叉熵等于熵加 KL 散度。", text)


class ShippedCalibrationTests(unittest.TestCase):
    def test_the_shipped_calibration_loads(self) -> None:
        root = (
            Path(__file__).resolve().parent.parent / "evals" / "quality"
        )

        entries = load_calibration(root / "calibrate" / "answers.yaml")
        cases = {case.name for case in load_cases(root / "cases")}

        self.assertEqual(len(entries), 4)
        self.assertEqual(
            {entry.name for entry in entries},
            {
                "grounded-good",
                "fabricated",
                "incomplete-honest",
                "overconfident",
            },
        )
        self.assertTrue({entry.case for entry in entries} <= cases)
