"""The local writing quality machinery, exercised with fakes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.quality.writing.cases import ENTROPY, writing_cases
from evals.quality.writing.judge import DIMENSIONS
from evals.quality.writing.report import WritingReport
from evals.quality.writing.runner import run_pair

from noteagent.agent.faux import FauxModelClient
from noteagent.llm.client import LlmTurn

WIKI = "wiki/concepts/entropy.md"


class FauxJudge:
    def __init__(self, verdicts: dict[str, str] | None = None) -> None:
        self.verdicts = verdicts or {
            dimension: "pass" for dimension in DIMENSIONS
        }
        self.requests: list[object] = []

    def complete(self, messages, **kwargs) -> LlmTurn:
        self.requests.append(messages)
        payload = {
            dimension: {
                "verdict": self.verdicts.get(dimension, "pass"),
                "reason": "looks right",
            }
            for dimension in DIMENSIONS
        }
        return LlmTurn(content=json.dumps(payload))


def writing_model() -> FauxModelClient:
    scripts = [
        FauxModelClient.tool_call(
            "c1", "get_state", '{"pageId": "page-1"}'
        ).script,
        FauxModelClient.tool_call(
            "c2",
            "select_frame",
            '{"pageId": "page-1", "blockIds": ["b1", "b2"]}',
        ).script,
        FauxModelClient.tool_call(
            "c3",
            "compose_wiki",
            json.dumps(
                {
                    "wikiFile": WIKI,
                    "baseDraftVersion": 0,
                    "sections": [
                        {
                            "id": "definition",
                            "heading": "定义",
                            "body": "信息熵衡量不确定性。",
                            "citations": ["frame-1"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        ).script,
        FauxModelClient.tool_call(
            "c4",
            "write_wiki",
            json.dumps({"wikiFile": WIKI, "draftVersion": 1}),
        ).script,
        FauxModelClient.text("已发布。").script,
    ]
    return FauxModelClient(*(scripts * 2))


def _session_sections(entries_path: str) -> list[dict[str, object]]:
    folder = Path(entries_path)
    sections: list[dict[str, object]] = []
    for path in sorted(folder.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("type") == "system_section":
                sections.append(entry)
    return sections


class WritingQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.artifacts = Path(self._tmp.name) / "run"

    def test_the_pair_runs_both_arms_and_scores_the_judge(self) -> None:
        pair = run_pair(
            ENTROPY,
            artifacts=self.artifacts,
            model=writing_model(),
            judge_client=FauxJudge(),
        )

        for arm in (pair.skill, pair.plain):
            self.assertTrue(arm.passed, arm.error)
            self.assertEqual(arm.metrics["write_success"], 1)
            self.assertEqual(arm.metrics["completion"], 1)
            self.assertEqual(arm.metrics["sections"], 1)
            self.assertEqual(arm.metrics["citation_coverage"], 1.0)
            self.assertEqual(arm.metrics["tool_rounds"], 4)
            self.assertIn("latency_s", arm.metrics)
            self.assertIn("tokens", arm.metrics)
            self.assertGreaterEqual(arm.metrics["tool_calls"], 4)
            self.assertEqual(arm.judge.score(), 4)
            self.assertIn("get_state", arm.tool_names)
            self.assertIn("write_wiki", arm.tool_names)
        self.assertEqual(pair.verdict, "unchanged")

    def test_the_skill_arm_carries_the_skill_section(self) -> None:
        pair = run_pair(
            ENTROPY,
            artifacts=self.artifacts,
            model=writing_model(),
            judge_client=FauxJudge(),
        )

        skill_sections = _session_sections(pair.skill.entries_path)
        plain_sections = _session_sections(pair.plain.entries_path)
        self.assertTrue(
            any(
                "skills" in (entry.get("sections") or {})
                for entry in skill_sections
            )
        )
        self.assertFalse(
            any(
                "skills" in (entry.get("sections") or {})
                for entry in plain_sections
            )
        )
        self.assertTrue(
            any(
                check.name == "the skill section matches the arm"
                and check.passed
                for check in pair.skill.checks
            )
        )

    def test_the_report_is_honest_when_the_skill_does_not_help(self) -> None:
        pair = run_pair(
            ENTROPY,
            artifacts=self.artifacts,
            model=writing_model(),
            judge_client=FauxJudge(),
        )
        report = WritingReport((pair,), "2026-11-21T00:00:00Z", 1.0)

        markdown = report.markdown()

        self.assertIn("no measurable difference", markdown)
        self.assertIn("Next step", markdown)
        self.assertTrue(report.passed)

    def test_a_failed_judge_is_an_error_not_a_pass(self) -> None:
        class BrokenJudge(FauxJudge):
            def complete(self, messages, **kwargs) -> LlmTurn:
                return LlmTurn(content="not json")

        pair = run_pair(
            ENTROPY,
            artifacts=self.artifacts,
            model=writing_model(),
            judge_client=BrokenJudge(),
        )
        report = WritingReport((pair,), "2026-11-21T00:00:00Z", 1.0)

        self.assertFalse(pair.skill.judge_usable)
        self.assertFalse(report.usable)
        self.assertEqual(pair.skill.judge.score(), 0)

    def test_the_cases_carry_real_rules_and_sources(self) -> None:
        cases = writing_cases()

        self.assertGreaterEqual(len(cases), 2)
        for case in cases:
            self.assertTrue(case.sources)
            self.assertTrue(case.request)
            for dimension in DIMENSIONS:
                self.assertIn(dimension, case.rules)


if __name__ == "__main__":
    unittest.main()
