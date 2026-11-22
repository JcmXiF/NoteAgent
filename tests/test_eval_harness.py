"""The eval harness: what a failure keeps, and what a report says."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.report import CaseResult, Check, EvalReport, FirstError
from evals.support import remove_tree
from evals.trace import TraceEvidence, ToolCall, TraceSpec, first_error

try:
    from evals.harness import Case, CaseRun, run_case
    from evals.sse import parse_sse
except ImportError:  # pragma: no cover - CI installs the API extra
    Case = None  # type: ignore[misc, assignment]
    CaseRun = None  # type: ignore[misc, assignment]
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


class TraceSpecTests(unittest.TestCase):
    def test_required_is_an_ordered_subsequence(self) -> None:
        spec = TraceSpec(required=("web_search", "select_page", "compose_wiki"))
        evidence = TraceEvidence(
            tools=(
                ToolCall("web_search", 1),
                ToolCall("get_state", 2),
                ToolCall("select_page", 3),
                ToolCall("compose_wiki", 4),
            ),
            model_turns=4,
        )

        self.assertEqual(spec.violations(evidence), ())

    def test_a_missing_required_tool_names_what_is_missing(self) -> None:
        spec = TraceSpec(required=("web_search", "select_page"))
        evidence = TraceEvidence(tools=(ToolCall("web_search", 1),), model_turns=1)

        violations = spec.violations(evidence)

        self.assertEqual([item.kind for item in violations], ["required"])
        self.assertEqual(violations[0].name, "select_page")
        self.assertIsNone(violations[0].step)

    def test_out_of_order_is_a_missing_required_tool(self) -> None:
        spec = TraceSpec(required=("web_search", "select_page"))
        evidence = TraceEvidence(
            tools=(ToolCall("select_page", 1), ToolCall("web_search", 2)),
            model_turns=2,
        )

        violations = spec.violations(evidence)

        self.assertEqual([item.name for item in violations], ["select_page"])

    def test_forbidden_records_the_step(self) -> None:
        spec = TraceSpec(forbidden=("propose",))
        evidence = TraceEvidence(
            tools=(ToolCall("web_search", 1), ToolCall("propose", 2)),
            model_turns=2,
        )

        violations = spec.violations(evidence)

        self.assertEqual(violations[0].kind, "forbidden")
        self.assertEqual(violations[0].step, 2)
        self.assertEqual(violations[0].category, "tool-contract")

    def test_max_turns_is_a_violation(self) -> None:
        spec = TraceSpec(max_turns=2)
        evidence = TraceEvidence(model_turns=3)

        violations = spec.violations(evidence)

        self.assertEqual([item.kind for item in violations], ["max_turns"])
        self.assertEqual(violations[0].step, 3)

    def test_a_violation_becomes_a_failed_check(self) -> None:
        spec = TraceSpec(forbidden=("propose",))
        evidence = TraceEvidence(tools=(ToolCall("propose", 1),), model_turns=1)

        checks = spec.checks(evidence)

        self.assertFalse(checks[0].passed)
        self.assertEqual(checks[0].category, "tool-contract")


class CheckCategoryTests(unittest.TestCase):
    def test_a_check_category_defaults_to_uncategorized(self) -> None:
        self.assertEqual(Check("ok", True).category, "uncategorized")

    def test_a_check_category_is_kept(self) -> None:
        self.assertEqual(
            Check("the save is refused", False, "busy", "lock").category,
            "lock",
        )

    def test_an_uncategorized_check_reads_as_uncategorized(self) -> None:
        error = first_error((Check("untagged", False, "why"),))

        assert error is not None
        self.assertEqual(error.category, "uncategorized")


class FirstErrorTests(unittest.TestCase):
    def test_the_first_failed_check_carries_its_category(self) -> None:
        checks = (
            Check("the page exists", True),
            Check(
                "the page cites its frame",
                False,
                "sources were empty",
                "provenance",
                "wiki/concepts/entropy.md",
            ),
        )

        error = first_error(checks)

        assert error is not None
        self.assertIsInstance(error, FirstError)
        self.assertEqual(error.kind, "check")
        self.assertEqual(error.check, "the page cites its frame")
        self.assertEqual(error.category, "provenance")
        self.assertEqual(error.event, "wiki/concepts/entropy.md")
        self.assertEqual(error.actual, "sources were empty")
        self.assertIsNone(error.step)

    def test_a_stepped_trace_violation_beats_a_failed_check(self) -> None:
        checks = (Check("the page exists", False, "missing", "publish"),)
        trace = TraceSpec(forbidden=("propose",))
        evidence = TraceEvidence(tools=(ToolCall("propose", 2),), model_turns=2)

        error = first_error(checks, evidence, trace)

        assert error is not None
        self.assertEqual(error.kind, "trace")
        self.assertEqual(error.category, "tool-contract")
        self.assertEqual(error.step, 2)
        self.assertEqual(error.event, "propose")

    def test_a_missing_required_tool_is_a_trace_error(self) -> None:
        checks = (
            Check("the answer is committed", False, "not committed", "state"),
        )
        trace = TraceSpec(required=("web_search",))
        evidence = TraceEvidence(model_turns=1)

        error = first_error(checks, evidence, trace)

        assert error is not None
        self.assertEqual(error.kind, "trace")
        self.assertEqual(error.event, "web_search")
        self.assertIsNone(error.step)

    def test_an_error_is_the_last_resort(self) -> None:
        error = first_error((), error="Traceback...\nValueError: boom")

        assert error is not None
        self.assertEqual(error.kind, "error")
        self.assertEqual(error.category, "uncategorized")
        self.assertEqual(error.actual, "ValueError: boom")

    def test_no_failure_has_no_first_error(self) -> None:
        self.assertIsNone(first_error((Check("ok", True),)))


@unittest.skipIf(CaseRun is None, "fastapi not installed")
class RestartTests(unittest.TestCase):
    def test_restart_reopens_the_app_on_the_same_project(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        tmp = Path(tempfile.mkdtemp(prefix="noteagent-eval-restart-"))
        run = CaseRun(tmp / "work")
        try:
            api = run.start(FauxModelClient())
            session = api.create_session()
            run.store.append(
                session,
                {"type": "message", "role": "user", "content": "hello"},
            )
            old_app = run.app

            new_api = run.restart()

            self.assertIsNot(run.app, old_app)
            self.assertEqual(
                new_api.get_session(session)["messages"][0]["content"],
                "hello",
            )
            self.assertGreaterEqual(len(new_api.exchanges), 2)
        finally:
            run.close()
            remove_tree(tmp)


@unittest.skipIf(CaseRun is None, "fastapi not installed")
class WebSeamTests(unittest.TestCase):
    def test_start_forwards_the_web_search_seam(self) -> None:
        from noteagent.acquire.types import CandidateCard
        from noteagent.agent.faux import FauxModelClient

        calls: list[str] = []

        def search(query: str) -> list[CandidateCard]:
            calls.append(query)
            return [
                CandidateCard(
                    title="信息熵",
                    url="https://example.com/entropy",
                    site="example.com",
                    snippet="熵是信息的平均量。",
                    why="",
                    via="test",
                )
            ]

        model = FauxModelClient(
            FauxModelClient.tool_call(
                "t1", "web_search", '{"query": "信息熵"}'
            ).script,
            FauxModelClient.text("done").script,
        )
        tmp = Path(tempfile.mkdtemp(prefix="noteagent-eval-web-"))
        run = CaseRun(tmp / "work")
        try:
            api = run.start(model, web_search=search)
            session = api.create_session()
            reply = api.query(session, "搜一下信息熵")

            self.assertEqual(reply.status_code, 200)
            self.assertEqual(calls, ["信息熵"])
        finally:
            run.close()
            remove_tree(tmp)


    def test_start_keeps_both_web_seams(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        from evals import harness as harness_module

        def search(query: str):
            return []

        def fetch(url: str):
            return None

        seen: dict[str, object] = {}
        real_create = harness_module.create_app

        def capture(deps):
            seen["deps"] = deps
            return real_create(deps)

        tmp = Path(tempfile.mkdtemp(prefix="noteagent-eval-web-"))
        run = CaseRun(tmp / "work")
        try:
            with patch.object(harness_module, "create_app", capture):
                run.start(
                    FauxModelClient(),
                    web_search=search,
                    web_fetch=fetch,
                )
            deps = seen["deps"]
            self.assertIs(deps.web_search, search)
            self.assertIs(deps.web_fetch, fetch)
        finally:
            run.close()
            remove_tree(tmp)


@unittest.skipIf(run_case is None, "fastapi not installed")
class TraceIntegrationTests(unittest.TestCase):
    def test_a_trace_violation_fails_the_case_with_a_first_error(self) -> None:
        from noteagent.agent.faux import FauxModelClient

        def run_writing(run) -> None:
            model = FauxModelClient(
                FauxModelClient.tool_call(
                    "t1", "web_search", '{"query": "信息熵"}'
                ).script,
                FauxModelClient.text("done").script,
            )
            api = run.start(model, web_search=lambda query: [])
            session = api.create_session()
            api.query(session, "搜一下")

        case = Case(
            "forbidden_search",
            "A forbidden web search fails the trace.",
            run_writing,
            trace=TraceSpec(forbidden=("web_search",)),
        )
        tmp = Path(tempfile.mkdtemp(prefix="noteagent-eval-trace-"))
        try:
            result = run_case(case, tmp / "artifacts")

            self.assertFalse(result.passed)
            self.assertTrue(
                any(
                    check.name == "trace: forbidden web_search"
                    for check in result.checks
                )
            )
            assert result.first_error is not None
            self.assertEqual(result.first_error.kind, "trace")
            self.assertEqual(result.first_error.step, 1)
            self.assertEqual(result.first_error.category, "tool-contract")

            report = EvalReport((result,), "now", 0.1)
            payload = report.payload()["cases"][0]
            self.assertEqual(payload["firstError"]["kind"], "trace")
            self.assertIn("category", payload["checks"][0])
        finally:
            remove_tree(tmp)


if __name__ == "__main__":
    unittest.main()
