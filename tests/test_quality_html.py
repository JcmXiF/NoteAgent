"""The HTML report: one escaped file with a timeline and a baseline delta."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals.quality.assertions import AssertionResult
from evals.quality.baseline import BaselineDelta, CaseDelta
from evals.quality.html_report import write_html
from evals.quality.judge import DimensionVerdict, JudgeVerdict
from evals.quality.report import (
    AgentInfo,
    CaseSummary,
    JudgeInfo,
    QualityReport,
)

DIMENSIONS = ("grounding", "completeness", "uncertainty")


def judge(
    *verdicts: str,
    reason: str = "有依据",
    prompt: str = "abc",
    raw: str = '{"grounding": {"verdict": "pass"}}',
    error: str = "",
) -> JudgeVerdict:
    if error:
        return JudgeVerdict(error=error, raw=raw, prompt_sha256=prompt)
    return JudgeVerdict(
        dimensions=tuple(
            DimensionVerdict(
                name,
                verdict,
                reason if name == "grounding" else "",
                ("证据" if name == "grounding" else ()),
            )
            for name, verdict in zip(DIMENSIONS, verdicts)
        ),
        raw=raw,
        prompt_sha256=prompt,
    )


def case(
    name: str,
    *,
    passed: bool = True,
    title: str | None = None,
    verdicts: tuple[str, str, str] = ("pass", "pass", "pass"),
    reason: str = "有依据",
    prompt: str = "abc",
) -> CaseSummary:
    return CaseSummary(
        name=name,
        title=title or name,
        passed=passed,
        duration_s=1.0,
        assertions=(
            AssertionResult("tools_any", passed, ["wiki_read"], ["wiki_read"]),
        ),
        judge=judge(*verdicts, reason=reason, prompt=prompt),
    )


def report(cases: tuple[CaseSummary, ...]) -> QualityReport:
    return QualityReport(
        started_at="2026-11-11T01:00:00.000Z",
        duration_s=1.5,
        agent=AgentInfo(
            model="a-model",
            base_url="https://user:secret@api.example.com/v1",
            temperature=0.1,
        ),
        cases=cases,
        judge=JudgeInfo(model="a-model", temperature=0.0),
    )


def write_detail(root: Path, name: str, payload: dict) -> None:
    target = root / "cases" / name
    target.mkdir(parents=True)
    (target / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


DETAIL = {
    "entries": [
        {"type": "message", "role": "user", "content": "熵是什么？"},
        {
            "type": "message",
            "role": "assistant",
            "content": "我先读一页。",
            "toolCalls": [
                {
                    "id": "c1",
                    "name": "wiki_read",
                    "arguments": '{"slug": "concepts/entropy"}',
                }
            ],
        },
        {
            "type": "message",
            "role": "toolResult",
            "content": "熵衡量不确定性。",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": "熵衡量不确定性。",
        },
    ],
    "trace": [{"type": "run_finished", "status": "completed"}],
}


class WriteHtmlTests(unittest.TestCase):
    def test_the_page_has_the_header_summary_timeline_and_judge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_detail(root, "sample", DETAIL)
            write_detail(
                root,
                "broken",
                {
                    "entries": [],
                    "trace": [{"type": "run_failed", "code": "internal"}],
                },
            )
            delta = BaselineDelta(
                created_at="2026-11-10T00:00:00Z",
                cases=(
                    CaseDelta("sample", "unchanged", (), ()),
                    CaseDelta(
                        "broken",
                        "regressed",
                        ("tools_any",),
                        ("grounding",),
                    ),
                ),
                config_changes=("agent.model",),
                prompt_changes=("sample",),
            )
            page = root / "report.html"

            write_html(
                report(
                    (
                        case("sample"),
                        case(
                            "broken",
                            passed=False,
                            verdicts=("fail", "pass", "pass"),
                        ),
                    )
                ),
                page,
                artifacts_root=root,
                delta=delta,
                baseline_note="Compared with the baseline.",
            )

            text = page.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertIn("Quality eval", text)
        self.assertIn("1 passed", text)
        self.assertIn("1 failed", text)
        self.assertIn("Judge prompt sha256", text)
        self.assertIn("failed assertions: 1", text)
        self.assertIn("failed dimensions: 1", text)
        self.assertIn("熵是什么？", text)
        self.assertIn("熵衡量不确定性。", text)
        self.assertIn("tool calls: wiki_read", text)
        self.assertIn("run_failed", text)
        self.assertIn("python -m evals.quality --case sample", text)
        self.assertIn("cases/sample/session.jsonl", text)
        self.assertIn("cases/sample/judge.json", text)
        self.assertIn("Compared with the baseline.", text)
        self.assertIn("Configuration changed: agent.model", text)
        self.assertIn("Judge prompt changed for: sample", text)
        self.assertIn("regressed", text)
        self.assertIn('<details class="case" open>', text)
        self.assertIn('<details class="case">', text)

    def test_the_page_has_no_external_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_detail(root, "sample", DETAIL)
            page = root / "report.html"

            write_html(report((case("sample"),)), page, artifacts_root=root)

            text = page.read_text(encoding="utf-8")

        self.assertNotIn("<script", text)
        self.assertNotIn("<link", text)
        self.assertNotIn("@import", text)
        self.assertNotIn("url(", text)

    def test_model_text_is_escaped_and_the_key_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_detail(root, "sample", DETAIL)
            page = root / "report.html"

            write_html(
                report(
                    (
                        case(
                            "sample",
                            title="<script>alert(1)</script>",
                            reason="<script>alert(2)</script>",
                        ),
                    )
                ),
                page,
                artifacts_root=root,
            )

            text = page.read_text(encoding="utf-8")

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", text)
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("secret", text)

    def test_a_missing_case_detail_is_noted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "report.html"

            write_html(
                report((case("sample"),)),
                page,
                artifacts_root=Path(tmp),
            )

            text = page.read_text(encoding="utf-8")

        self.assertIn("Case details were not kept for this run.", text)

    def test_no_baseline_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "report.html"

            write_html(
                report((case("sample"),)),
                page,
                artifacts_root=Path(tmp),
            )

            text = page.read_text(encoding="utf-8")

        self.assertIn("No baseline to compare with.", text)

    def test_a_judge_error_is_shown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "report.html"
            broken = CaseSummary(
                name="sample",
                title="sample",
                passed=False,
                duration_s=1.0,
                assertions=(),
                judge=JudgeVerdict(
                    error="judge_error: bad json",
                    raw="<bad>",
                    prompt_sha256="abc",
                ),
            )

            write_html(
                report((broken,)),
                page,
                artifacts_root=Path(tmp),
            )

            text = page.read_text(encoding="utf-8")

        self.assertIn("judge_error: bad json", text)
        self.assertIn("&lt;bad&gt;", text)
