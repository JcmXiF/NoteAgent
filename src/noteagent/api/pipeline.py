"""The pipeline service for the active project, and its production stages.

The run belongs to the core, so the routes do not keep one: they ask this module
for a service built from the active project, with the clock, ids, stages and
registry the app was composed with. The default stages are wired here, at the
composition root, because this is where the search and fetch adapters belong.
"""

from __future__ import annotations

from collections.abc import Sequence

from noteagent.acquire.query import rewrite_query
from noteagent.acquire.run_fetch import run_web_fetch
from noteagent.acquire.run_search import run_web_search
from noteagent.agent.clock import Clock
from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.pipeline.acquire import AcquireStage
from noteagent.pipeline.service import PipelineService, PipelineStage
from noteagent.project.config import ProjectNotConfiguredError
from noteagent.project.layout import ProjectLayout
from noteagent.util.run_log import RunLogger


def pipeline_service(deps: AppDeps) -> PipelineService:
    """The active project's pipeline, or the one a test handed in."""
    if deps.pipeline is not None:
        return deps.pipeline
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError as err:
        raise CodedHTTPException(409, str(err), code="project.notConfigured") from err
    # The service exists before its stages do: a default stage writes the
    # sign-in queue through the same single writer the service owns.
    service = PipelineService(
        layout,
        clock=deps.clock,
        ids=deps.ids,
        registry=deps.pipeline_registry,
        spawn=deps.pipeline_spawn,
        keep_runs=deps.pipeline_keep_runs,
    )
    if deps.pipeline_stages is None:
        service.use_stages(default_stages(layout, clock=deps.clock))
    else:
        service.use_stages(deps.pipeline_stages)
    return service


def prepare_active_project(deps: AppDeps) -> None:
    """Close runs a previous core left open and settle the material store.

    A core with no project yet, or one whose pointer cannot be read, has nothing
    to prepare; that is a normal start, not a failure.
    """
    try:
        service = pipeline_service(deps)
    except (CodedHTTPException, OSError, ValueError):
        return
    try:
        service.prepare()
    except OSError:
        return


def default_stages(layout: ProjectLayout, *, clock: Clock) -> Sequence[PipelineStage]:
    """The stages a product run walks: collect the material, for now."""
    del clock
    logger = RunLogger.for_layout(layout)

    def search(query: str):
        run = run_web_search(
            query, top_k=8, run_logger=logger, caller="pipeline"
        )
        return run.cards

    def fetch(url: str, query: str):
        return run_web_fetch(
            url, query=query, run_logger=logger, caller="pipeline"
        )

    return (
        AcquireStage(
            search=search,
            fetch=fetch,
            rewrite=rewrite_query,
        ),
    )


__all__ = [
    "default_stages",
    "pipeline_service",
    "prepare_active_project",
]
