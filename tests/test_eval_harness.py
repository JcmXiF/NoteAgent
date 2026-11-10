"""The eval harness: what a failure keeps, and what a report says."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from evals.harness import Case, CaseRun, run_case
    from evals.report import CaseResult, Check, EvalReport
    from evals.sse import parse_sse
except ImportError:  # pragma: no cover - CI installs the API extra
    Case = None  # type: ignore[misc, assignment]
    CaseRun = None  # type: ignore[misc, assignment]
    CaseResult = None  # type: ignore[misc, assignment]
    Check = None  # type: ignore[misc, assignment]
    EvalReport = None  # type: ignore[misc, assignment]
    parse_sse = None  # type: ignore[misc, assignment]
    run_case = None  # type: ignore[misc, assignment]


@unittest.skipIf(parse_sse is None, "fastapi not installed")
class SseParsingTests(unittest.TestCase):
    def test_comments_are_skipped_and_data_is_joined(self) -> None:
        text = (
            ": keepalive\n\n"
            'event: token\ndata: {"text": "答"}\n\n'
            'event: done\ndata: {"run": {"status": "completed"}}\n'
        )

        events = parse_sse(text)

        self.assertEqual(
            [kind for kind, _data in events], ["token", "done"]
        )
        self.assertEqual(events[0][1], {"text": "答"})


@unittest.skipIf(run_case is None, "fastapi not installed")
class FailureArtifactTests(unittest.TestCase):
    def test_a_failed_case_keeps_its_project_events_and_checks(self) -> None:
        def fails(run) -> None:
            run.check("a passing check", True)
            run.check("a failing check", False, "the reason")
            events = run.layout.session_events_path("s1")
            events.parent.mkdir(parents=True, exist_ok=True)
            events.write_text('{"type": "run_started"}\n', encoding="utf-8")
            raise AssertionError("boom")

        case = Case("broken_case", "A case that fails on purpose.", fails)
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"

            result = run_case(case, artifacts)

            self.assertFalse(result.passed)
            target = artifacts / "broken_case"
            self.assertTrue(
                (target / "project" / "wiki" / "index.md").is_file()
            )
            self.assertTrue(
                (target / "events" / "s1.events.jsonl").is_file()
            )
            payload = json.loads(
                (target / "case.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["passed"] for item in payload["checks"]],
                [True, False],
            )
            self.assertEqual(payload["checks"][1]["detail"], "the reason")
            self.assertIn("boom", payload["error"])

    def test_a_passing_case_leaves_no_artifacts(self) -> None:
        case = Case(
            "passing_case",
            "A case that passes.",
            lambda run: run.check("it ran", True),
        )
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"

            result = run_case(case, artifacts)

            self.assertTrue(result.passed)
            self.assertFalse((artifacts / "passing_case").exists())

    def test_a_case_that_cannot_start_restores_the_active_project(self) -> None:
        from noteagent.api import projects as projects_api

        root = projects_api.REPO_ROOT
        project = os.environ.get("NOTEAGENT_PROJECT")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                projects_api,
                "use_project",
                side_effect=RuntimeError("the project cannot be used"),
            ):
                with self.assertRaises(RuntimeError):
                    CaseRun(Path(tmp) / "work")

        self.assertIs(projects_api.REPO_ROOT, root)
        self.assertEqual(os.environ.get("NOTEAGENT_PROJECT"), project)


@unittest.skipIf(EvalReport is None, "fastapi not installed")
class ReportTests(unittest.TestCase):
    def test_the_report_counts_both_verdicts_and_writes_markdown(self) -> None:
        report = EvalReport(
            (
                CaseResult(
                    "a", "A", (Check("ok", True),), "", 0.1, (), ()
                ),
                CaseResult(
                    "b",
                    "B",
                    (Check("not ok", False, "no"),),
                    "",
                    0.2,
                    (),
                    (),
                ),
            ),
            "2026-11-10T09:00:00Z",
            0.3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = report.write(Path(tmp) / "report.json")

            payload = json.loads(path.read_text(encoding="utf-8"))
            summary = path.with_suffix(".md").read_text(encoding="utf-8")

        self.assertEqual((payload["passed"], payload["failed"]), (1, 1))
        self.assertIn("1 passed, 1 failed", summary)
        self.assertIn("not ok: no", summary)


if __name__ == "__main__":
    unittest.main()
