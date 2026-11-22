"""Opening a session for the versioned routes, with one set of refusals.

The read view, the query and the cancel all need the same store, the same answer
when the id is bad or the session is gone, and the same answer to "is it
running?". Keeping that here means the stream cannot start on a session the read
would have refused, and a cancel reaches the same registry the query claimed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from noteagent.agent.kernel import KernelRuntime, RunRegistry
from noteagent.agent.model import ModelClient
from noteagent.agent.projection import project
from noteagent.agent.retry import RetryingModel
from noteagent.agent.compaction import (
    DEFAULT_CONTEXT_WINDOW,
    CompactionLimits,
)
from noteagent.agent.runtime import (
    AgentRuntime,
    EventSink,
    RunRequest,
    RunResult,
)
from noteagent.agent.run_marker import (
    RunFingerprint,
    config_digest,
    sections_digest,
)
from noteagent.agent.store import SessionStore
from noteagent.agent.tools import ToolRegistry, toolset_name
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.llm.config import LlmConfigError, load_llm_config
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.pipeline.lock import ProjectBusy
from noteagent.project.run_fingerprint import project_fingerprint
from noteagent.rag.status import STATUS_UNAVAILABLE, IndexStatus
from noteagent.sessions.jsonl import JsonlSessionStore, SessionFormatError
from noteagent.sessions.legacy import MigrationReport
from noteagent.sessions.recovery import interrupted_run, recover_store
from noteagent.sessions.trace import SessionTrace
from noteagent.skills import default_registry
from noteagent.skills.tools import skill_tools
from noteagent.util.refusal import Refusal
from noteagent.wiki.agent_tools import wiki_tools
from noteagent.writing.state import WritingStateStore
from noteagent.writing.tools import (
    compose_wiki_tool,
    get_state_tool,
    project_fetch,
    project_web_search,
    select_frame_tool,
    select_page_tool,
    web_search_tool,
    write_wiki_tool,
)

if TYPE_CHECKING:
    from noteagent.rag.settings import RagSettings

logger = logging.getLogger(__name__)


def active_layout(deps: AppDeps) -> ProjectLayout | None:
    """The project the default store reads, when no store was injected."""
    if deps.sessions is not None:
        return None
    try:
        return get_active_layout()
    except ProjectNotConfiguredError as err:
        raise CodedHTTPException(
            409, str(err), code="project.notConfigured"
        ) from err


def default_session_store(deps: AppDeps, layout: ProjectLayout) -> SessionStore:
    """The project's own store, with the clock and ids the app was built with."""
    return JsonlSessionStore(layout, clock=deps.clock, ids=deps.ids)


def _active_project_store(deps: AppDeps) -> SessionStore | None:
    """The active project's store, or ``None`` when there is nothing to open.

    A core that has no project yet, or cannot read the pointer, is a normal
    start; the route that needs the project reports the reason later.
    """
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError:
        return None
    except (OSError, ValueError) as err:
        logger.warning("the active project cannot be read at startup: %s", err)
        return None
    return default_session_store(deps, layout)


def project_sessions(deps: AppDeps) -> SessionStore:
    """The store of the active project, or the one a test injected."""
    if deps.sessions is not None:
        return deps.sessions
    layout = active_layout(deps)
    assert layout is not None
    return default_session_store(deps, layout)


def migrate_sessions(store: SessionStore) -> MigrationReport | None:
    """Run the old-chat migration for a real store; none for a test fake."""
    if isinstance(store, JsonlSessionStore):
        return store.ensure_migrated()
    return None


def migrate_active_project_chats(deps: AppDeps) -> None:
    """Bring an upgraded project's old chats across as the core starts.

    The list and open routes migrate lazily too, but an upgrade should not wait
    for somebody to open a conversation before the old ones exist. A test that
    injected its own store owns its migration, and a core with no project yet
    has nothing to migrate: both are normal starts, not failures.
    """
    if deps.sessions is not None:
        return
    store = _active_project_store(deps)
    if store is None:
        return
    try:
        migrate_sessions(store)
    except OSError as err:
        # A project the process cannot read is reported when a route needs it;
        # it must not keep the core from starting.
        logger.warning("old chats cannot be migrated at startup: %s", err)


def recover_active_project_runs(deps: AppDeps) -> None:
    """Mark the runs a previous process left running, before the socket answers.

    The scan is best effort: a project that cannot be read is reported when a
    route needs it, and a session that cannot be parsed is skipped. It never
    starts a run; a person chooses resume or abandon later.
    """
    if deps.sessions is not None:
        # A test that injected its own store owns its recovery, the same way it
        # owns its migration; production reads the active project's sessions.
        return
    store = _active_project_store(deps)
    if store is None:
        return
    try:
        recover_store(store, clock=deps.clock, mark=True)
    except Exception:
        logger.exception("the interrupted-run scan failed")


def open_session_store(
    deps: AppDeps,
    session_id: str,
    *,
    layout: ProjectLayout | None = None,
) -> SessionStore:
    """The store holding *session_id*, after proving the session can be read.

    *layout* is the one the caller already resolved, so a query does not read
    the active-project pointer twice.
    """
    store = deps.sessions
    if store is None:
        resolved = layout or active_layout(deps)
        assert resolved is not None
        store = default_session_store(deps, resolved)
    if isinstance(store, JsonlSessionStore):
        # A conversation opened by its old id may not have been through a list
        # first; the marker makes this a file-existence check on later calls.
        store.ensure_migrated()
    try:
        store.load(session_id)
    except SessionFormatError as err:
        raise CodedHTTPException(
            422, str(err), code="session.invalid", params={"id": session_id}
        ) from err
    except FileNotFoundError as err:
        raise CodedHTTPException(
            404, str(err), code="session.notFound", params={"id": session_id}
        ) from err
    except ValueError as err:
        raise CodedHTTPException(
            400, str(err), code="session.invalidId", params={"id": session_id}
        ) from err
    return store


def session_tools(deps: AppDeps, layout: ProjectLayout | None) -> ToolRegistry:
    """The tools a kernel run offers: the injected set, or the project's wiki.

    The catalog is built per run because the active project can change while the
    server keeps running; a registry captured at startup would read the wrong
    wiki after the reader switched projects.
    """
    if deps.tools is not None:
        return deps.tools
    if layout is None:
        # A test that injected its own store and no tools asked for a bare run.
        return ToolRegistry()
    tools = wiki_tools(layout, rag=rag_backend(layout))
    tools.register(get_state_tool(layout))
    tools.register(
        web_search_tool(
            layout,
            deps.web_search or project_web_search(layout),
            clock=deps.clock,
            ids=deps.ids,
        )
    )
    tools.register(
        select_page_tool(
            layout,
            deps.web_fetch or project_fetch(layout),
            clock=deps.clock,
            ids=deps.ids,
        )
    )
    tools.register(
        select_frame_tool(layout, clock=deps.clock, ids=deps.ids)
    )
    tools.register(compose_wiki_tool(layout, clock=deps.clock))
    tools.register(write_wiki_tool(layout, clock=deps.clock))
    for tool in skill_tools(default_registry(layout.root)):
        tools.register(tool)
    return tools


class UnavailableRag:
    """A backend that only knows why it cannot answer."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    @property
    def reason(self) -> str:
        return self._reason

    def search(self, query: str):
        from noteagent.rag.result import RagSearchResult

        return RagSearchResult(
            (), "lexical", True, (self._reason,)
        )

    def status(self) -> IndexStatus:
        """The settings screen's answer: unavailable, with the reason."""
        return IndexStatus(STATUS_UNAVAILABLE, reason=self._reason)

    def rebuild(self) -> None:
        """Refuse a rebuild: no usable backend exists to rebuild."""
        raise Refusal(
            "settings.ragUnavailable", self._reason, status=409
        )


def rag_backend(
    layout: ProjectLayout, settings: "RagSettings | None" = None
):
    """The cached hybrid backend, or a degraded one when it cannot be built.

    Imported lazily so a run with RAG off never loads the index engine.
    ``settings`` is the already-resolved switch and services when the caller
    has read them; the answer is the same either way.
    """
    try:
        if settings is None:
            from noteagent.rag.settings import load_rag_settings

            settings = load_rag_settings()
    except Exception as err:  # noqa: BLE001 - a bad config must not stop chat
        return UnavailableRag(f"rag settings: {err}")
    if not settings.enabled:
        return None
    try:
        from noteagent.rag.search import get_backend

        backend = get_backend(layout, settings)
    except Exception as err:  # noqa: BLE001 - a bad config must not stop chat
        return UnavailableRag(f"rag unavailable: {err}")
    if backend is None:
        return UnavailableRag(
            "rag unavailable: configure the embedding service"
        )
    return backend


class WritingLeaseRuntime:
    """A kernel run releases the writing state's lease when it ends.

    The lease is the state's ``writer`` field, not the project lock: the run's
    own tools take and release that lock for every state write, so the run
    cannot hold it for its whole length. The first writing tool takes the lease
    (a run that never writes the state creates nothing); this wrapper releases
    it at the end. A person's save sees the lease and refuses; a crashed run's
    lease goes stale after ``WRITER_TTL_S``.
    """

    def __init__(
        self, inner: AgentRuntime, store: WritingStateStore
    ) -> None:
        self._inner = inner
        self._store = store

    def execute(self, request: RunRequest, sink: EventSink) -> RunResult:
        session_id = str(getattr(request, "session_id", "") or "")
        try:
            return self._inner.execute(request, sink)
        finally:
            try:
                self._store.release_writer(session_id)
            except ProjectBusy:
                # A holder we cannot reach leaves only the stale lease behind.
                pass

    def cancel(self, session_id: str) -> None:
        self._inner.cancel(session_id)

    def is_busy(self, session_id: str) -> bool:
        return self._inner.is_busy(session_id)

    def compact(self, session_id: str, instructions: str = "") -> bool:
        return self._inner.compact(session_id, instructions)


def kernel_model(deps: AppDeps) -> tuple[ModelClient, str]:
    """The model a kernel run uses, and the name its marker records."""
    model_name = str((deps.settings or {}).get("model") or "")
    model = deps.model
    if model is None:
        config = load_llm_config()
        model = RetryingModel(OpenAIModelClient(config))
        model_name = model_name or config.model
    return model, model_name


def _context_window() -> int:
    """The configured window, or the default when no endpoint is configured."""
    try:
        return load_llm_config(require_api_key=False).context_window
    except LlmConfigError:
        return DEFAULT_CONTEXT_WINDOW


def kernel_runtime(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
    tools: ToolRegistry,
) -> KernelRuntime:
    """A kernel with the model, log, registry and trace every run shares."""
    model, model_name = kernel_model(deps)
    fingerprint = (
        project_fingerprint(layout, config=deps.settings or {})
        if layout is not None
        else {}
    )
    return KernelRuntime(
        model=model,
        sessions=store,
        tools=tools,
        clock=deps.clock,
        ids=deps.ids,
        registry=deps.runs,
        model_name=model_name,
        max_turns=deps.max_turns,
        compaction=CompactionLimits(
            context_tokens=_context_window()
        ),
        # A run against a real project writes a content-free trace beside its
        # session; a test store with no project has nowhere to put one.
        trace=SessionTrace(layout, clock=deps.clock)
        if layout is not None
        else None,
        # Non-secret digests a resume can compare; the kernel only records them.
        fingerprint=fingerprint,
    )


def session_runtime(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
) -> AgentRuntime:
    """The runtime a run crosses: the injected one, or the project's kernel.

    A query and its crash resume must be the same kind of run, so they are
    built in one place. The registry is shared, which keeps a resumed run from
    starting a second loop beside a person's question.
    """
    if deps.runtime is not None:
        return deps.runtime
    runtime: AgentRuntime = kernel_runtime(
        deps, store, layout, session_tools(deps, layout)
    )
    if layout is not None:
        runtime = WritingLeaseRuntime(
            runtime, WritingStateStore(layout, clock=deps.clock)
        )
    return runtime


def _session_model_name(deps: AppDeps) -> str:
    """The model name a kernel run records, resolved as ``session_runtime`` does."""
    return kernel_model(deps)[1]


def resume_refusal(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
    session_id: str,
    run_id: str,
    *,
    tools: ToolRegistry | None = None,
) -> str:
    """Why this interrupted run cannot continue here, or an empty string.

    The comparison is the kernel's fingerprint check, built from the same
    environment; it stays a route preflight instead of a sixth runtime call.
    """
    marker = interrupted_run(store, session_id, run_id)
    if marker is None:
        return ""
    entries = store.load(session_id).entries
    projection = project(entries)
    fingerprint = (
        project_fingerprint(layout, config=deps.settings or {})
        if layout is not None
        else {}
    )
    current = RunFingerprint.from_mapping(
        fingerprint,
        model=_session_model_name(deps),
        toolset=toolset_name(
            tools if tools is not None else session_tools(deps, layout)
        ),
        config_hash=config_digest(
            fingerprint.get("configHash", ""), projection.sections
        ),
    )
    return marker.fingerprint.refusal(
        current, legacy_config=sections_digest(projection.sections)
    )


def _run_owner(deps: AppDeps) -> AgentRuntime | RunRegistry:
    """Whoever holds the runs: one carried runtime, or the shared registry.

    The read view, the query and the cancel must not disagree about who owns
    the session, so they all ask this one question.
    """
    return deps.runtime if deps.runtime is not None else deps.runs


def session_busy(deps: AppDeps, session_id: str) -> bool:
    """Whether the session has a run in progress."""
    return _run_owner(deps).is_busy(session_id)


def cancel_session(deps: AppDeps, session_id: str) -> bool:
    """Stop the session's run; say whether there was one to stop."""
    owner = _run_owner(deps)
    busy = owner.is_busy(session_id)
    if busy:
        owner.cancel(session_id)
    return busy


#: The wait routes and the job gate are gone; the old
#: wait entries stay readable in the transcript.
