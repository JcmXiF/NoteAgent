"""Recovering the run a crash left running, without inventing one."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.run_marker import (
    FAILED,
    FINISHED,
    INTERRUPTED,
    RUNNING,
    WAITING,
    RunMarker,
    marker_entry,
)
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import recover_active_project_runs
from noteagent.api.sessions import session_payload
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore
from noteagent.sessions.recovery import (
    interrupted_payload,
    interrupted_runs,
    recover_store,
)

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]


class RunRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout,
            clock=FixedClock(),
            ids=SequentialIds(),
        )

    def create(self, session_id: str = "s1") -> str:
        self.store.create(session_id)
        return session_id

    def user(self, session_id: str, content: str = "问题") -> None:
        self.store.append(
            session_id,
            {"type": "message", "role": "user", "content": content},
        )

    def start(self, session_id: str, run_id: str = "run-1") -> None:
        self.store.append(
            session_id, marker_entry(RunMarker(run_id, RUNNING))
        )

    def assistant(
        self,
        session_id: str,
        *,
        tool_calls: bool = False,
        content: str = "答案",
    ) -> None:
        entry: dict[str, object] = {
            "type": "message",
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            entry["toolCalls"] = [
                {"id": "c1", "name": "wiki_read", "arguments": "{}"}
            ]
        self.store.append(session_id, entry)

    def finish(self, session_id: str, run_id: str = "run-1") -> None:
        self.store.append(
            session_id, marker_entry(RunMarker(run_id, FINISHED))
        )

    def markers(self, session_id: str = "s1") -> list[dict]:
        return [
            entry
            for entry in self.store.load(session_id).entries
            if entry["type"] == "run_marker"
        ]

    def last_content_id(self, session_id: str = "s1") -> str:
        entries = [
            entry
            for entry in self.store.load(session_id).entries
            if entry["type"] != "run_marker"
        ]
        return str(entries[-1]["id"])

    def test_a_finished_answer_repairs_the_missing_terminal_marker(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(
            [marker["state"] for marker in self.markers(session)],
            [RUNNING, FINISHED],
        )
        self.assertEqual(interrupted_runs(self.store), ())

    def test_a_tool_call_without_a_result_is_interrupted(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].run_id, "run-1")
        self.assertEqual(found[0].step, 1)
        self.assertEqual(found[0].reason, "tool call without result")
        self.assertEqual(found[0].last_message, "答案")
        self.assertEqual(self.markers(session)[-1]["state"], INTERRUPTED)
        self.assertEqual(interrupted_runs(self.store)[0].session_id, session)

        state = session_payload(self.store, session)["state"]
        interrupted = state["interrupted"]
        self.assertEqual(interrupted["runId"], "run-1")
        self.assertEqual(interrupted["step"], 1)
        self.assertEqual(interrupted["reason"], "tool call without result")
        self.assertEqual(
            interrupted["lastEntryId"], self.last_content_id(session)
        )
        self.assertTrue(interrupted["reason"])
        self.assertTrue(interrupted["at"])

    def test_a_question_without_an_answer_is_interrupted(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].step, 0)
        self.assertEqual(self.markers(session)[-1]["state"], INTERRUPTED)

    def test_a_settled_run_is_left_alone(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session)
        self.finish(session)
        before = len(self.store.load(session).entries)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(len(self.store.load(session).entries), before)
        self.assertEqual(interrupted_runs(self.store), ())

    def test_a_question_after_a_settled_run_is_recovered(self) -> None:
        session = self.create()
        self.user(session, "第一个问题")
        self.start(session, "run-1")
        self.assistant(session)
        self.finish(session, "run-1")
        self.user(session, "第二个问题")

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].reason, "question without answer")
        self.assertTrue(found[0].run_id.startswith("recovered-"))
        markers = self.markers(session)
        self.assertEqual(
            [marker["state"] for marker in markers[-2:]],
            [RUNNING, INTERRUPTED],
        )
        self.assertEqual(
            markers[-1]["runId"], found[0].run_id
        )
        self.assertEqual(
            markers[-1]["lastEntryId"], self.last_content_id(session)
        )
        self.assertEqual(
            session_payload(self.store, session)["state"]["interrupted"][
                "reason"
            ],
            "question without answer",
        )

    def test_a_failed_question_is_not_recovered_as_pending(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.store.append(
            session, marker_entry(RunMarker("run-1", FAILED))
        )

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(
            [marker["state"] for marker in self.markers(session)],
            [RUNNING, FAILED],
        )

    def test_a_waiting_run_is_not_turned_into_an_interruption(self) -> None:
        session = self.create()
        self.user(session)
        self.store.append(
            session, marker_entry(RunMarker("run-1", WAITING))
        )
        self.store.append(
            session,
            {
                "type": "wait_proposed",
                "id": "w1",
                "waitId": "w1",
                "job": "plan",
                "interface": "source",
                "completion": "start_plan",
                "proposedAt": "2026-11-10T09:00:00Z",
            },
        )
        self.user(session, "等待时又问了问题")

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(
            [marker["state"] for marker in self.markers(session)],
            [WAITING],
        )

    def test_an_old_session_without_a_marker_is_not_scanned(self) -> None:
        session = self.create()
        self.user(session)
        self.assistant(session)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(self.markers(session), [])
        self.assertEqual(interrupted_runs(self.store), ())

    def test_a_later_finished_run_hides_an_older_running_one(self) -> None:
        session = self.create()
        self.user(session, "第一个问题")
        self.start(session, "run-1")
        self.assistant(session, tool_calls=True)
        self.user(session, "第二个问题")
        self.start(session, "run-2")
        self.assistant(session)
        self.finish(session, "run-2")

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(found, ())
        self.assertEqual(self.markers(session)[-1]["state"], FINISHED)
        self.assertEqual(interrupted_runs(self.store), ())

    def test_a_newer_running_run_is_interrupted(self) -> None:
        session = self.create()
        self.user(session, "第一个问题")
        self.start(session, "run-1")
        self.assistant(session)
        self.finish(session, "run-1")
        self.user(session, "第二个问题")
        self.start(session, "run-2")
        self.assistant(session, tool_calls=True)

        found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].run_id, "run-2")
        self.assertEqual(found[0].step, 1)
        self.assertEqual(self.markers(session)[-1]["state"], INTERRUPTED)

    def test_a_corrupt_session_is_skipped(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)
        self.layout.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.layout.session_path("bad").write_text(
            '{"v":1,"type":"session","id":"bad","createdAt":"x"}\n'
            "{not json\n",
            encoding="utf-8",
        )

        with self.assertLogs("noteagent.sessions.jsonl", level="WARNING"):
            found = recover_store(self.store, clock=FixedClock())

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].session_id, session)

    def test_a_read_only_scan_does_not_write(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)
        before = len(self.store.load(session).entries)

        found = recover_store(self.store, clock=FixedClock(), mark=False)

        self.assertEqual(len(found), 1)
        self.assertEqual(len(self.store.load(session).entries), before)
        self.assertEqual(self.markers(session)[-1]["state"], RUNNING)

    def test_a_store_that_cannot_be_listed_is_not_fatal(self) -> None:
        class Broken:
            def list(self):
                raise OSError("gone")

        with self.assertLogs("noteagent.sessions.recovery", level="ERROR"):
            self.assertEqual(recover_store(Broken()), ())
        with self.assertLogs("noteagent.sessions.recovery", level="ERROR"):
            self.assertEqual(interrupted_runs(Broken()), ())

    def test_the_payload_is_none_without_an_interrupted_marker(self) -> None:
        session = self.create()
        self.user(session)
        self.assistant(session)

        self.assertIsNone(
            interrupted_payload(self.store.load(session).entries)
        )

    def test_startup_recovery_is_a_noop_without_a_project(self) -> None:
        with (
            patch(
                "noteagent.api.session_access.get_active_layout",
                side_effect=OSError("gone"),
            ),
            self.assertLogs(
                "noteagent.api.session_access", level="WARNING"
            ),
        ):
            recover_active_project_runs(AppDeps())

    def test_startup_recovery_uses_the_active_project_store(self) -> None:
        session = self.create()
        self.user(session)
        self.start(session)
        self.assistant(session, tool_calls=True)

        with patch(
            "noteagent.api.session_access.get_active_layout",
            return_value=self.layout,
        ):
            recover_active_project_runs(AppDeps(clock=FixedClock()))

        self.assertEqual(self.markers(session)[-1]["state"], INTERRUPTED)

    def test_startup_recovery_ignores_an_unreadable_store(self) -> None:
        class Broken:
            def list(self):
                raise OSError("gone")

        with (
            patch(
                "noteagent.api.session_access.get_active_layout",
                return_value=self.layout,
            ),
            patch(
                "noteagent.api.session_access._default_store",
                return_value=Broken(),
            ),
            self.assertLogs(
                "noteagent.sessions.recovery", level="ERROR"
            ),
        ):
            recover_active_project_runs(AppDeps())

    def test_the_lifespan_runs_the_recovery_scan(self) -> None:
        if TestClient is None or create_app is None:
            raise unittest.SkipTest("fastapi is not installed")
        with patch(
            "noteagent.api.app.recover_active_project_runs"
        ) as recover:
            with TestClient(create_app(AppDeps())):
                pass
        recover.assert_called_once()


if __name__ == "__main__":
    unittest.main()
