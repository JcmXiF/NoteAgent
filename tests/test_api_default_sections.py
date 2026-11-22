"""A product session starts with role, tools, project, grounding and skills."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FakeModelClient

from evals.harness import CaseRun

BRIEF = "# Brief\n\n信息论 wiki：熵、联合熵、KL 散度与互信息。\n"


class DefaultSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run = CaseRun(Path(self._tmp.name))
        self.addCleanup(self.run.close)
        self.run.layout.brief.parent.mkdir(parents=True, exist_ok=True)
        self.run.layout.brief.write_text(BRIEF, encoding="utf-8")
        self.model = FakeModelClient("答案")
        self.api = self.run.start(
            self.model, seed_system_sections=True
        )

    def sections(self, session_id: str) -> dict[str, str]:
        view = self.api.get_session(session_id)
        return dict(view["derived"]["sections"])

    def system_prompt(self, index: int = 0) -> str:
        request = self.model.requests[index]
        return "\n\n".join(
            message.content
            for message in request.messages
            if message.role == "system"
        )

    def test_a_new_session_carries_the_defaults(self) -> None:
        session = self.api.create_session(title="默认")

        sections = self.sections(session)

        self.assertIn("NoteAgent", sections["identity"])
        self.assertIn("Available tools:", sections["tools"])
        self.assertIn("- `search(query)`", sections["tools"])
        self.assertIn("- `wiki_read(slug)`", sections["tools"])
        for tool in (
            "web_search",
            "select_page",
            "select_frame",
            "compose_wiki",
            "write_wiki",
            "get_state",
            "skill(name)",
            "skill_read_file",
        ):
            self.assertIn(tool, sections["tools"])
        self.assertNotIn("cannot write", sections["tools"])
        self.assertIn("grounding", sections)
        self.assertIn("信息论 wiki", sections["project"])
        self.assertIn("<available_skills>", sections["skills"])
        self.assertIn("- writing [", sections["skills"])

    def test_the_prompt_reads_the_defaults(self) -> None:
        session = self.api.create_session(title="默认")

        self.api.query(session, "什么是熵？")

        prompt = self.system_prompt()
        self.assertIn("NoteAgent", prompt)
        self.assertIn("search", prompt)
        self.assertIn("信息论 wiki", prompt)
        self.assertIn("Answer only from this project's wiki", prompt)
        self.assertIn("<available_skills>", prompt)

    def test_repeated_queries_do_not_duplicate_the_section(self) -> None:
        session = self.api.create_session(title="默认")
        self.api.query(session, "第一问")
        self.api.query(session, "第二问")

        count = len(
            [
                entry
                for entry in self.api.entries(session)
                if entry.get("type") == "system_section"
            ]
        )
        self.assertEqual(count, 1)

    def test_an_old_session_is_backfilled_on_query(self) -> None:
        self.run.store.create("old")

        self.api.query("old", "什么问题？")

        sections = self.sections("old")
        self.assertIn("NoteAgent", sections["identity"])
        self.assertIn("信息论 wiki", sections["project"])

    def test_a_custom_section_is_not_overwritten(self) -> None:
        session = self.api.create_session(title="默认")
        self.run.store.append(
            session,
            {
                "type": "system_section",
                "sections": {"identity": "CUSTOM IDENTITY"},
            },
        )

        self.api.query(session, "什么问题？")

        prompt = self.system_prompt()
        self.assertIn("CUSTOM IDENTITY", prompt)
        self.assertNotIn("You are NoteAgent", prompt)


if __name__ == "__main__":
    unittest.main()
