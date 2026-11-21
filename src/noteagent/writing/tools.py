"""The writing state as an agent tool: one perception call, no content copy."""

from __future__ import annotations

import json

from noteagent.agent.tools import OutputLimits, ToolResult, ToolSpec
from noteagent.project.layout import ProjectLayout
from noteagent.writing.state import WritingStateStore, state_summary

GET_STATE = "get_state"

_DESCRIPTION = (
    "Read this project's writing state. With no arguments it returns the summary: "
    "stateVersion, the current writer, the counts of searches, selected pages, "
    "frames and the draft, and attention. The state is the only source of truth "
    "for those four sections; read it before acting on them."
)


def get_state_tool(layout: ProjectLayout) -> ToolSpec:
    """The one perception tool the writing workflow starts from."""

    def get_state(_arguments, _context) -> ToolResult:
        summary = state_summary(WritingStateStore(layout).load())
        return ToolResult(
            content=json.dumps(summary, ensure_ascii=False),
            meta={"title": "writing state", "target": "state"},
        )

    return ToolSpec(
        name=GET_STATE,
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        side_effect="read",
        timeout_s=5.0,
        output_limits=OutputLimits(
            max_items=1,
            max_chars_per_item=4_000,
            max_chars_total=4_000,
        ),
        handler=get_state,
        argument_hint="",
    )


__all__ = ["GET_STATE", "get_state_tool"]
