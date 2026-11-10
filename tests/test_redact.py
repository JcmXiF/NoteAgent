"""Secrets are removed before a string can reach a log or an error."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.util.redact import (
    REDACTED,
    environment_secrets,
    redact,
    redact_value,
)
from noteagent.util.run_log import RunLogger


class RedactTests(unittest.TestCase):
    def test_a_secret_is_replaced_wherever_it_appears(self) -> None:
        key = "sk-live-1234567890"
        text = f"Authorization: Bearer {key}; retry with {key}"

        self.assertEqual(
            redact(text, (key,)),
            f"Authorization: Bearer {REDACTED}; retry with {REDACTED}",
        )

    def test_a_short_configured_secret_is_still_replaced(self) -> None:
        # Discovery ignores placeholders, but a caller that names the configured
        # key knows it is a secret even when it is short.
        self.assertEqual(
            redact("the value is EMPTY", ("EMPTY",)),
            f"the value is {REDACTED}",
        )

    def test_the_longest_secret_is_replaced_first(self) -> None:
        short = "sk-12345678"
        long = "sk-12345678-extended"

        self.assertEqual(redact(long, (short, long)), REDACTED)

    def test_values_are_redacted_through_containers(self) -> None:
        key = "sk-secret-value-1"
        value = {
            "message": f"failed with {key}",
            "items": [f"Bearer {key}", 3],
            "ok": True,
        }

        self.assertEqual(
            redact_value(value, (key,)),
            {
                "message": f"failed with {REDACTED}",
                "items": [f"Bearer {REDACTED}", 3],
                "ok": True,
            },
        )


class EnvironmentTests(unittest.TestCase):
    def test_only_secret_named_values_of_a_usable_length_count(self) -> None:
        environ = {
            "NOTEAGENT_LLM_API_KEY": "sk-live-1234567890",
            "SEARCH_TOKEN": "token-value-1",
            "SOME_PASSWORD": "short",
            "NOTEAGENT_LOG_DIR": "/tmp/logs",
            "KEYBOARD_LAYOUT": "us",
        }

        self.assertEqual(
            environment_secrets(environ),
            ("sk-live-1234567890", "token-value-1"),
        )

    def test_a_secret_does_not_reach_the_run_log(self) -> None:
        key = "sk-live-1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-log.jsonl"
            logger = RunLogger(path=path)
            with patch.dict(
                "os.environ", {"NOTEAGENT_LLM_API_KEY": key}, clear=False
            ):
                logger.append(
                    "fetch.error",
                    message=f"upstream said {key}",
                    reasons=[f"Bearer {key}"],
                )

            written = path.read_text(encoding="utf-8")
            self.assertNotIn(key, written)
            self.assertIn(REDACTED, written)
            json.loads(written)


if __name__ == "__main__":
    unittest.main()
