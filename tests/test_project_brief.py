from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.chat.limits import default_limits, limits_to_dict, load_limits, save_limits
from noteagent.io.read_source import SourceDocument
from noteagent.project.init import init_project
from noteagent.prompts.analysis import build_analysis_prompt
from noteagent.prompts.context import DEFAULT_BRIEF, MAX_BRIEF_CHARS, read_brief


class BriefTests(unittest.TestCase):
    def test_default_when_the_project_has_no_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertEqual(read_brief(layout), DEFAULT_BRIEF.strip())

    def test_brief_file_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.legacy_purpose.write_text("# old purpose\n", encoding="utf-8")
            layout.config.mkdir()
            layout.brief.write_text("# my brief\n", encoding="utf-8")
            self.assertIn("my brief", read_brief(layout))
            self.assertNotIn("old purpose", read_brief(layout))

    def test_legacy_purpose_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.legacy_purpose.write_text("# old purpose\n", encoding="utf-8")
            self.assertIn("old purpose", read_brief(layout))

    def test_brief_is_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.config.mkdir()
            layout.brief.write_text("x" * (MAX_BRIEF_CHARS + 50), encoding="utf-8")
            text = read_brief(layout)
            self.assertLess(len(text), MAX_BRIEF_CHARS + 30)
            self.assertIn("truncated", text)

    def test_init_creates_no_retired_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertFalse(layout.brief.exists())
            self.assertFalse(layout.legacy_purpose.exists())
            self.assertFalse(layout.legacy_overview.exists())
            self.assertFalse(layout.config.exists())

    def test_prompt_carries_the_brief_and_not_the_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.config.mkdir()
            layout.brief.write_text("# Write compactly\n", encoding="utf-8")
            layout.root.joinpath("schema.md").write_text(
                "# Page types\n\n- secret schema text\n", encoding="utf-8"
            )
            doc = SourceDocument(
                project_root=layout.root,
                identity="raw/demo.md",
                path=layout.raw / "demo.md",
                content="# Demo\n\nA claim.\n",
            )
            prompt = build_analysis_prompt(doc, layout)
            self.assertIn("Write compactly", prompt.system)
            self.assertNotIn("secret schema text", prompt.system)


class ChatLimitsTests(unittest.TestCase):
    def test_limits_live_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_limits(layout, default_limits())
            self.assertTrue(layout.chat_limits.is_file())
            self.assertEqual(layout.chat_limits.parent.name, "config")
            self.assertFalse(layout.meta.joinpath("chat-limits.json").exists())

    def test_legacy_limits_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = limits_to_dict(default_limits())
            payload["maxBriefChars"] = 1_234
            layout.meta.mkdir(parents=True, exist_ok=True)
            layout.legacy_chat_limits.write_text(
                json.dumps(payload), encoding="utf-8"
            )
            self.assertEqual(load_limits(layout).max_brief_chars, 1_234)


if __name__ == "__main__":
    unittest.main()
