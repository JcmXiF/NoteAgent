"""The V4 runner: local atoms, the report, and the failure library."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.quality.v4.atoms import LOCAL_ATOMS
from evals.quality.v4.run import main as run_main
from evals.quality.v4.runner import (
    AtomCase,
    AtomResult,
    V4Report,
    run_atoms,
)
from evals.report import Check, STATE, TOOL_CONTRACT

from noteagent.agent.faux import FauxModelClient


def _failing_atom(run, recorder) -> None:
    api = run.start(recorder.wrap(FauxModelClient.text("答案。")))
    session = api.create_session()
    api.query(session, "你好")
    run.check("always false", False, "boom", TOOL_CONTRACT)


def _passing_runner(atom, _failures_root, _run_id) -> AtomResult:
    return AtomResult(
        atom.name,
        atom.title,
        (Check("ok", True, "", TOOL_CONTRACT),),
        0.1,
        tokens_in=1,
        tokens_out=2,
    )


def _live_fails(atom, failures_root, run_id) -> AtomResult:
    if atom.name.startswith("live"):
        return AtomResult(
            atom.name,
            atom.title,
            (Check("live down", False, "no network", TOOL_CONTRACT),),
            0.1,
        )
    return _passing_runner(atom, failures_root, run_id)


class AtomRunnerTests(unittest.TestCase):
    def test_the_four_local_atoms_pass_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_atoms(
                LOCAL_ATOMS, root / "failures", run_id="test"
            )
            failures = list((root / "failures").rglob("failure.json"))

        self.assertEqual(len(LOCAL_ATOMS), 4)
        self.assertEqual(
            [atom.name for atom in report.atoms],
            [atom.name for atom in LOCAL_ATOMS],
        )
        for atom in report.atoms:
            self.assertTrue(
                atom.passed,
                f"{atom.name}: {atom.error_line} "
                f"{[check.name for check in atom.failed_checks]}",
            )
            self.assertTrue(atom.checks)
        self.assertEqual(failures, [])
        self.assertEqual(report.tokens_in, 0)
        self.assertEqual(report.tokens_out, 0)

    def test_the_live_smoke_is_reported_apart(self) -> None:
        live_atom = AtomCase(
            "live-x", "Live", lambda run, counter: None
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_atoms(
                LOCAL_ATOMS,
                Path(tmp) / "failures",
                live=(live_atom,),
                run_atom=_live_fails,
                run_id="run-1",
            )
            payload = report.payload()
            summary = report.markdown()

        self.assertEqual(
            [atom.name for atom in report.live], ["live-x"]
        )
        self.assertEqual(len(payload["atoms"]), len(LOCAL_ATOMS))
        self.assertEqual(len(payload["live"]), 1)
        self.assertEqual(payload["localTotal"], len(LOCAL_ATOMS))
        self.assertEqual(payload["localPassed"], len(LOCAL_ATOMS))
        self.assertEqual(payload["liveTotal"], 1)
        self.assertEqual(payload["livePassed"], 0)
        self.assertEqual(report.local_passed_count, len(LOCAL_ATOMS))
        self.assertEqual(report.passed_count, len(LOCAL_ATOMS))
        self.assertIn("## Local atoms", summary)
        self.assertIn("## Live smoke", summary)
        self.assertIn(
            f"Local atoms: **{len(LOCAL_ATOMS)} passed, 0 failed**",
            summary,
        )
        self.assertIn("Live smoke: **0 passed, 1 failed**", summary)

    def test_an_atom_passes_share_settings_to_the_case(self) -> None:
        seen: dict[str, bool] = {}

        def atom_run(run, counter) -> None:
            seen["shared"] = run.share_settings
            run.check(
                "settings were shared",
                run.share_settings,
                "",
                TOOL_CONTRACT,
            )

        atom = AtomCase(
            "atom-settings",
            "Settings",
            atom_run,
            share_settings=True,
        )

        with tempfile.TemporaryDirectory() as tmp:
            report = run_atoms(
                (atom,), Path(tmp) / "failures", run_id="run-1"
            )

        self.assertTrue(report.atoms[0].passed)
        self.assertTrue(seen.get("shared"))

    def test_a_failed_atom_keeps_its_project_and_writes_an_entry(
        self,
    ) -> None:
        atom = AtomCase("atom-fail", "It fails", _failing_atom)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = run_atoms(
                (atom,), root / "failures", run_id="run-1"
            )
            entry_path = (
                root / "failures" / "run-1" / "atom-fail"
                / "failure.json"
            )
            entry = json.loads(
                entry_path.read_text(encoding="utf-8")
            )
            project = entry_path.parent / "project"
            project_exists = project.is_dir()
            sessions = list(
                (project / ".noteagent" / "sessions").glob("*.jsonl")
            )
            events = list(
                (entry_path.parent / "events").glob("*.events.jsonl")
            )

        self.assertFalse(report.atoms[0].passed)
        self.assertTrue(report.atoms[0].artifacts)
        self.assertFalse(entry["passed"])
        self.assertEqual(
            [check["name"] for check in entry["checks"]],
            ["always false"],
        )
        self.assertIn("boom", entry["checks"][0]["detail"])
        self.assertTrue(project_exists)
        self.assertTrue(sessions)
        self.assertTrue(events)


class ReportTests(unittest.TestCase):
    def test_the_payload_keeps_checks_time_tokens_and_artifacts(self) -> None:
        passed = AtomResult(
            "atom-ok",
            "Passes",
            (Check("one", True, "", TOOL_CONTRACT),),
            0.5,
            tokens_in=3,
            tokens_out=1,
        )
        failed = AtomResult(
            "atom-bad",
            "Fails",
            (Check("two", False, "why", STATE),),
            1.5,
            tokens_in=7,
            tokens_out=2,
            error="traceback\nthe last line",
            artifacts="failures/run-1/atom-bad",
        )
        report = V4Report(
            (passed, failed), "run-1", "now", 2.5
        )

        payload = report.payload()

        self.assertEqual(payload["passed"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["tokensIn"], 10)
        self.assertEqual(payload["tokensOut"], 3)
        self.assertEqual(
            payload["atoms"][1]["artifacts"],
            "failures/run-1/atom-bad",
        )
        self.assertEqual(
            payload["atoms"][1]["checks"][0]["detail"], "why"
        )
        summary = report.markdown()
        self.assertIn("atom-bad", summary)
        self.assertIn("two: why", summary)
        self.assertIn("the last line", summary)


class CliTests(unittest.TestCase):
    def test_the_cli_writes_the_json_and_markdown_report(self) -> None:
        atom = AtomCase(
            "atom-cli", "CLI", lambda run, recorder: None
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            code = run_main(
                [
                    "--report",
                    str(report_path),
                    "--failures",
                    str(root / "failures"),
                ],
                atoms=(atom,),
                run_atom=_passing_runner,
                run_id="run-1",
            )
            payload = json.loads(
                report_path.read_text(encoding="utf-8")
            )
            markdown_written = report_path.with_suffix(
                ".md"
            ).is_file()

        self.assertEqual(code, 0)
        self.assertEqual(payload["atoms"][0]["tokensIn"], 1)
        self.assertTrue(markdown_written)

    def test_the_cli_runs_the_live_smoke_only_when_asked(self) -> None:
        local_atom = AtomCase(
            "atom-local", "Local", lambda run, counter: None
        )
        live_atom = AtomCase(
            "live-x", "Live", lambda run, counter: None
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            without = root / "without.json"
            run_main(
                [
                    "--report",
                    str(without),
                    "--failures",
                    str(root / "f1"),
                ],
                atoms=(local_atom,),
                live=(live_atom,),
                run_atom=_passing_runner,
                run_id="run-1",
            )
            without_live = json.loads(
                without.read_text(encoding="utf-8")
            )
            with_live_path = root / "with.json"
            run_main(
                [
                    "--report",
                    str(with_live_path),
                    "--failures",
                    str(root / "f2"),
                    "--live",
                ],
                atoms=(local_atom,),
                live=(live_atom,),
                run_atom=_passing_runner,
                run_id="run-2",
            )
            with_live = json.loads(
                with_live_path.read_text(encoding="utf-8")
            )

        self.assertEqual(without_live["live"], [])
        self.assertEqual(without_live["liveTotal"], 0)
        self.assertEqual(
            [atom["name"] for atom in with_live["live"]],
            ["live-x"],
        )
        self.assertEqual(with_live["localTotal"], 1)
        self.assertEqual(with_live["liveTotal"], 1)

    def test_the_cli_records_a_failure_and_still_completes(self) -> None:
        atom = AtomCase("atom-fail", "Fails", _failing_atom)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            code = run_main(
                [
                    "--report",
                    str(report_path),
                    "--failures",
                    str(root / "failures"),
                ],
                atoms=(atom,),
                run_id="run-1",
            )
            payload = json.loads(
                report_path.read_text(encoding="utf-8")
            )
            failure = (
                root / "failures" / "run-1" / "atom-fail"
                / "failure.json"
            ).is_file()

        self.assertEqual(code, 0)
        self.assertEqual(payload["failed"], 1)
        self.assertTrue(failure)

    def test_ci_refuses_and_writes_nothing(self) -> None:
        atom = AtomCase(
            "atom-cli", "CLI", lambda run, recorder: None
        )

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            with patch.dict(os.environ, {"CI": "1"}, clear=False):
                code = run_main(
                    [
                        "--report",
                        str(report_path),
                        "--failures",
                        str(Path(tmp) / "failures"),
                    ],
                    atoms=(atom,),
                    run_atom=_passing_runner,
                )
            written = report_path.is_file()

        self.assertEqual(code, 2)
        self.assertFalse(written)


if __name__ == "__main__":
    unittest.main()
