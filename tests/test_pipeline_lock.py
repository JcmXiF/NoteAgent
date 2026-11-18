"""The project lock: one writer across processes, and a gone one lets go."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.pipeline.lock import ProjectBusy, ProjectLock


def _clock(hour: int, minute: int = 0) -> FixedClock:
    return FixedClock(datetime(2026, 11, 10, hour, minute, tzinfo=timezone.utc))


def _lock(path: Path, hour: int, minute: int = 0, *, alive=None) -> ProjectLock:
    return ProjectLock(
        path,
        clock=_clock(hour, minute),
        stale_after_s=60,
        alive=alive,
    )


class ProjectLockTests(unittest.TestCase):
    def test_a_second_owner_is_refused_while_the_first_holds_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            first = _lock(path, 9)
            first.acquire("pr-1")

            second = _lock(path, 9)
            with self.assertRaises(ProjectBusy) as caught:
                second.acquire("pr-2")
            self.assertEqual(caught.exception.holder["owner"], "pr-1")

    def test_a_gone_owner_lets_the_next_one_take_over(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            _lock(path, 9).acquire("pr-1")

            late = _lock(path, 9, 5, alive=lambda pid: False)
            late.acquire("pr-2")
            self.assertEqual(late.read()["owner"], "pr-2")

    def test_a_dead_owner_with_a_fresh_heartbeat_is_stale(self) -> None:
        # A core killed and restarted quickly leaves a fresh heartbeat behind.
        # The process id is what says the owner is gone.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            path.write_text(
                json.dumps(
                    {
                        "owner": "pr-1",
                        "pid": 4242,
                        "acquiredAt": "2026-11-10T09:00:00Z",
                        "heartbeatAt": "2026-11-10T09:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            late = _lock(path, 9, 0, alive=lambda pid: False)
            late.acquire("pr-2")
            self.assertEqual(late.read()["owner"], "pr-2")

    def test_a_live_owner_is_not_stale_however_old_its_heartbeat(self) -> None:
        # A stage can run for minutes; a live process must keep its project.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            _lock(path, 9).acquire("pr-1")

            late = _lock(path, 9, 5, alive=lambda pid: True)
            with self.assertRaises(ProjectBusy):
                late.acquire("pr-2")

    def test_an_unreadable_holder_is_not_taken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            path.write_text("", encoding="utf-8")

            with self.assertRaises(ProjectBusy):
                _lock(path, 9).acquire("pr-2")

    def test_a_holder_without_a_pid_goes_stale_after_its_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            path.write_text(
                json.dumps({"owner": "pr-1", "heartbeatAt": "2026-11-10T09:00:00Z"}),
                encoding="utf-8",
            )

            late = _lock(path, 9, 5)
            late.acquire("pr-2")
            self.assertEqual(late.read()["owner"], "pr-2")

    def test_only_the_owner_can_release_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            first = _lock(path, 9)
            first.acquire("pr-1")

            other = _lock(path, 9)
            self.assertFalse(other.release("pr-2"))
            self.assertEqual(first.read()["owner"], "pr-1")
            self.assertTrue(first.release("pr-1"))
            self.assertIsNone(first.read())

    def test_heartbeat_updates_the_holder_it_belongs_to(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.lock"
            _lock(path, 9).acquire("pr-1")

            late = _lock(path, 9, 5)
            self.assertFalse(late.heartbeat("pr-2"))
            self.assertTrue(late.heartbeat("pr-1"))
            self.assertEqual(late.read()["heartbeatAt"], "2026-11-10T09:05:00Z")


if __name__ == "__main__":
    unittest.main()
