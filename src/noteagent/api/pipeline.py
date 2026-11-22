"""The read-only legacy view of old pipeline runs.

The run engine is gone; the run files stay. The legacy routes list, read and
restore them through this module, which only touches the store and the
materials log — nothing here can start or advance a run.
"""

from __future__ import annotations

from noteagent.pipeline.materials import Materials
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.layout import ProjectLayout


def pipeline_store(layout: ProjectLayout) -> PipelineRunStore:
    """The project's run files, read only."""
    return PipelineRunStore(layout)


def pipeline_materials(layout: ProjectLayout, *, clock=None) -> Materials:
    """The project's material log and staging, read only."""
    return Materials(layout, clock=clock or _system_clock())


def _system_clock():
    from noteagent.agent.clock import SystemClock

    return SystemClock()


__all__ = [
    "pipeline_materials",
    "pipeline_store",
]
