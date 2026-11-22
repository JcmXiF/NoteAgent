"""The product prompt is versioned, and an old session migrates once."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent_fakes import FakeSessionStore

from noteagent.agent.projection import project
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.system import (
    PROMPT_VERSION,
    SKILLS_SECTION,
    sync_default_sections,
)
from noteagent.skills import SkillRegistry, SkillRoot, SkillRootKind


def _skill(folder: Path, name: str) -> Path:
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when needed.\n"
        "---\n\n"
        "Skill body.\n",
        encoding="utf-8",
    )
    return path


class PromptMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.layout = ProjectLayout(self.base / "project")
        self.layout.brief.parent.mkdir(parents=True)
        self.layout.brief.write_text(
            "# Brief\n\nFixture wiki.", encoding="utf-8"
        )
        self.skills = self.base / "skills"
        self.registry = SkillRegistry(
            (SkillRoot(SkillRootKind.PROJECT, self.skills),)
        )
        self.store = FakeSessionStore()

    def _sections(self, session_id: str) -> dict[str, str]:
        return project(self.store.load(session_id).entries).sections

    def _versioned(self, session_id: str) -> list[dict[str, object]]:
        return [
            entry
            for entry in self.store.load(session_id).entries
            if "promptVersion" in entry
        ]

    def _sync(self, session_id: str) -> bool:
        return sync_default_sections(
            self.store, session_id, self.layout, registry=self.registry
        )

    def test_an_old_session_migrates_and_keeps_custom_keys(self) -> None:
        self.store.create("old")
        self.store.append(
            "old",
            {
                "type": "system_section",
                "sections": {
                    "identity": "You use propose(job, interface).",
                    "tools": "Available tools: propose(job, interface).",
                    "grounding": "Old grounding.",
                    "project": "OLD PROJECT",
                    "custom": "KEEP ME",
                },
            },
        )
        _skill(self.skills, "writing")

        self.assertTrue(self._sync("old"))

        sections = self._sections("old")
        self.assertEqual(sections["custom"], "KEEP ME")
        self.assertIn("NoteAgent", sections["identity"])
        self.assertNotIn("propose", sections["identity"])
        self.assertNotIn("propose", sections["tools"])
        self.assertNotIn("cannot write", sections["tools"])
        self.assertEqual(
            sections["project"], "# Brief\n\nFixture wiki."
        )
        self.assertIn("<available_skills>", sections[SKILLS_SECTION])
        self.assertEqual(
            self._versioned("old")[-1]["promptVersion"], PROMPT_VERSION
        )

    def test_a_migrated_session_is_not_appended_again(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")

        self.assertTrue(self._sync("s"))
        self.assertFalse(self._sync("s"))

        self.assertEqual(len(self._versioned("s")), 1)

    def test_a_new_skill_refreshes_the_index_once(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")
        self._sync("s")

        _skill(self.skills, "search")
        self.assertTrue(self._sync("s"))

        self.assertIn("search", self._sections("s")[SKILLS_SECTION])
        self.assertFalse(self._sync("s"))
        self.assertEqual(len(self._versioned("s")), 2)

    def test_when_the_last_skill_goes_the_section_is_removed(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")
        self._sync("s")

        shutil.rmtree(self.skills)
        self.assertTrue(self._sync("s"))

        self.assertNotIn(SKILLS_SECTION, self._sections("s"))
        self.assertFalse(self._sync("s"))

    def test_a_current_session_keeps_a_custom_product_value(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")
        self._sync("s")
        self.store.append(
            "s",
            {
                "type": "system_section",
                "sections": {"identity": "CUSTOM IDENTITY"},
            },
        )

        self.assertFalse(self._sync("s"))

        self.assertEqual(
            self._sections("s")["identity"], "CUSTOM IDENTITY"
        )

    def test_a_reset_starts_the_sections_and_the_version_again(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")
        self._sync("s")
        self.store.append("s", {"type": "reset", "head": "self"})
        self.assertEqual(self._sections("s"), {})

        self.assertTrue(self._sync("s"))

        self.assertIn("NoteAgent", self._sections("s")["identity"])
        self.assertEqual(
            self._versioned("s")[-1]["promptVersion"], PROMPT_VERSION
        )

    def test_a_reset_to_a_later_entry_clears_the_version(self) -> None:
        self.store.create("s")
        _skill(self.skills, "writing")
        self._sync("s")
        self.store.append(
            "s",
            {
                "id": "m1",
                "type": "message",
                "role": "user",
                "content": "go",
            },
        )
        self.store.append("s", {"type": "reset", "head": "m1"})

        self.assertEqual(self._sections("s"), {})
        self.assertTrue(self._sync("s"))
        self.assertIn("NoteAgent", self._sections("s")["identity"])

    def test_no_layout_does_nothing(self) -> None:
        self.store.create("s")

        self.assertFalse(
            sync_default_sections(
                self.store, "s", None, registry=self.registry
            )
        )

        self.assertEqual(self._sections("s"), {})


if __name__ == "__main__":
    unittest.main()
