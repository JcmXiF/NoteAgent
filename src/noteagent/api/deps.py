"""What the interface is built from.

The factory takes one of these so a test can hand it fakes without patching a
module global. Production starts with the kernel parts that exist today; the
model, sessions, waits and jobs are wired by the slices that build them, and
nothing here silently falls back to a fake.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds
from noteagent.agent.kernel import RunRegistry
from noteagent.agent.model import ModelClient
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore
from noteagent.agent.tools import ToolRegistry
from noteagent.writing.tools import WebFetchFn, WebSearchFn


@dataclass(frozen=True, slots=True)
class AppDeps:
    """The interfaces the app may cross, resolved once at composition."""

    clock: Clock = field(default_factory=SystemClock)
    ids: Ids = field(default_factory=UuidIds)
    #: ``None`` composes the project's wiki tools for each run; a test that says
    #: ``ToolRegistry()`` gets a run with no tools at all.
    tools: ToolRegistry | None = None
    runs: RunRegistry = field(default_factory=RunRegistry)
    runtime: AgentRuntime | None = None
    model: ModelClient | None = None
    sessions: SessionStore | None = None
    #: The web search the writing tools call. ``None`` uses the project's own
    #: acquire search; a test hands in a callable that returns cards.
    web_search: WebSearchFn | None = None
    #: The web fetch the page selection tool calls. ``None`` uses the project's
    #: own acquire fetch; a test hands in a callable that returns a capture.
    web_fetch: WebFetchFn | None = None
    settings: Mapping[str, object] | None = None
    #: A product session starts with the default role/tools/grounding sections.
    #: The eval harness turns this off; its cases bring their own sections.
    seed_system_sections: bool = True
    #: One safety bound for a run: model turns, not tool rounds. Spending it
    #: forces one extra answer without tools.
    max_turns: int = 50
