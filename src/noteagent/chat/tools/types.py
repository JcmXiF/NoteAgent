"""Chat tool contracts. Results are ephemeral observations, not session turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _empty_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {}}


@dataclass(frozen=True, slots=True)
class ChatToolSpec:
    """Catalog row. ``enabled`` tools are sent to the model as OpenAI tools."""

    name: str
    description: str
    enabled: bool = True
    argument_hint: str = ""
    parameters: dict[str, Any] = field(default_factory=_empty_schema)


@dataclass(frozen=True, slots=True)
class ToolObservation:
    """One tool result, for this request only.

    Session JSON still stores only user and assistant turns. The query loop
    appends ``role=tool`` messages built from these observations.
    """

    tool: str
    title: str
    target: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    def as_event(self) -> dict[str, Any]:
        """What this observation tells the interface.

        The same result, minus the text written for the model: ``content`` is a whole
        page or a whole search result and is not what a card shows. A card shows what
        was touched and where it is, so the fields that say that are the ones sent —
        a whitelist rather than the whole ``meta``, because ``meta`` is the tools'
        own scratch space and adding a key there should not silently widen what
        leaves the core.
        """
        event: dict[str, Any] = {
            "tool": self.tool,
            "title": self.title,
            "target": self.target,
        }
        for key in EVENT_META:
            if key in self.meta:
                event[key] = self.meta[key]
        return event


#: The parts of an observation's ``meta`` an interface may see.
EVENT_META = (
    # Where it is.
    "url",
    "path",
    "slug",
    "scope",
    "query",
    "match",
    # What happened to it.
    "missing",
    "unsafe",
    "error",
    "omitted",
    "truncated",
    "not_source",
    "needs_human",
    "job",
    "fidelity",
    "http_status",
    # What a write action did, so the interface can say where to confirm rather than
    # showing the tool's own name for it.
    "action",
    "wrote_wiki",
)


def openai_tool(spec: ChatToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }
