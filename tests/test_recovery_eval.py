"""The recovery eval's own arithmetic: ratios, exclusions and the report.

The eval harness is a measurement tool, so its reducers are tested like any
other module: a run marked ``missed`` or ``infra`` must never enter a product
denominator, and a safety ratio must keep its exact numerator and denominator
instead of collapsing into a rounded score.
"""

from __future__ import annotations

import unittest

from evals.recovery.boundary import (
    ANSWER_COMMITTED,
    EMPTY,
    QUESTION_ONLY,
    STARTED,
    TERMINAL,
    TOOL_CALL_OPEN,
    TOOL_RESULT_OPEN,
    classify_entries,
    wait_for_boundary,
)
from evals.recovery.dataset import SCENARIOS, Scenario
from evals.recovery.metrics import Metrics, Ratio, ScenarioRun, compute_metrics
from evals.recovery.report import RecoveryReport, markdown, payload


def run(**fields: object) -> ScenarioRun:
    base: dict[str, object] = {
        "scenario": "R1",
        "suite": "recovery",
        "rep": 1,
    }
    base.update(fields)
    return ScenarioRun(**base)  # type: ignore[arg-type]


def recovery_run(**fields: object) -> ScenarioRun:
    base: dict[str, object] = {
        "interrupted_expected": True,
        "interrupted_detected": True,
        "preserved_entries": 2,
        "committed_entries": 2,
        "replayed_tools": 0,
        "resume_attempted": True,
        "resume_succeeded": True,
        "budget_exceeded": False,
        "terminal": True,
    }
    base.update(fields)
    return run(**base)


class MetricsTests(unittest.TestCase):
    def test_detection_excludes_missed_and_infra_runs(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(status="passed"),
                recovery_run(status="failed", interrupted_detected=False),
                recovery_run(status="missed", interrupted_detected=False),
                recovery_run(status="infra", interrupted_detected=False),
            )
        )
        self.assertEqual(metrics.detection, Ratio(1, 2))

    def test_false_interrupt_counts_only_normal_runs(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(
                    normal_completion=True, false_interrupted=True
                ),
                recovery_run(
                    normal_completion=True, false_interrupted=False
                ),
                recovery_run(),
            )
        )
        self.assertEqual(metrics.false_interrupt, Ratio(1, 2))

    def test_preservation_is_entry_weighted(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(preserved_entries=9, committed_entries=10),
                recovery_run(preserved_entries=5, committed_entries=5),
            )
        )
        self.assertEqual(metrics.preservation, Ratio(14, 15))

    def test_replay_count_sums_and_reports_runs(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(replayed_tools=0),
                recovery_run(replayed_tools=2),
                recovery_run(replayed_tools=0),
            )
        )
        self.assertEqual(metrics.replay_count, 2)
        self.assertEqual(metrics.replay_runs, 1)

    def test_resume_success_requires_budget(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(budget_exceeded=False),
                recovery_run(resume_succeeded=False, budget_exceeded=False),
                recovery_run(resume_succeeded=True, budget_exceeded=True),
                recovery_run(status="missed"),
            )
        )
        self.assertEqual(metrics.resume_success, Ratio(1, 3))

    def test_recovery_latency_percentiles(self) -> None:
        metrics = compute_metrics(
            tuple(
                recovery_run(recovery_seconds=value)
                for value in (4.0, 1.0, 3.0, 2.0)
            )
        )
        self.assertEqual(metrics.recovery_samples, 4)
        self.assertEqual(metrics.recovery_p50_ms, 2000.0)
        self.assertEqual(metrics.recovery_p95_ms, 4000.0)

    def test_classification_accuracy_is_page_weighted(self) -> None:
        metrics = compute_metrics(
            (
                run(
                    suite="rollback",
                    classified_correct=3,
                    classified_total=4,
                ),
                run(
                    suite="rollback",
                    classified_correct=6,
                    classified_total=6,
                ),
            )
        )
        self.assertEqual(metrics.classification, Ratio(9, 10))

    def test_overwrite_rate_counts_only_user_changed_pages(self) -> None:
        metrics = compute_metrics(
            (
                run(
                    suite="rollback",
                    user_changed_pages=2,
                    user_pages_overwritten=0,
                ),
                run(
                    suite="rollback",
                    user_changed_pages=2,
                    user_pages_overwritten=1,
                ),
            )
        )
        self.assertEqual(metrics.overwrite, Ratio(1, 4))

    def test_idempotency_counts_only_repeated_restores(self) -> None:
        metrics = compute_metrics(
            (
                run(
                    suite="rollback",
                    repeat_restore=True,
                    extra_undo_after_repeat=0,
                ),
                run(
                    suite="rollback",
                    repeat_restore=True,
                    extra_undo_after_repeat=1,
                ),
                run(suite="rollback", repeat_restore=False),
            )
        )
        self.assertEqual(metrics.idempotency, Ratio(1, 2))

    def test_restore_success_uses_attempted_restores(self) -> None:
        metrics = compute_metrics(
            (
                run(
                    suite="rollback",
                    restore_attempted=True,
                    restore_succeeded=True,
                ),
                run(
                    suite="rollback",
                    restore_attempted=True,
                    restore_succeeded=False,
                ),
                run(suite="rollback", restore_attempted=False),
            )
        )
        self.assertEqual(metrics.restore_success, Ratio(1, 2))

    def test_budget_completion_ignores_runs_without_a_budget(self) -> None:
        metrics = compute_metrics(
            (
                recovery_run(budget_exceeded=False),
                recovery_run(budget_exceeded=True),
                recovery_run(budget_exceeded=None),
            )
        )
        self.assertEqual(metrics.budget_completion, Ratio(1, 2))

    def test_empty_metrics_do_not_divide_by_zero(self) -> None:
        metrics = compute_metrics(())
        self.assertIsInstance(metrics, Metrics)
        self.assertFalse(metrics.detection.present)
        self.assertEqual(metrics.replay_count, 0)


def message(role: str, **fields: object) -> dict[str, object]:
    entry: dict[str, object] = {"type": "message", "role": role}
    entry.update(fields)
    return entry


def marker(state: str, run_id: str = "run-1") -> dict[str, object]:
    return {"type": "run_marker", "runId": run_id, "state": state}


def call(call_id: str) -> dict[str, object]:
    return {"id": call_id, "name": "read_page", "arguments": "{}"}


class BoundaryTests(unittest.TestCase):
    def test_question_without_answer(self) -> None:
        entries = [marker("finished"), message("user", content="hi")]
        self.assertEqual(classify_entries(entries), QUESTION_ONLY)

    def test_started_marker_without_content(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
        ]
        self.assertEqual(classify_entries(entries), STARTED)

    def test_tool_call_without_result(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
            message("assistant", toolCalls=[call("c1")]),
        ]
        self.assertEqual(classify_entries(entries), TOOL_CALL_OPEN)

    def test_partial_tool_results_still_open(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
            message("assistant", toolCalls=[call("c1"), call("c2")]),
            message("toolResult", toolCallId="c1", content="ok"),
        ]
        self.assertEqual(classify_entries(entries), TOOL_CALL_OPEN)

    def test_tool_result_committed(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
            message("assistant", toolCalls=[call("c1")]),
            message("toolResult", toolCallId="c1", content="ok"),
        ]
        self.assertEqual(classify_entries(entries), TOOL_RESULT_OPEN)

    def test_answer_committed(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
            message("assistant", content="answer"),
        ]
        self.assertEqual(classify_entries(entries), ANSWER_COMMITTED)

    def test_terminal_marker_wins(self) -> None:
        entries = [
            message("user", content="hi"),
            marker("running"),
            message("assistant", content="answer"),
            marker("finished"),
        ]
        self.assertEqual(classify_entries(entries), TERMINAL)

    def test_interrupted_marker_is_settled(self) -> None:
        entries = [message("user", content="hi"), marker("interrupted")]
        self.assertEqual(classify_entries(entries), TERMINAL)

    def test_empty_transcript(self) -> None:
        self.assertEqual(classify_entries([]), EMPTY)

    def test_wait_returns_when_the_boundary_appears(self) -> None:
        import json
        import tempfile
        import threading
        import time
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            path.write_text("", encoding="utf-8")

            def writer() -> None:
                time.sleep(0.05)
                with path.open("a", encoding="utf-8") as handle:
                    for entry in (
                        message("user", content="hi"),
                        marker("running"),
                        message("assistant", toolCalls=[call("c1")]),
                    ):
                        handle.write(json.dumps(entry) + "\n")

            thread = threading.Thread(target=writer)
            thread.start()
            try:
                observed = wait_for_boundary(
                    path, TOOL_CALL_OPEN, timeout_s=2.0
                )
            finally:
                thread.join()
        self.assertIsNotNone(observed)
        assert observed is not None
        self.assertEqual(observed.boundary, TOOL_CALL_OPEN)

    def test_wait_times_out_without_the_boundary(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "s.jsonl"
            path.write_text("", encoding="utf-8")
            observed = wait_for_boundary(
                path, TOOL_CALL_OPEN, timeout_s=0.1
            )
        self.assertIsNone(observed)


class SlowToolTests(unittest.TestCase):
    def test_wrap_tool_sleeps_and_preserves_the_spec(self) -> None:
        from noteagent.agent.tools import ToolSpec

        from evals.recovery.server import wrap_tool

        sentinel = object()
        slept: list[float] = []

        def handler(arguments: object, context: object) -> object:
            return sentinel

        spec = ToolSpec(
            name="demo",
            description="a demo tool",
            parameters={},
            handler=handler,
        )
        wrapped = wrap_tool(spec, 0.25, sleeper=slept.append)
        self.assertEqual(wrapped.name, "demo")
        self.assertIs(wrapped.handler({}, None), sentinel)  # type: ignore[arg-type]
        self.assertEqual(slept, [0.25])
        self.assertIsNot(wrapped, spec)

    def test_wrap_tool_rejects_a_negative_delay(self) -> None:
        from noteagent.agent.tools import ToolSpec

        from evals.recovery.server import wrap_tool

        spec = ToolSpec(
            name="demo",
            description="a demo tool",
            parameters={},
            handler=lambda arguments, context: None,
        )
        with self.assertRaises(ValueError):
            wrap_tool(spec, -0.1)


class ProcessTests(unittest.TestCase):
    def test_free_port_is_bindable(self) -> None:
        import socket

        from evals.recovery.process import free_port

        port = free_port()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))

    def test_wait_ready_sees_a_listener(self) -> None:
        import socket

        from evals.recovery.process import wait_ready

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        try:
            self.assertTrue(wait_ready(port, timeout_s=1.0))
        finally:
            server.close()

    def test_spawn_and_kill_a_child(self) -> None:
        import sys
        import tempfile
        from pathlib import Path

        from evals.recovery.process import Child, spawn

        with tempfile.TemporaryDirectory() as directory:
            child = spawn(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                log_path=Path(directory) / "child.log",
            )
            try:
                self.assertIsInstance(child, Child)
                self.assertTrue(child.alive)
            finally:
                child.kill()
            self.assertFalse(child.alive)

    def test_server_environment_points_at_the_project(self) -> None:
        import tempfile
        from pathlib import Path

        from evals.recovery.process import server_env

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            env = server_env(project, {"NOTEAGENT_LLM_MODEL": "test-model"})
        self.assertEqual(env["NOTEAGENT_PROJECT"], str(project))
        self.assertEqual(env["NOTEAGENT_LLM_MODEL"], "test-model")
        self.assertIn("src", env["PYTHONPATH"])


class SseTests(unittest.TestCase):
    def test_sse_reader_reads_kind_and_data(self) -> None:
        from evals.recovery.client import iter_sse

        text = (
            'event: token\ndata: {"text": "hi", "seq": 1}\n\n'
            'event: done\ndata: {"seq": 2}\n\n'
        )
        events = tuple(iter_sse(text.split("\n")))
        self.assertEqual([event.kind for event in events], ["token", "done"])
        self.assertEqual(events[0].data["text"], "hi")

    def test_sse_reader_skips_keepalive_blocks(self) -> None:
        from evals.recovery.client import iter_sse

        text = (
            ": keepalive\n\n"
            'event: status\ndata: {"message": "thinking"}\n\n'
        )
        events = tuple(iter_sse(text.split("\n")))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data["message"], "thinking")

    def test_sse_reader_tolerates_a_partial_tail(self) -> None:
        from evals.recovery.client import iter_sse

        text = 'event: token\ndata: {"text": "hi"}'
        self.assertEqual(tuple(iter_sse(text.split("\n"))), ())


class ProxyTests(unittest.TestCase):
    def test_failure_counter_fails_exactly_the_first_n(self) -> None:
        from evals.recovery.proxy import FailureCounter

        counter = FailureCounter(2)
        self.assertEqual(
            [counter.take() for _ in range(4)],
            [True, True, False, False],
        )


class EnvFileTests(unittest.TestCase):
    def test_load_env_file_keeps_existing_values_and_skips_noise(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from evals.recovery.envfile import load_env_file

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# comment\n"
                "EVALTEST_A=1\n"
                "EVALTEST_B='two'\n"
                "EVALTEST_C=\n"
                "not a pair\n"
                "EVALTEST_D=4\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ, {"EVALTEST_B": "keep"}, clear=False
            ):
                loaded = load_env_file(path)
                self.assertIn("EVALTEST_A", loaded)
                self.assertNotIn("EVALTEST_B", loaded)
                self.assertEqual(os.environ["EVALTEST_A"], "1")
                self.assertEqual(os.environ["EVALTEST_B"], "keep")
                self.assertEqual(os.environ["EVALTEST_C"], "")
                self.assertEqual(os.environ["EVALTEST_D"], "4")

    def test_a_missing_env_file_is_not_an_error(self) -> None:
        from pathlib import Path

        from evals.recovery.envfile import load_env_file

        self.assertEqual(
            load_env_file(Path("no-such-eval-directory/.env")), ()
        )


class RetryRuleTests(unittest.TestCase):
    def test_a_persistent_miss_becomes_infra_after_four_attempts(self) -> None:
        import tempfile
        from pathlib import Path

        from evals.recovery import runner

        scenario = SCENARIOS[0]
        original = runner._IMPLEMENTATIONS[scenario.id]
        try:
            runner._IMPLEMENTATIONS[scenario.id] = lambda context: runner.Attempt(
                status="missed", detail="no boundary"
            )
            with tempfile.TemporaryDirectory() as directory:
                run = runner.run_scenario(
                    scenario, 1, artifacts=Path(directory), delay_ms=0.0
                )
                attempts = list(
                    (Path(directory) / scenario.id / "rep-1").glob("attempt-*")
                )
        finally:
            runner._IMPLEMENTATIONS[scenario.id] = original
        self.assertEqual(run.status, "infra")
        self.assertIn("never hit the target boundary", run.detail)
        self.assertEqual(len(attempts), 4)

    def test_a_single_miss_is_retried_and_the_second_attempt_wins(self) -> None:
        import tempfile
        from pathlib import Path

        from evals.recovery import runner

        scenario = SCENARIOS[0]
        original = runner._IMPLEMENTATIONS[scenario.id]
        calls = {"count": 0}

        def fake(context: object) -> object:
            calls["count"] += 1
            if calls["count"] == 1:
                return runner.Attempt(status="missed", detail="no boundary")
            return runner.Attempt(
                status="passed", observed_boundary="finished"
            )

        try:
            runner._IMPLEMENTATIONS[scenario.id] = fake
            with tempfile.TemporaryDirectory() as directory:
                run = runner.run_scenario(
                    scenario, 1, artifacts=Path(directory), delay_ms=0.0
                )
                attempts = list(
                    (Path(directory) / scenario.id / "rep-1").glob("attempt-*")
                )
        finally:
            runner._IMPLEMENTATIONS[scenario.id] = original
        self.assertEqual(run.status, "passed")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(len(attempts), 2)


class DatasetTests(unittest.TestCase):
    def test_twelve_scenarios_with_six_per_suite(self) -> None:
        self.assertEqual(len(SCENARIOS), 12)
        self.assertEqual(
            [scenario.id for scenario in SCENARIOS],
            ["R1", "R2", "R3", "R4", "R5", "R6",
             "B1", "B2", "B3", "B4", "B5", "B6"],
        )
        self.assertEqual(
            sum(1 for item in SCENARIOS if item.suite == "recovery"), 6
        )
        self.assertEqual(
            sum(1 for item in SCENARIOS if item.suite == "rollback"), 6
        )

    def test_every_scenario_has_a_goal_and_checks(self) -> None:
        for scenario in SCENARIOS:
            self.assertIsInstance(scenario, Scenario)
            self.assertTrue(scenario.title.strip(), scenario.id)
            self.assertTrue(scenario.goal.strip(), scenario.id)
            self.assertGreaterEqual(len(scenario.checks), 2, scenario.id)

    def test_recovery_scenarios_name_a_boundary(self) -> None:
        for scenario in SCENARIOS:
            if scenario.suite == "recovery":
                self.assertTrue(scenario.boundary, scenario.id)
            else:
                self.assertEqual(scenario.boundary, "", scenario.id)


class ReportTests(unittest.TestCase):
    def report(self) -> RecoveryReport:
        return RecoveryReport(
            runs=(
                recovery_run(status="passed"),
                recovery_run(
                    scenario="R2",
                    status="failed",
                    interrupted_detected=False,
                ),
                run(suite="rollback", scenario="B1", status="missed"),
            ),
            started_at="2026-11-19T00:00:00Z",
            duration_s=12.5,
            model="configured",
            artifacts="build/evals/recovery/artifacts",
        )

    def test_payload_keeps_exact_counts(self) -> None:
        data = payload(self.report())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["counts"]["runs"], 3)
        self.assertEqual(data["counts"]["passed"], 1)
        self.assertEqual(data["counts"]["failed"], 1)
        self.assertEqual(data["counts"]["missed"], 1)
        self.assertEqual(data["counts"]["errors"], 0)
        self.assertEqual(data["model"], "configured")

    def test_payload_keeps_ratio_parts(self) -> None:
        data = payload(self.report())
        detection = data["metrics"]["detection"]
        self.assertEqual(detection["numerator"], 1)
        self.assertEqual(detection["denominator"], 2)
        self.assertEqual(detection["value"], 0.5)
        self.assertTrue(detection["safety"])

    def test_payload_groups_suites(self) -> None:
        data = payload(self.report())
        suites = {suite["name"]: suite for suite in data["suites"]}
        self.assertEqual(set(suites), {"recovery", "rollback"})
        self.assertEqual(len(suites["recovery"]["scenarios"]), 2)
        self.assertEqual(len(suites["rollback"]["scenarios"]), 1)

    def test_markdown_names_every_metric_and_target(self) -> None:
        text = markdown(self.report())
        for label in (
            "Interrupt detection",
            "False interrupts",
            "Content preservation",
            "Tool replay",
            "Resume success",
            "Recovery time",
            "Classification accuracy",
            "User-edit overwrite",
            "Restore idempotency",
            "Restore success",
            "Budget completion",
        ):
            self.assertIn(label, text)
        self.assertIn("100%", text)
        self.assertIn("90%", text)

    def test_markdown_lists_missed_runs_separately(self) -> None:
        text = markdown(self.report())
        self.assertIn("Missed boundaries", text)
        self.assertIn("B1", text)

    def test_report_marks_safety_metrics(self) -> None:
        data = payload(self.report())
        metrics = data["metrics"]
        for name in (
            "detection",
            "false_interrupt",
            "preservation",
            "classification",
            "overwrite",
            "idempotency",
        ):
            self.assertTrue(metrics[name]["safety"], name)
        for name in ("resume_success", "restore_success", "budget_completion"):
            self.assertFalse(metrics[name]["safety"], name)


if __name__ == "__main__":
    unittest.main()
