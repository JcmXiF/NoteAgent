"""The writing skill is disclosed by the index and loaded by tool."""

from __future__ import annotations

import unittest

from noteagent.agent.tools import ToolContext
from noteagent.skills import (
    BUNDLED_ROOT,
    SkillRegistry,
    SkillRoot,
    SkillRootKind,
)
from noteagent.skills.index import render_index
from noteagent.skills.tools import skill_tools


def _bundled() -> SkillRegistry:
    return SkillRegistry(
        (SkillRoot(SkillRootKind.BUNDLED, BUNDLED_ROOT),)
    )


class WritingSkillTests(unittest.TestCase):
    def test_the_index_discloses_the_writing_skill(self) -> None:
        text = render_index(_bundled())

        self.assertIn("- writing [bundled]:", text)
        self.assertIn("整理成一页", text)
        self.assertNotIn("## Recipe", text)

    def test_the_registry_loads_the_writing_skill(self) -> None:
        skill = _bundled().load("writing")

        self.assertIn("compose_wiki", skill.body)
        self.assertIn("write_wiki", skill.body)
        self.assertIn("cites the frame", skill.body)
        self.assertIn("stale frame", skill.body)

    def test_the_tool_loads_the_writing_skill(self) -> None:
        tools = {spec.name: spec for spec in skill_tools(_bundled())}

        result = tools["skill"].handler(
            {"name": "writing"}, ToolContext("s")
        )

        self.assertIn("compose_wiki", result.content)
        self.assertIn("Base directory:", result.content)


if __name__ == "__main__":
    unittest.main()
