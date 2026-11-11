"""The quality runner: one case end to end, with a scripted model."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fakes import ALL_PASS, FakeJudge

try:
    from evals.quality.cases import load_case
    from evals.quality.runner import run_case
    from noteagent.agent.faux import FauxModelClient
    from noteagent.llm.config import LlmConfig
except ImportError:  # pragma: no cover - CI installs the API extra
    load_case = None  # type: ignore[assignment]
    run_case = None  # type: ignore[assignment]
    FauxModelClient = None  # type: ignore[assignment]
    LlmConfig = None  # type: ignore[assignment]


CASE = """\
name: sample-case
title: 样例
turns:
  - "熵是什么？"
assertions:
  tools_any: [wiki_read]
  wait: absent
  max_tool_rounds: 2
  no_error: true
judge:
  grounding:
    must: ["有 wiki 依据"]
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


def make_case(root: Path, case: str = CASE) -> Path:
    path = root / "sample-case"
    (path / "wiki" / "concepts").mkdir(parents=True)
    (path / "case.yaml").write_text(case, encoding="utf-8")
    (path / "wiki" / "concepts" / "entropy.md").write_text(
        WIKI, encoding="utf-8"
    )
    return path


def config() -> "LlmConfig":
    return LlmConfig(
        base_url="https://api.example.com/v1",
        api_key="secret-key",
        model="faux",
    )


def wiki_then_answer() -> "FauxModelClient":
    return FauxModelClient(
        FauxModelClient.tool_call(
            "t1", "wiki_read", '{"slug": "concepts/entropy"}'
        ).script,
        FauxModelClient.text("熵衡量不确定性。").script,
    )


@unittest.skipIf(run_case is None, "fastapi not installed")
class RunCaseTests(unittest.TestCase):
    def test_a_passing_case_keeps_its_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = load_case(make_case(root / "case"))
            artifacts = root / "run"

            result = run_case(
                spec,
                config(),
                artifacts,
                model=wiki_then_answer(),
                judge_client=FakeJudge(ALL_PASS),
            )

            self.assertTrue(result.passed, result.error)
            self.assertIsNotNone(result.judge)
            self.assertTrue(result.judge.passed)
            self.assertEqual(
                [item.name for item in result.assertions],
                ["tools_any", "wait", "max_tool_rounds", "no_error"],
            )
            target = artifacts / "cases" / "sample-case"
            self.assertTrue(
                (target / "project" / "wiki" / "concepts" / "entropy.md")
                .is_file()
            )
            self.assertTrue((target / "session.jsonl").is_file())
            self.assertTrue((target / "events.jsonl").is_file())
            self.assertTrue((target / "judge.json").is_file())
            judge_payload = json.loads(
                (target / "judge.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [
                    item["dimension"]
                    for item in judge_payload["dimensions"]
                ],
                ["grounding", "completeness", "uncertainty"],
            )
            text = (target / "case.json").read_text(encoding="utf-8")
            payload = json.loads(text)
            self.assertTrue(payload["passed"])
            self.assertTrue(payload["entries"])
            self.assertTrue(payload["trace"])
            self.assertNotIn("secret-key", text)

    def test_a_failing_assertion_is_reported_and_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = CASE.replace("tools_any: [wiki_read]", "tools_any: [search]")
            spec = load_case(make_case(root / "case", case))
            artifacts = root / "run"

            result = run_case(
                spec,
                config(),
                artifacts,
                model=wiki_then_answer(),
                judge_client=FakeJudge(ALL_PASS),
            )

            self.assertFalse(result.passed)
            failed = [item for item in result.assertions if not item.passed]
            self.assertEqual([item.name for item in failed], ["tools_any"])
            self.assertIn("search", failed[0].detail)
            self.assertTrue(
                (artifacts / "cases" / "sample-case" / "case.json").is_file()
            )

    def test_a_model_error_fails_no_error_and_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = load_case(make_case(root / "case"))
            artifacts = root / "run"

            # The faux client runs out of scripts on the first model call; the
            # run reports a model error instead of the runner crashing.
            result = run_case(
                spec,
                config(),
                artifacts,
                model=FauxModelClient(),
                judge_client=FakeJudge(ALL_PASS),
            )

            self.assertFalse(result.passed)
            self.assertEqual(result.error, "")
            self.assertIsNotNone(result.judge)
            self.assertEqual(result.judge.skipped, "no assistant answer")
            failed = {
                item.name: item
                for item in result.assertions
                if not item.passed
            }
            self.assertIn("no_error", failed)
            self.assertIn("no scripted response", failed["no_error"].detail)
            target = artifacts / "cases" / "sample-case"
            payload = json.loads(
                (target / "case.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                any(
                    record.get("type") == "run_failed"
                    for record in payload["trace"]
                )
            )

    def test_a_failing_judge_fails_the_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = load_case(make_case(root / "case"))
            artifacts = root / "run"
            payload = json.loads(ALL_PASS)
            payload["grounding"]["verdict"] = "fail"
            payload["grounding"]["reason"] = "缺依据"

            result = run_case(
                spec,
                config(),
                artifacts,
                model=wiki_then_answer(),
                judge_client=FakeJudge(
                    json.dumps(payload, ensure_ascii=False)
                ),
            )

            self.assertFalse(result.passed)
            self.assertTrue(all(item.passed for item in result.assertions))
            self.assertIsNotNone(result.judge)
            self.assertFalse(result.judge.passed)
            self.assertEqual(result.judge.dimensions[0].verdict, "fail")

    def test_a_silent_proposal_is_still_judged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = (
                Path(__file__).resolve().parent.parent
                / "evals"
                / "quality"
                / "cases"
                / "propose-protocol"
            )
            spec = load_case(case_dir)
            artifacts = root / "run"
            model = FauxModelClient(
                FauxModelClient.tool_call(
                    "p1",
                    "propose",
                    json.dumps(
                        {
                            "job": "compile",
                            "interface": "plan_board",
                            "intent": "对比页",
                            "reason": (
                                "我会整理成对比页，需要你在计划板进入编译。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                ).script
            )
            judge = FakeJudge(ALL_PASS)

            result = run_case(
                spec,
                config(),
                artifacts,
                model=model,
                judge_client=judge,
            )

            self.assertTrue(result.passed, result.error)
            self.assertIsNotNone(result.judge)
            self.assertEqual(result.judge.skipped, "")
            request = "\n".join(
                message.content for message in judge.requests[0]
            )
            self.assertIn("我会整理成对比页", request)
            self.assertTrue(
                (
                    artifacts
                    / "cases"
                    / "propose-protocol"
                    / "judge.json"
                ).is_file()
            )
