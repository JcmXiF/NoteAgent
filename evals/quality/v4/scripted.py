"""The small helpers both atom sets script a model with.

A scripted atom is a list of model events; these turn one tool call or one
final answer into that list, and read a tool's answer back out of a run.
"""

from __future__ import annotations

import json
from typing import Any

from noteagent.agent.faux import FauxModelClient
from noteagent.agent.model import ModelEvent


def call_script(
    call_id: str, name: str, arguments: dict[str, object]
) -> list[ModelEvent]:
    """One scripted tool call."""
    return FauxModelClient.tool_call(
        call_id, name, json.dumps(arguments, ensure_ascii=False)
    ).script


def final_script(text: str) -> list[ModelEvent]:
    """One scripted final answer."""
    return FauxModelClient.text(text).script


def tool_results(api, session: str, name: str) -> list[dict[str, Any]]:
    """Every result one tool returned in a session, in call order."""
    return [
        entry
        for entry in api.entries(session)
        if entry.get("role") == "toolResult"
        and entry.get("name") == name
    ]


def as_json(content: object) -> dict[str, Any]:
    """A tool answer that is JSON, or an empty mapping when it is not."""
    try:
        payload = json.loads(str(content or ""))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = ["as_json", "call_script", "final_script", "tool_results"]
