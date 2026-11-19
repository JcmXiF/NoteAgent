"""The writing task of the active project, and the production search node.

The task belongs to the core, so a route does not keep one: it asks for the
active project's service, built with the clock, ids, spawn and registry the app
was composed with. The default search node wraps the existing acquire search;
the task executor only sees a pure function that returns cards.
"""

from __future__ import annotations

from noteagent.acquire.run_search import run_web_search
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import SearchCard, SearchFn
from noteagent.task.service import TaskService
from noteagent.util.run_log import RunLogger


def task_service(deps: AppDeps) -> TaskService:
    """The active project's task, or the one a test handed in."""
    if deps.tasks is not None:
        return deps.tasks
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError as err:
        raise CodedHTTPException(409, str(err), code="project.notConfigured") from err
    return task_service_for(deps, layout)


def task_service_for(deps: AppDeps, layout: ProjectLayout) -> TaskService:
    """The task service of one named project, for a caller with its layout."""
    if deps.tasks is not None:
        return deps.tasks
    search = deps.task_search or default_search(layout)
    return TaskService(
        layout,
        clock=deps.clock,
        ids=deps.ids,
        search=search,
        registry=deps.task_registry,
        spawn=deps.task_spawn,
        wait_s=deps.task_wait_s,
    )


def task_view_payload(deps: AppDeps, view: str) -> dict[str, object]:
    """A bounded task view for the interface, with its revision."""
    payload, _rev = task_service(deps).inspect(view)
    return payload


def default_search(layout: ProjectLayout) -> SearchFn:
    """The project's configured web search, as a pure card-returning node."""
    logger = RunLogger.for_layout(layout)

    def search(query: str) -> list[SearchCard]:
        run = run_web_search(
            query, top_k=8, run_logger=logger, caller="task"
        )
        return [
            SearchCard(
                title=card.title,
                url=card.url,
                snippet=card.snippet,
                site=card.site or card.site_name,
            )
            for card in run.cards
        ]

    return search


__all__ = [
    "default_search",
    "task_service",
    "task_service_for",
    "task_view_payload",
]
