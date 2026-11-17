"""The pipeline service for the active project, and the startup sweep.

The run belongs to the core, so the routes do not keep one: they ask this module
for a service built from the active project, with the clock, ids, stages and
registry the app was composed with.
"""

from __future__ import annotations

from noteagent.api.deps import AppDeps
from noteagent.api.errors import CodedHTTPException
from noteagent.api.projects import get_active_layout
from noteagent.pipeline.service import PipelineService
from noteagent.project.config import ProjectNotConfiguredError


def pipeline_service(deps: AppDeps) -> PipelineService:
    """The active project's pipeline, or the one a test handed in."""
    if deps.pipeline is not None:
        return deps.pipeline
    try:
        layout = get_active_layout()
    except ProjectNotConfiguredError as err:
        raise CodedHTTPException(409, str(err), code="project.notConfigured") from err
    return PipelineService(
        layout,
        clock=deps.clock,
        ids=deps.ids,
        stages=deps.pipeline_stages,
        registry=deps.pipeline_registry,
        spawn=deps.pipeline_spawn,
        keep_runs=deps.pipeline_keep_runs,
    )


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


__all__ = ["pipeline_service", "prepare_active_project"]
