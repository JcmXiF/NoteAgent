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
from noteagent.agent.jobs import JobClient
from noteagent.agent.kernel import RunRegistry
from noteagent.agent.model import ModelClient
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import ToolRegistry


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
    waits: WaitStore | None = None
    jobs: JobClient | None = None
    settings: Mapping[str, object] | None = None
