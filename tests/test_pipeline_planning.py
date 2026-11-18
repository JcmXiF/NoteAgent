"""Planning's limits: the answers a plan can hold match the pages it names."""

from __future__ import annotations

import unittest

from noteagent.ingest.clips import decisions_from_raw

#: The most pages one plan names; an answer fits it only if it fits its pages.
#: Spelled out rather than imported, so a cap that drifts away from the plan's
#: page limit fails here instead of moving the assertion with it.
PLAN_PAGES = 24


def _items(count: int) -> list[dict[str, object]]:
    return [
        {"slug": f"p{index}", "action": "write", "sectionIds": []}
        for index in range(count)
    ]


class DecisionCapTests(unittest.TestCase):
    def test_a_decision_for_every_page_is_accepted(self) -> None:
        self.assertEqual(len(decisions_from_raw(_items(PLAN_PAGES))), PLAN_PAGES)

    def test_one_more_than_a_plan_can_name_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            decisions_from_raw(_items(PLAN_PAGES + 1))


if __name__ == "__main__":
    unittest.main()
