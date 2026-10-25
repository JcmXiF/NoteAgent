"""skill: load one SKILL.md body. Does not run other tools."""

from __future__ import annotations

from typing import Any

from noteagent.acquire.skills import SkillNotFoundError
from noteagent.chat.limits import ChatLimits
from noteagent.chat.skills import load_skill
from noteagent.chat.tools.types import ToolObservation
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import truncate

TOOL_NAME = "skill"
TOOL_DESCRIPTION = (
    "Load the full text of one skill by name from the catalog. "
    "Skills are instructions; they do not execute tools."
)
TOOL_ARGUMENT_HINT = "name: skill id from the catalog"
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Skill name, e.g. query-wiki",
        }
    },
    "required": ["name"],
}


def skill(
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    del layout
    name = str(arguments.get("name") or "").strip()
    if not name:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title="skill",
                target="",
                content="skill name is empty",
                meta={"missing": True},
            ),
        )
    try:
        loaded = load_skill(name)
    except SkillNotFoundError:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title=name,
                target=name,
                content=f"unknown skill: {name}",
                meta={"missing": True},
            ),
        )
    body = truncate(loaded.body, limits.max_page_chars)
    return (
        ToolObservation(
            tool=TOOL_NAME,
            title=loaded.name,
            target=loaded.name,
            content=body,
            meta={
                "description": loaded.description,
                "truncated": body != loaded.body,
            },
        ),
    )
