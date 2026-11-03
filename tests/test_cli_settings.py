"""The command line and the desktop core read the same settings.

Each command that needs a model or a search provider applies the saved document
to its own process first, exactly as the API does. There is no second file for
the CLI to read.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.__main__ import _apply_saved_settings, cmd_acquire_status, cmd_analyze
from noteagent.acquire.search_config import SearchConfig
from noteagent.llm.config import LlmConfigError
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal


class CliSettingsTests(unittest.TestCase):
    def test_a_command_applies_the_saved_document_before_reading_settings(self) -> None:
        order: list[str] = []

        def view() -> None:
            order.append("settings")

        def search() -> SearchConfig:
            order.append("search")
            return SearchConfig(provider="brave", api_key="tok")

        with (
            patch("noteagent.settings.install.install_view", view),
            patch("noteagent.acquire.search_config.load_search_config", search),
        ):
            code = cmd_acquire_status()

        self.assertEqual(order, ["settings", "search"])
        self.assertEqual(code, 0)

    def test_analyze_applies_settings_before_reading_the_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            source = layout.raw / "note.md"
            source.write_text("# Note\n\nBody.\n", encoding="utf-8")
            order: list[str] = []

            def view() -> None:
                order.append("settings")

            def load() -> object:
                order.append("model")
                raise LlmConfigError("no model")

            with (
                patch("noteagent.settings.install.install_view", view),
                patch("noteagent.__main__.load_llm_config", load),
            ):
                code = cmd_analyze(layout, source, dry_run=False)

        self.assertEqual(order, ["settings", "model"])
        self.assertEqual(code, 2)

    def test_a_damaged_document_is_reported(self) -> None:
        def broken() -> None:
            raise Refusal("settings.configInvalid", "the document is damaged")

        with patch("noteagent.settings.install.install_view", broken):
            code = cmd_acquire_status()

        self.assertEqual(code, 2)

if __name__ == "__main__":
    unittest.main()
