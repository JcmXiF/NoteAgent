"""One run at a time, and the worker that walks its stages.

The run owns the project while it is active: a second start is refused, not
queued. The worker holds the project's lock file for the length of the run, so a
second process cannot write the same project at the same time either.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from threading import Event, Lock
from typing import Protocol, runtime_checkable

from noteagent.agent.clock import Clock, Ids, iso_timestamp
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.pipeline.run import (
    MATERIAL_MODES,
    QUERY_MODES,
    STAGES,
    STARTED_BY,
    MaterialMode,
    PipelineRun,
    QueryMode,
    StartedBy,
)
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.layout import ProjectLayout

logger = logging.getLogger(__name__)


class PipelineBusy(RuntimeError):
    """The project already has an active run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"a pipeline run is already active: {run_id}")


class PipelineCancelled(RuntimeError):
    """A stage noticed the run was cancelled."""


@dataclass(slots=True)
class StageContext:
    """What a stage may read and change while it runs."""

    run: PipelineRun
    layout: ProjectLayout
    cancel: Event
    _save: Callable[[PipelineRun], PipelineRun]

    def update(self, **changes: object) -> PipelineRun:
        """Write one change to the run and carry on with the fresh copy."""
        self.run = self._save(replace(self.run, **changes))
        return self.run

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise PipelineCancelled("the run was cancelled")


@runtime_checkable
class PipelineStage(Protocol):
    """One step of the run, named so the run says where it is."""

    name: str

    def __call__(self, context: StageContext) -> None: ...


class PipelineRegistry:
    """The active run of each project, shared by every request in one process."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: dict[str, str] = {}
        self._cancels: dict[str, Event] = {}
        self._writers: dict[str, Lock] = {}

    def writer(self, project_key: str) -> Lock:
        """The one lock every run-file write of a project goes through."""
        with self._lock:
            return self._writers.setdefault(project_key, Lock())

    def claim(self, project_key: str, run_id: str) -> Event:
        with self._lock:
            current = self._active.get(project_key)
            if current is not None:
                raise PipelineBusy(current)
            event = Event()
            self._active[project_key] = run_id
            self._cancels[run_id] = event
            return event

    def release(self, project_key: str, run_id: str) -> None:
        with self._lock:
            if self._active.get(project_key) == run_id:
                del self._active[project_key]
            self._cancels.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            event = self._cancels.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def is_busy(self, project_key: str) -> bool:
        with self._lock:
            return project_key in self._active


class PipelineService:
    """One project's runs: start, read, cancel, and the worker behind them."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        clock: Clock,
        ids: Ids,
        stages: Sequence[PipelineStage] = (),
        registry: PipelineRegistry | None = None,
        lock: ProjectLock | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._layout = layout
        self._clock = clock
        self._ids = ids
        self._stages = tuple(stages)
        for stage in self._stages:
            # A stage the run file cannot read back would make a run vanish from
            # the store, so a mistyped name is refused where it is wired.
            if stage.name not in STAGES:
                raise ValueError(f"unknown pipeline stage: {stage.name!r}")
        self._registry = registry or PipelineRegistry()
        self._lock = lock or ProjectLock(layout.project_lock, clock=clock)
        self._spawn = spawn or _spawn_thread
        self._store = PipelineRunStore(layout)
        self._project_key = str(layout.root.resolve())

    def start(
        self,
        *,
        intent: str,
        started_by: StartedBy = "human",
        session_id: str = "",
        query_mode: QueryMode = "precise",
        material_mode: MaterialMode = "auto",
    ) -> PipelineRun:
        """Create a run and hand it to the worker."""
        cleaned = " ".join(intent.split())
        if not cleaned:
            raise ValueError("a pipeline run needs an intent")
        if started_by not in STARTED_BY:
            raise ValueError(f"unknown startedBy: {started_by!r}")
        if query_mode not in QUERY_MODES:
            raise ValueError(f"unknown queryMode: {query_mode!r}")
        if material_mode not in MATERIAL_MODES:
            raise ValueError(f"unknown materialMode: {material_mode!r}")

        # A paused run has no worker to hold the in-memory claim, so the store
        # answers too. The registry still arbitrates two starts that arrive
        # together.
        active = self._store.active()
        if active:
            raise PipelineBusy(active[0].id)

        now = iso_timestamp(self._clock)
        run = PipelineRun(
            id=self._ids.new_id("pr"),
            intent=cleaned[:2000],
            started_by=started_by,
            session_id=(session_id or "").strip(),
            query_mode=query_mode,
            material_mode=material_mode,
            started_at=now,
            updated_at=now,
        )
        cancel = self._registry.claim(self._project_key, run.id)
        try:
            self._lock.acquire(run.id)
        except ProjectBusy:
            self._registry.release(self._project_key, run.id)
            raise
        try:
            with self._registry.writer(self._project_key):
                self._store.create(run)
        except Exception:
            self._lock.release(run.id)
            self._registry.release(self._project_key, run.id)
            raise
        self._spawn(lambda: self._work(run, cancel))
        return self._store.get(run.id) or run

    def get(self, run_id: str) -> PipelineRun | None:
        return self._store.get(run_id)

    def list(self) -> list[PipelineRun]:
        return self._store.list()

    def active(self) -> list[PipelineRun]:
        return self._store.active()

    def cancel(self, run_id: str) -> PipelineRun | None:
        """Ask the run to stop; a run that is already done is returned as it is."""
        run = self._store.get(run_id)
        if run is None:
            return None
        if not run.active:
            return run
        if self._registry.cancel(run_id):
            return self._store.get(run_id) or run
        # Nothing in this process will settle it: it is a leftover, not a worker.
        # The write takes the project's lock, so this core cannot cancel a run
        # another process is still moving.
        self._lock.acquire(run_id)
        try:
            return self._save(replace(run, status="cancelled", error=""))
        finally:
            self._lock.release(run_id)

    def mark_interrupted(self) -> int:
        """Close the runs a previous core left open.

        A live owner means another process is working this project, so its runs
        are not ours to close. Taking the lock is what proves that, and it also
        keeps the sweep from racing a run that starts at the same moment.
        """
        try:
            self._lock.acquire("startup-sweep")
        except ProjectBusy:
            return 0
        try:
            with self._registry.writer(self._project_key):
                return self._store.mark_interrupted(
                    now=iso_timestamp(self._clock)
                )
        finally:
            self._lock.release("startup-sweep")

    def _save(self, run: PipelineRun) -> PipelineRun:
        current = replace(run, updated_at=iso_timestamp(self._clock))
        with self._registry.writer(self._project_key):
            self._store.save(current)
        return current

    def _work(self, run: PipelineRun, cancel: Event) -> None:
        current = run
        try:
            for stage in self._stages:
                if cancel.is_set():
                    raise PipelineCancelled("the run was cancelled")
                self._lock.heartbeat(current.id)
                current = self._save(
                    replace(current, stage=stage.name, status="running")
                )
                context = StageContext(
                    run=current,
                    layout=self._layout,
                    cancel=cancel,
                    _save=self._save,
                )
                stage(context)
                current = context.run
                # A stage that pauses the run (or fails it) ends this worker; the
                # run is not walked any further.
                if current.status != "running":
                    return
            if cancel.is_set():
                raise PipelineCancelled("the run was cancelled")
            self._save(replace(current, status="succeeded", stage="done"))
        except PipelineCancelled:
            self._save(replace(current, status="cancelled", error=""))
        except Exception as err:  # noqa: BLE001 - a failed run is a result
            logger.exception("pipeline run %s failed", current.id)
            self._save(replace(current, status="failed", error=str(err)[:500]))
        finally:
            self._lock.release(current.id)
            self._registry.release(self._project_key, current.id)


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="pipeline-run", daemon=True).start()


__all__ = [
    "PipelineBusy",
    "PipelineCancelled",
    "PipelineRegistry",
    "PipelineService",
    "PipelineStage",
    "StageContext",
]
