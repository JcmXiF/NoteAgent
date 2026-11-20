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
from noteagent.task.model import FetchFn, SearchFn, StageFn
from noteagent.task.service import TaskRegistry, TaskService
from noteagent.task.writing import (
    ComposePageFn,
    PageLocks,
    SavePageFn,
)


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
    #: The writing task's service, when a test hands one in; otherwise it is
    #: built from the active project on each request.
    tasks: TaskService | None = None
    #: The pure search node behind ``web_search``. ``None`` uses the project's
    #: configured web search; a test scripts the hits and never touches a network.
    task_search: SearchFn | None = None
    #: The pure fetch and stage nodes behind ``pick_card``. ``None`` uses the
    #: project's fetch and staging area; a test scripts both and keeps the
    #: network and the filesystem out of the pick.
    task_fetch: FetchFn | None = None
    task_stage: StageFn | None = None
    #: The pure compose and save nodes behind an approved ``compose_page``.
    #: ``None`` uses the production pair (``write_confirmed_page`` then
    #: ``write_file_blocks`` + the index); a test scripts them and keeps the
    #: model and network out of the write.
    task_compose: ComposePageFn | None = None
    task_save: SavePageFn | None = None
    #: The page locks the task's save and the human editor share, so the later
    #: writer is refused instead of interleaving with the other.
    task_page_locks: PageLocks = field(default_factory=PageLocks)
    #: The task's active revision per session, shared by every request in this
    #: process, so the wrapper can name the revision the run observed.
    task_registry: TaskRegistry = field(default_factory=TaskRegistry)
    #: How the task executor is taken off the request thread. A test passes a
    #: synchronous launcher so the op finishes before the handler waits.
    task_spawn: Callable[[Callable[[], None]], None] | None = None
    #: The bounded wait a search tool gives its executor, in seconds.
    task_wait_s: float = 15.0
    #: The bounded wait a pick tool gives its executor, in seconds.
    task_pick_wait_s: float = 90.0
    #: The bounded wait an approved compose gives its executor, in seconds. The
    #: tool's registry timeout sits above it, so a slow write answers
    #: requested/running instead of a registry timeout.
    task_compose_wait_s: float = 180.0
    settings: Mapping[str, object] | None = None
    #: A product session starts with the default role/tools/grounding sections.
    #: The eval harness turns this off; its cases bring their own sections.
    seed_system_sections: bool = True
    #: One safety bound for a run: model turns, not tool rounds.
    max_turns: int = 50
