from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import noteagent.api.attention as attention_mod
from noteagent.api.attention import attention_payload
from noteagent.project.init import init_project


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
                    attention_mod,
                    "doctor_payload",
                    return_value={"errors": 1, "warnings": 3, "findings": []},
                ),
            ):
                payload = attention_payload(layout)

            self.assertEqual(
                payload["counts"], {"revision": 2, "recovery": 1, "doctor": 4}
            )
            self.assertEqual(payload["total"], 7)

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
            self.assertIsNotNone(payload["doctor"])
            self.assertIn("findings", payload["doctor"])


if __name__ == "__main__":
    unittest.main()
