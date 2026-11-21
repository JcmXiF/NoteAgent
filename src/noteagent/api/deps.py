"""What the interface is built from.

The factory takes one of these so a test can hand it fakes without patching a
module global. Production starts with the kernel parts that exist today; the
model, sessions, waits and jobs are wired by the slices that build them, and
nothing here silently falls back to a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from noteagent.agent.clock import Clock, Ids, SystemClock, UuidIds
from noteagent.agent.jobs import JobClient, JobGate
from noteagent.agent.kernel import RunRegistry
from noteagent.agent.model import ModelClient
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import ToolRegistry
from noteagent.pipeline.frame_model import (
    DEFAULT_CHARS as DEFAULT_FRAME_CHARS,
    DEFAULT_ROUNDS as DEFAULT_FRAME_ROUNDS,
    ModelFraming,
)
from noteagent.pipeline.confirm import CompileFn
from noteagent.pipeline.materials import DEFAULT_KEEP_RUNS
from noteagent.pipeline.planning import PlanFn
from noteagent.pipeline.service import PipelineRegistry, PipelineService
from noteagent.pipeline.stage import PipelineStage
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
    waits: WaitStore | None = None
    jobs: JobClient | None = None
    #: One collaboration job at a time, across every session.
    job_gate: JobGate = field(default_factory=JobGate)
    #: The project's pipeline, when a test hands one in; otherwise it is built
    #: from the active project on each request.
    pipeline: PipelineService | None = None
    #: The stages a run walks. ``None`` composes the production stages; an
    #: explicit empty sequence is a run that does nothing (a test seam).
    pipeline_stages: Sequence[PipelineStage] | None = None
    #: The active run of each project, shared by every request in this process.
    pipeline_registry: PipelineRegistry = field(default_factory=PipelineRegistry)
    #: How a run is taken off the request thread. A test passes a synchronous
    #: launcher so a whole run finishes before the response is read.
    pipeline_spawn: Callable[[Callable[[], None]], None] | None = None
    #: How many runs of un-promoted staging are kept before the oldest is pruned.
    pipeline_keep_runs: int = DEFAULT_KEEP_RUNS
    #: The bounded model loop that frames a material; ``None`` in a test that
    #: wants the deterministic split, the production model in a real core.
    pipeline_frame_model: ModelFraming | None = None
    #: The disclosure budget a production frame model is built with.
    pipeline_frame_rounds: int = DEFAULT_FRAME_ROUNDS
    pipeline_frame_chars: int = DEFAULT_FRAME_CHARS
    #: The planner a run calls. A test that wires its own stages hands in a
    #: scripted planner; ``None`` leaves the plan stage unwired.
    pipeline_planner: PlanFn | None = None
    #: What an approved run calls to write its pages. ``None`` compiles through
    #: the existing write path; a test scripts it and inspects what it was given.
    pipeline_compile: CompileFn | None = None
    #: The web search the writing tools call. ``None`` uses the project's own
    #: acquire search; a test hands in a callable that returns cards.
    web_search: WebSearchFn | None = None
    #: The web fetch the page selection tool calls. ``None`` uses the project's
    #: own acquire fetch; a test hands in a callable that returns a capture.
    web_fetch: WebFetchFn | None = None
    #: The writing skill as a context source: ``None`` follows the message's
    #: intent, ``True``/``False`` force it on or off (the A/B experiment).
    writing_skill: bool | None = None
    settings: Mapping[str, object] | None = None
    #: A product session starts with the default role/tools/grounding sections.
    #: The eval harness turns this off; its cases bring their own sections.
    seed_system_sections: bool = True
    #: One safety bound for a run: model turns, not tool rounds.
    max_turns: int = 50
