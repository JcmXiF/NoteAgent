"""Chat tools: the five actions a query may call, and the loop's dispatcher."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from noteagent.chat.limits import ChatLimits
from noteagent.chat.tools.run import (
    TOOL_ARGUMENT_HINT as RUN_ARGUMENT_HINT,
    TOOL_DESCRIPTION as RUN_DESCRIPTION,
    TOOL_NAME as RUN_NAME,
    TOOL_PARAMETERS as RUN_PARAMETERS,
    run as run_job,
)
from noteagent.chat.tools.search import (
    TOOL_ARGUMENT_HINT as SEARCH_ARGUMENT_HINT,
    TOOL_DESCRIPTION as SEARCH_DESCRIPTION,
    TOOL_NAME as SEARCH_NAME,
    TOOL_PARAMETERS as SEARCH_PARAMETERS,
    search,
)
from noteagent.chat.tools.skill import (
    TOOL_ARGUMENT_HINT as SKILL_ARGUMENT_HINT,
    TOOL_DESCRIPTION as SKILL_DESCRIPTION,
    TOOL_NAME as SKILL_NAME,
    TOOL_PARAMETERS as SKILL_PARAMETERS,
    skill,
)
from noteagent.chat.tools.types import ChatToolSpec, ToolObservation, openai_tool
from noteagent.chat.tools.wiki_read import (
    TOOL_ARGUMENT_HINT as WIKI_READ_ARGUMENT_HINT,
    TOOL_DESCRIPTION as WIKI_READ_DESCRIPTION,
    TOOL_NAME as WIKI_READ_NAME,
    TOOL_PARAMETERS as WIKI_READ_PARAMETERS,
    wiki_read,
)
from noteagent.chat.tools.write import (
    TOOL_ARGUMENT_HINT as WRITE_ARGUMENT_HINT,
    TOOL_DESCRIPTION as WRITE_DESCRIPTION,
    TOOL_NAME as WRITE_NAME,
    TOOL_PARAMETERS as WRITE_PARAMETERS,
    write,
)
from noteagent.project.layout import ProjectLayout

ToolHandler = Callable[
    [ProjectLayout, dict[str, Any], ChatLimits], tuple[ToolObservation, ...]
]


def _wiki_read_handler(
    layout: ProjectLayout, arguments: dict[str, Any], limits: ChatLimits
) -> tuple[ToolObservation, ...]:
    raw = arguments.get("targets", [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("wiki_read targets must be a list")
    targets: Sequence[str] = [str(item) for item in raw if str(item).strip()]
    if not targets:
        raise ValueError("wiki_read requires at least one target")
    return wiki_read(layout, targets, limits)


_HANDLERS: dict[str, ToolHandler] = {
    WIKI_READ_NAME: _wiki_read_handler,
    SKILL_NAME: skill,
    SEARCH_NAME: search,
    WRITE_NAME: write,
    RUN_NAME: run_job,
}


def tool_catalog() -> tuple[ChatToolSpec, ...]:
    return (
        ChatToolSpec(
            name=WIKI_READ_NAME,
            description=WIKI_READ_DESCRIPTION,
            enabled=True,
            argument_hint=WIKI_READ_ARGUMENT_HINT,
            parameters=WIKI_READ_PARAMETERS,
        ),
        ChatToolSpec(
            name=SKILL_NAME,
            description=SKILL_DESCRIPTION,
            enabled=True,
            argument_hint=SKILL_ARGUMENT_HINT,
            parameters=SKILL_PARAMETERS,
        ),
        ChatToolSpec(
            name=SEARCH_NAME,
            description=SEARCH_DESCRIPTION,
            enabled=True,
            argument_hint=SEARCH_ARGUMENT_HINT,
            parameters=SEARCH_PARAMETERS,
        ),
        ChatToolSpec(
            name=WRITE_NAME,
            description=WRITE_DESCRIPTION,
            enabled=True,
            argument_hint=WRITE_ARGUMENT_HINT,
            parameters=WRITE_PARAMETERS,
        ),
        ChatToolSpec(
            name=RUN_NAME,
            description=RUN_DESCRIPTION,
            enabled=True,
            argument_hint=RUN_ARGUMENT_HINT,
            parameters=RUN_PARAMETERS,
        ),
    )


def enabled_tools() -> tuple[ChatToolSpec, ...]:
    return tuple(spec for spec in tool_catalog() if spec.enabled)


def openai_tools() -> list[dict[str, Any]]:
    return [openai_tool(spec) for spec in enabled_tools()]


def run_tool(
    name: str,
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    spec = {item.name: item for item in tool_catalog()}.get(name)
    if spec is None:
        raise ValueError(f"unknown chat tool: {name}")
    if not spec.enabled:
        raise ValueError(f"chat tool is disabled: {name}")
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"chat tool has no handler: {name}")
    return handler(layout, arguments, limits)


def observe_tool(
    name: str,
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    """Like ``run_tool``, but unknown/disabled/bad args become observations."""
    try:
        return run_tool(name, layout, arguments, limits)
    except ValueError as err:
        return (
            ToolObservation(
                tool=name,
                title="error",
                target=name,
                content=str(err),
                meta={"error": True},
            ),
        )


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
