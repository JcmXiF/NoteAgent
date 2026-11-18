from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import noteagent.api.attention as attention_mod
from noteagent.agent.run_marker import INTERRUPTED, RunMarker, marker_entry
from noteagent.api.attention import attention_payload
from noteagent.project.init import init_project
from noteagent.sessions.jsonl import JsonlSessionStore


class AttentionPayloadTests(unittest.TestCase):
    """What the panel is handed: one number over the three queues."""

    def test_the_queues_are_counted_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with (
                patch.object(
                    attention_mod,
                    "revision_inbox_payload",
                    return_value={"openCount": 2, "items": [{"identity": "raw/web/a.md"}]},
                ),
                patch.object(
                    attention_mod,
                    "recovery_payload",
                    return_value={"openCount": 1, "items": [{"id": "job"}], "last": None},
                ),
                patch.object(
                    attention_mod.CaptureInbox,
                    "payload",
                    return_value={"openCount": 1, "items": [{"id": "capture-1"}]},
                ),
                patch.object(
                    attention_mod,
                    "doctor_payload",
                    return_value={"errors": 1, "warnings": 3, "findings": []},
                ),
            ):
                payload = attention_payload(layout)

            self.assertEqual(
                payload["counts"],
                {
                    "revision": 2,
                    "recovery": 1,
                    "capture": 1,
                    "doctor": 4,
                    "interrupted": 0,
                },
            )
            self.assertEqual(payload["total"], 8)

    def test_a_repeat_read_does_not_run_the_check(self) -> None:
        # The check reads every page, so it is asked for once, not on every poll.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with (
                patch.object(
                    attention_mod,
                    "revision_inbox_payload",
                    return_value={"openCount": 0, "items": []},
                ),
                patch.object(
                    attention_mod,
                    "recovery_payload",
                    return_value={"openCount": 0, "items": [], "last": None},
                ),
                patch.object(attention_mod, "doctor_project") as checked,
            ):
                payload = attention_payload(layout, check=False)

            checked.assert_not_called()
            self.assertIsNone(payload["doctor"])
            self.assertEqual(payload["counts"]["doctor"], 0)
            self.assertEqual(payload["total"], 0)

    def test_a_healthy_project_says_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = attention_payload(layout)
            self.assertEqual(payload["counts"]["revision"], 0)
            self.assertEqual(payload["counts"]["recovery"], 0)
            self.assertEqual(payload["counts"]["capture"], 0)
            self.assertEqual(payload["counts"]["interrupted"], 0)
            self.assertIsNotNone(payload["doctor"])
            self.assertIn("findings", payload["doctor"])

    def test_an_interrupted_run_is_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            store = JsonlSessionStore(layout)
            store.create("s1", "Interrupted")
            store.append(
                "s1",
                {"type": "message", "role": "user", "content": "问题"},
            )
            store.append(
                "s1",
                marker_entry(
                    RunMarker(
                        "run-1",
                        INTERRUPTED,
                        note="the run was interrupted",
                        created_at="2026-11-18T09:00:00Z",
                    )
                ),
            )

            payload = attention_payload(layout, check=False)
            item = payload["interruptedRuns"][0]

            self.assertEqual(payload["counts"]["interrupted"], 1)
            self.assertEqual(payload["total"], 1)
            self.assertEqual(item["sessionId"], "s1")
            self.assertEqual(item["title"], "Interrupted")
            self.assertEqual(item["runId"], "run-1")
            self.assertEqual(item["step"], 0)
            self.assertEqual(item["lastMessage"], "问题")


if __name__ == "__main__":
    unittest.main()
