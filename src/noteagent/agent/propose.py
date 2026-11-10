"""The propose control action: the model asks a human to enter a job.

It travels as a function call because that is the channel a model can write
reliably, but it is not a tool: the runtime intercepts it, records a wait, and
ends the turn. The job starts in the interface that owns it, never here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from noteagent.agent.jobs import JobInterface, JobKind
from noteagent.agent.tool_schema import ToolArgumentError, validate_arguments
from noteagent.agent.wait import JOB_INTERFACES, JOB_KINDS, JOB_SOURCES

PROPOSE_NAME = "propose"

PROPOSE_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "job": {"type": "string", "enum": list(JOB_KINDS)},
        "interface": {
            "type": "string",
            "enum": list(JOB_INTERFACES),
        },
        "intent": {"type": "string", "maxLength": 2_000},
        "reason": {"type": "string", "maxLength": 2_000},
    },
    "required": ["job", "interface"],
    "additionalProperties": False,
}

#: The function list entry the model sees beside the tools.
PROPOSE_TOOL: dict[str, object] = {
    "type": "function",
    "function": {
        "name": PROPOSE_NAME,
        "description": (
            "Propose a collaboration node. The human enters it in the original "
            "interface; the job starts there, not here. Propose alone: never "
            "put this call in the same batch as another call."
        ),
        "parameters": PROPOSE_PARAMETERS,
    },
}

@dataclass(frozen=True, slots=True)
class Proposal:
    """A checked proposal, before the runtime turns it into a wait."""

    job: JobKind
    interface: JobInterface
    intent: str = ""
    reason: str = ""


class ProposeError(ValueError):
    """The model's proposal cannot be recorded as written."""


def parse_proposal(arguments: str | Mapping[str, object]) -> Proposal:
    """Read one propose call, or say why it cannot be a wait."""
    values = _arguments(arguments)
    try:
        validate_arguments(PROPOSE_PARAMETERS, values)
    except ToolArgumentError as err:
        raise ProposeError(str(err)) from err
    job = values["job"]
    interface = values["interface"]
    assert isinstance(job, str) and isinstance(interface, str)
    allowed = JOB_SOURCES.get(job, ())
    if interface not in allowed:
        where = " or ".join(allowed)
        raise ProposeError(f"a {job} starts from the {where}")
    return Proposal(
        job=job,  # type: ignore[arg-type]
        interface=interface,  # type: ignore[arg-type]
        intent=_text(values.get("intent")),
        reason=_text(values.get("reason")),
    )


def _arguments(arguments: str | Mapping[str, object]) -> dict[str, object]:
    if isinstance(arguments, Mapping):
        return dict(arguments)
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError as err:
        raise ProposeError(f"invalid JSON: {err.msg}") from err
    if not isinstance(value, Mapping):
        raise ProposeError("arguments must be an object")
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
