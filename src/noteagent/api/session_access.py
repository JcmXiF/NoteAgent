"""Opening a session for the versioned routes, with one set of refusals.

The read view, the query and the cancel all need the same store, the same answer
when the id is bad or the session is gone, and the same answer to "is it
running?". Keeping that here means the stream cannot start on a session the read
would have refused, and a cancel reaches the same registry the query claimed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from noteagent.agent.jobs import JobBusyError
from noteagent.agent.kernel import KernelRuntime, RunRegistry
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import ToolRegistry
from noteagent.agent.wait import wait_payload
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.llm.config import load_llm_config
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore, SessionFormatError
from noteagent.sessions.legacy import MigrationReport
from noteagent.sessions.trace import SessionTrace
from noteagent.wiki.agent_tools import wiki_tools


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


def project_sessions(deps: AppDeps) -> SessionStore:
    """The store of the active project, or the one a test injected."""
    if deps.sessions is not None:
        return deps.sessions
    layout = active_layout(deps)
    assert layout is not None
    return JsonlSessionStore(layout, clock=deps.clock, ids=deps.ids)


def migrate_sessions(store: SessionStore) -> MigrationReport | None:
    """Run the old-chat migration for a real store; none for a test fake."""
    if isinstance(store, JsonlSessionStore):
        return store.ensure_migrated()
    return None


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
        store = JsonlSessionStore(resolved, clock=deps.clock, ids=deps.ids)
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
    return wiki_tools(layout)


def session_runtime(
    deps: AppDeps,
    store: SessionStore,
    layout: ProjectLayout | None,
) -> AgentRuntime:
    """The runtime a run crosses: the injected one, or the project's kernel.

    A query and the resume that follows a job must be the same kind of run, so
    they are built in one place. The registry is shared, which is what keeps a
    job's continuation from starting a second loop beside a person's question.
    """
    if deps.runtime is not None:
        return deps.runtime
    model = deps.model
    if model is None:
        model = OpenAIModelClient(load_llm_config())
    return KernelRuntime(
        model=model,
        sessions=store,
        tools=session_tools(deps, layout),
        waits=session_waits(deps, store),
        clock=deps.clock,
        ids=deps.ids,
        registry=deps.runs,
        # A run against a real project writes a content-free trace beside its
        # session; a test store with no project has nowhere to put one.
        trace=SessionTrace(layout, clock=deps.clock) if layout is not None else None,
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


def session_waits(deps: AppDeps, store: SessionStore) -> WaitStore:
    """The waits a route reads: the injected set, or the session's own log."""
    return deps.waits if deps.waits is not None else SessionWaitStore(store)


@dataclass(frozen=True, slots=True)
class JobWait:
    """A job the chat proposed, with the session that records its lifecycle."""

    store: SessionStore
    waits: WaitStore
    session_id: str
    wait_id: str


#: Two presses of the same original button can arrive together. The check and
#: the entry are one step, so only one job id is ever handed out.
_ENTER_LOCK = threading.Lock()


def check_wait_for_job(job_wait: JobWait, job: str) -> None:
    """Refuse a job that the named wait does not allow.

    Split from the entry so an endpoint can answer "this wait is wrong" before
    it answers "the model is not configured", and can validate without writing
    an entry it may not be able to follow through.
    """
    wait = job_wait.waits.current(job_wait.session_id)
    if wait is None or wait.wait_id != job_wait.wait_id:
        raise CodedHTTPException(
            404,
            (
                f"wait {job_wait.wait_id} is not the current wait of "
                f"session {job_wait.session_id}"
            ),
            code="wait.notFound",
            params={"wait": job_wait.wait_id},
        )
    if wait.job != job:
        raise CodedHTTPException(
            409,
            f"wait {job_wait.wait_id} is for {wait.job}, not {job}",
            code="wait.wrongJob",
            params={"wait": job_wait.wait_id, "job": wait.job},
        )
    if wait.status != "proposed":
        raise CodedHTTPException(
            409,
            f"wait {job_wait.wait_id} is already {wait.status}",
            code="wait.alreadyStarted",
            params={"wait": job_wait.wait_id, "status": wait.status},
        )


def job_wait_for_body(
    deps: AppDeps,
    layout: ProjectLayout | None,
    session_id: str,
    wait_id: str,
    job: str,
) -> JobWait | None:
    """The wait a job endpoint's body names, after checking it may be entered.

    Both ids travel together: a wait without its session cannot be found, and a
    session without a wait is not a proposal. A wrong wait is answered before
    the model configuration, and nothing is written until the job can start.
    """
    if not (session_id or wait_id):
        return None
    if not (session_id and wait_id):
        raise CodedHTTPException(
            400,
            "a waited-on job needs both its session and its wait",
            code="wait.contextIncomplete",
        )
    store = open_session_store(deps, session_id, layout=layout)
    job_wait = JobWait(
        store,
        session_waits(deps, store),
        session_id,
        wait_id,
    )
    check_wait_for_job(job_wait, job)
    return job_wait


def claim_job(deps: AppDeps) -> str:
    """Take the one collaboration-job slot, or refuse with ``job.busy``.

    A direct plan or compile press is a collaboration job too: it writes the
    same project, so it must wait for a job the chat already entered.
    """
    job_id = deps.ids.new_id("job")
    try:
        deps.job_gate.claim(job_id)
    except JobBusyError as err:
        raise CodedHTTPException(409, str(err), code="job.busy") from err
    return job_id


def enter_wait_for_job(deps: AppDeps, job_wait: JobWait, job: str) -> str:
    """Record that the human started the waited-on job, once.

    The job id is minted here so reconciliation can ask the job system what
    happened when a completion event was lost. A wait that is not proposed is
    refused: the same button must not start a second job.
    """
    with _ENTER_LOCK:
        check_wait_for_job(job_wait, job)
        # The wait stays proposed when the slot is taken: the reader can press
        # again once the other collaboration job has finished.
        job_id = claim_job(deps)
        try:
            job_wait.waits.enter(job_wait.session_id, job_wait.wait_id, job_id)
        except Exception:
            deps.job_gate.release(job_id)
            raise
        return job_id


def cancel_wait(
    waits: WaitStore, session_id: str, wait_id: str
) -> dict[str, object]:
    """Stop a proposed wait, and say what the wait is now.

    Only ``proposed`` can be stopped here. Once the human has entered the node,
    the job belongs to its own interface, and cancelling it in the chat would
    leave that interface running something the conversation thinks is over.
    """
    wait = waits.current(session_id)
    if wait is None or wait.wait_id != wait_id:
        raise CodedHTTPException(
            404,
            f"wait {wait_id} is not the current wait of session {session_id}",
            code="wait.notFound",
            params={"wait": wait_id},
        )
    if wait.status == "cancelled":
        return {"cancelled": False, "wait": wait_payload(wait)}
    if wait.status != "proposed":
        raise CodedHTTPException(
            409,
            f"wait {wait_id} is {wait.status}; stop it in its own interface",
            code="wait.notCancelable",
            params={"wait": wait_id, "status": wait.status},
        )
    waits.cancel(session_id, wait_id, "stopped from the chat")
    stopped = waits.current(session_id) or wait
    return {"cancelled": True, "wait": wait_payload(stopped)}
