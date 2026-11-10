"""Time and identity: the real adapters, and the deterministic ones a test uses."""

from __future__ import annotations

import unittest
from datetime import timezone

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.clock import SystemClock, UuidIds


class ClockTests(unittest.TestCase):
    def test_the_system_clock_is_utc(self) -> None:
        self.assertEqual(SystemClock().now().tzinfo, timezone.utc)

    def test_ids_do_not_collide(self) -> None:
        ids = UuidIds()
        self.assertNotEqual(ids.new_id("run"), ids.new_id("run"))

    def test_a_fixed_clock_and_sequential_ids_make_a_test_repeatable(self) -> None:
        clock = FixedClock()
        ids = SequentialIds()
        self.assertEqual(clock.now(), clock.now())
        self.assertEqual(ids.new_id("run"), "run-1")
        self.assertEqual(ids.new_id("run"), "run-2")


if __name__ == "__main__":
    unittest.main()
