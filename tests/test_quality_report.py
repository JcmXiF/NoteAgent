"""The quality report: what a run leaves for a reader, without the key."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.quality.assertions import AssertionResult
from evals.quality.judge import DimensionVerdict, JudgeVerdict
from evals.quality.report import (
    AgentInfo,
    CaseSummary,
    JudgeInfo,
    QualityReport,
    public_base_url,
    write_report,
)

JUDGE = JudgeVerdict(
    dimensions=(
        DimensionVerdict("grounding", "pass", "有依据", ("熵",)),
        DimensionVerdict("completeness", "pass", "答全"),
        DimensionVerdict("uncertainty", "pass", "不猜测"),
    ),
    raw='{"grounding": {"verdict": "pass"}}',
    prompt_sha256="abc123",
)


def report() -> QualityReport:
    return QualityReport(
        started_at="2026-11-11T01:00:00.000Z",
        duration_s=1.5,
        agent=AgentInfo(
            model="a-model",
            base_url="https://api.example.com/v1",
            temperature=0.1,
        ),
        cases=(
            CaseSummary(
                name="sample-case",
                title="样例",
                passed=True,
                duration_s=1.0,
                assertions=(
                    AssertionResult(
                        "tools_any", True, ["wiki_read"], ["wiki_read"]
                    ),
                ),
                judge=JUDGE,
            ),
        ),
        judge=JudgeInfo(model="a-model", temperature=0.0),
    )


class PublicBaseUrlTests(unittest.TestCase):
    def test_userinfo_query_and_fragment_are_removed(self) -> None:
        self.assertEqual(
            public_base_url(
                "https://user:secret@api.example.com:8443/v1?key=abc#frag"
            ),
            "https://api.example.com:8443/v1",
        )

    def test_a_plain_url_is_kept(self) -> None:
        self.assertEqual(
            public_base_url("https://api.example.com/v1"),
            "https://api.example.com/v1",
        )


class WriteReportTests(unittest.TestCase):
    def test_the_report_has_the_agent_and_case_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "report.json"

            written = write_report(report(), path)

            self.assertEqual(written, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["agent"]["model"], "a-model")
            self.assertEqual(
                payload["agent"]["baseUrl"], "https://api.example.com/v1"
            )
            self.assertEqual(payload["cases"][0]["name"], "sample-case")
            self.assertTrue(payload["cases"][0]["assertions"][0]["passed"])
            self.assertEqual(payload["judge"]["model"], "a-model")
            self.assertEqual(payload["judge"]["temperature"], 0.0)
            self.assertEqual(
                payload["judge"]["prompts"],
                {"sample-case": "abc123"},
            )
            judge = payload["cases"][0]["judge"]
            self.assertTrue(judge["passed"])
            self.assertEqual(
                judge["dimensions"][0]["dimension"], "grounding"
            )
            self.assertIn("raw", judge)

    def test_the_report_never_contains_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"

            write_report(report(), path)

            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secret", text)
            self.assertNotIn("apiKey", text)
