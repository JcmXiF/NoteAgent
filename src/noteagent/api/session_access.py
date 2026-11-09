"""Opening a session for the versioned routes, with one set of refusals.

The read view, the query and the cancel all need the same store, the same answer
when the id is bad or the session is gone, and the same answer to "is it
running?". Keeping that here means the stream cannot start on a session the read
would have refused, and a cancel reaches the same registry the query claimed.
"""

from __future__ import annotations

from noteagent.agent.kernel import RunRegistry
from noteagent.agent.runtime import AgentRuntime
from noteagent.agent.store import SessionStore
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.sessions.jsonl import JsonlSessionStore, SessionFormatError


def open_session_store(deps: AppDeps, session_id: str) -> SessionStore:
    """The store holding *session_id*, after proving the session can be read."""
    store = deps.sessions
    if store is None:
        try:
            layout = get_active_layout()
        except ProjectNotConfiguredError as err:
            raise CodedHTTPException(
                409, str(err), code="project.notConfigured"
            ) from err
        store = JsonlSessionStore(layout, clock=deps.clock, ids=deps.ids)
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
