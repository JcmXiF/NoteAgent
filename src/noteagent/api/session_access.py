"""Opening a session for the versioned routes, with one set of refusals.

The read view, the query and the cancel all need the same store, the same answer
when the id is bad or the session is gone, and the same answer to "is it
running?". Keeping that here means the stream cannot start on a session the read
would have refused, and a cancel reaches the same registry the query claimed.
"""

from __future__ import annotations

from noteagent.agent.kernel import RunRegistry
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore, WaitStore
from noteagent.agent.tools import ToolRegistry
from noteagent.agent.wait import wait_payload
from noteagent.agent.wait_store import SessionWaitStore
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore, SessionFormatError
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
