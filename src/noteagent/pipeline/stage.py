"""What a stage may read and change while a run walks it.

The context is passed to every stage, so this module has no opinion about the
service that built it: a stage can read the run, write a change, stage a capture
and frame the material, and knows nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event
from typing import Protocol, runtime_checkable

from noteagent.acquire.types import Capture
from noteagent.pipeline.materials import MaterialState
from noteagent.pipeline.run import PipelineRun
from noteagent.project.layout import ProjectLayout


class PipelineCancelled(RuntimeError):
    """A stage noticed the run was cancelled."""


@dataclass(frozen=True, slots=True)
class FrameSummary:
    """How framing went: materials framed, and what it cost."""

    framed: int = 0
    model_calls: int = 0
    chars_read: int = 0
    fallbacks: int = 0
    omitted: int = 0

    def usage(self) -> dict[str, object]:
        return {
            "modelCalls": self.model_calls,
            "charsRead": self.chars_read,
            "fallbacks": self.fallbacks,
            "omittedFrames": self.omitted,
        }


@dataclass(slots=True)
class StageContext:
    """What a stage may read and change while it runs."""

    run: PipelineRun
    layout: ProjectLayout
    cancel: Event
    _save: Callable[[PipelineRun], PipelineRun]
    #: Stage one capture as a material of this run, through the single writer.
    stage_material: Callable[[Capture], MaterialState]
    #: Turn this run's staged materials into frames, through the single writer.
    frame_materials: Callable[[], FrameSummary]

    def update(self, **changes: object) -> PipelineRun:
        """Write one change to the run and carry on with the fresh copy."""
        self.run = self._save(replace(self.run, **changes))
        return self.run

    def add_detail(self, **values: object) -> PipelineRun:
        """Merge into the run's detail, keeping what earlier stages wrote."""
        self.run = self._save(
            replace(
                self.run,
                detail={**dict(self.run.detail), **values},
            )
        )
        return self.run

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise PipelineCancelled("the run was cancelled")


@runtime_checkable
class PipelineStage(Protocol):
    """One step of the run, named so the run says where it is."""

    name: str

    def __call__(self, context: StageContext) -> None: ...


__all__ = [
    "FrameSummary",
    "PipelineCancelled",
    "PipelineStage",
    "StageContext",
]
