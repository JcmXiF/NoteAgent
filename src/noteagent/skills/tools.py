"""The two read-only skill tools: load a body, read a referenced file.

Both tools are read effects with no network and no disk write. A refusal is a
result the model can correct from, not an exception: the content says what was
wrong and, for an unknown name, what is available.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from noteagent.agent.tools import (
    OutputLimits,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from noteagent.skills import (
    MAX_REFERENCE_BYTES,
    MAX_SKILL_BYTES,
    Skill,
    SkillError,
    SkillNotFoundError,
    SkillRegistry,
    read_skill_file,
)

SKILL = "skill"
SKILL_READ_FILE = "skill_read_file"

_SKILL_DESCRIPTION = (
    "Load one skill's instructions by name. Call it when an available skill "
    "matches the request; the result includes the skill's base directory for "
    "referenced files."
)
_READ_DESCRIPTION = (
    "Read one file a loaded skill references, relative to its base directory "
    "(for example references/example.md). Read-only; stays inside the skill "
    "folder."
)

_NAME_PROPERTY = {
    "type": "string",
    "minLength": 1,
    "pattern": r"\S",
    "description": "Skill name from the available skills list",
}

_SKILL_PARAMETERS = {
    "type": "object",
    "properties": {"name": _NAME_PROPERTY},
    "required": ["name"],
    "additionalProperties": False,
}

_READ_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": _NAME_PROPERTY,
        "path": {
            "type": "string",
            "minLength": 1,
            "pattern": r"\S",
            "description": "File path relative to the skill's base directory",
        },
    },
    "required": ["name", "path"],
    "additionalProperties": False,
}


def skill_tools(registry: SkillRegistry) -> tuple[ToolSpec, ...]:
    """The two tools, bound to one registry for the run."""
    return (
        ToolSpec(
            name=SKILL,
            description=_SKILL_DESCRIPTION,
            parameters=_SKILL_PARAMETERS,
            side_effect="read",
            timeout_s=10.0,
            output_limits=OutputLimits(
                max_items=1,
                max_chars_per_item=MAX_SKILL_BYTES,
                max_chars_total=MAX_SKILL_BYTES,
            ),
            handler=_load_handler(registry),
            argument_hint="name",
        ),
        ToolSpec(
            name=SKILL_READ_FILE,
            description=_READ_DESCRIPTION,
            parameters=_READ_PARAMETERS,
            side_effect="read",
            timeout_s=10.0,
            output_limits=OutputLimits(
                max_items=1,
                max_chars_per_item=MAX_REFERENCE_BYTES,
                max_chars_total=MAX_REFERENCE_BYTES,
            ),
            handler=_read_handler(registry),
            argument_hint="path",
        ),
    )


def _resolve(
    registry: SkillRegistry, arguments: Mapping[str, object]
) -> tuple[Skill | None, ToolResult | None]:
    """The named skill, or the refusal the model should read instead."""
    name = str(arguments.get("name") or "").strip()
    try:
        return registry.load(name), None
    except SkillNotFoundError:
        return None, _unknown(registry, name)


def _unknown(registry: SkillRegistry, name: str) -> ToolResult:
    available = ", ".join(
        entry.name for entry in registry.catalog()
    ) or "none"
    return ToolResult(
        content=f"no skill named {name!r}; available skills: {available}",
        meta={"target": name, "missing": True},
    )


def _meta(
    skill: Skill,
    target: str,
    *,
    title: str | None = None,
    path: Path | None = None,
) -> dict[str, object]:
    return {
        "title": title or f"skill {skill.name}",
        "target": target,
        "path": str(path if path is not None else skill.path.parent),
    }


def _load_handler(registry: SkillRegistry):
    def load(
        arguments: Mapping[str, object], _context: ToolContext
    ) -> ToolResult:
        skill, failure = _resolve(registry, arguments)
        if failure is not None:
            return failure
        return ToolResult(
            content=(
                f"Skill {skill.name}: {skill.description}\n"
                f"Base directory: {skill.path.parent}\n\n"
                f"{skill.body}"
            ),
            meta=_meta(skill, skill.name),
        )

    return load


def _read_handler(registry: SkillRegistry):
    def read(
        arguments: Mapping[str, object], _context: ToolContext
    ) -> ToolResult:
        skill, failure = _resolve(registry, arguments)
        if failure is not None:
            return failure
        relative = str(arguments.get("path") or "").strip()
        try:
            text = read_skill_file(skill, relative)
        except SkillError as err:
            return ToolResult(
                content=(
                    f"cannot read {relative!r} from skill "
                    f"{skill.name!r}: {err}"
                ),
                meta=_meta(skill, relative),
            )
        return ToolResult(
            content=text,
            meta=_meta(
                skill,
                relative,
                title=f"{skill.name}/{relative}",
                path=skill.path.parent / relative,
            ),
        )

    return read
