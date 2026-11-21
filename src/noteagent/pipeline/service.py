"""One run at a time, and the worker that walks its stages.

The run owns the project while it is active: a second start is refused, not
queued. The worker holds the project's lock file for the length of the run, so a
second process cannot write the same project at the same time either.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from threading import Event, Lock
from typing import TypeVar

from noteagent.agent.clock import Clock, Ids, iso_timestamp
from noteagent.effects.restore import preview_restore, restore_run
from noteagent.pipeline.captures import CaptureInbox, LoginWall
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.pipeline.materials import (
    DEFAULT_KEEP_RUNS,
    MaterialState,
    Materials,
)
from noteagent.pipeline.run import (
    MATERIAL_MODES,
    QUERY_MODES,
    STARTED_BY,
    MaterialMode,
    PipelineRun,
    QueryMode,
    StartedBy,
)
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.layout import ProjectLayout
from noteagent.util.cancel import JobCancelled

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class PipelineBusy(RuntimeError):
    """The project already has an active run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"a pipeline run is already active: {run_id}")


class PipelineConflict(RuntimeError):
    """The run is not in the state the caller's revision names."""


class PipelineCancelled(RuntimeError):
    """A stage noticed the run was cancelled."""


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
        registry: PipelineRegistry | None = None,
        lock: ProjectLock | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        keep_runs: int = DEFAULT_KEEP_RUNS,
    ) -> None:
        self._layout = layout
        self._clock = clock
        self._ids = ids
        if keep_runs < 1:
            raise ValueError("a pipeline keeps at least one run's staging")
        self._keep_runs = keep_runs
        self._registry = registry or PipelineRegistry()
        self._lock = lock or ProjectLock(layout.project_lock, clock=clock)
        self._spawn = spawn or _spawn_thread
        self._store = PipelineRunStore(layout)
        self._materials = Materials(layout, clock=clock)
        self._captures = CaptureInbox(layout.capture_inbox)
        self._project_key = str(layout.root.resolve())

    def restore_preview(self, run_id: str) -> dict[str, object]:
        """What undoing this run would touch, without changing anything."""
        return preview_restore(self._layout, run_id)

    def restore(
        self, run_id: str, pages: Sequence[Mapping[str, object]]
    ) -> dict[str, object]:
        """Undo the selected pages of this run."""
        return restore_run(self._layout, run_id, pages)

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
        if material_mode != "auto":
            # Manual material mode is the source workbench, where the person
            # picks cards; a run that acquired anyway would decide for them.
            raise ValueError(
                "manual material mode is used from the source workbench, not a pipeline run"
            )

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

    def materials(self) -> Materials:
        return self._materials

    def materials_for_run(self, run_id: str) -> list[MaterialState]:
        return self._materials.for_run(run_id)

    def material_blocks(
        self, material: str, *, run_id: str
    ) -> list[dict[str, object]] | None:
        return self._materials.blocks(material, run_id=run_id)

    def write_clips(self, work: Callable[[], _T]) -> _T:
        """Run a selection write under the project's lock and single writer."""
        self._lock.acquire("clips")
        try:
            with self._registry.writer(self._project_key):
                return work()
        finally:
            self._lock.release("clips")

    def capture_payload(self) -> dict[str, object]:
        return self._captures.payload()

    def record_capture(self, wall: LoginWall) -> None:
        """Put one sign-in page on the queue, serialized with every other write."""
        with self._registry.writer(self._project_key):
            self._captures.add(
                url=wall.url,
                title=wall.title,
                reason=wall.reason,
                http_status=wall.http_status,
                run_id=wall.run_id,
                at=iso_timestamp(self._clock),
            )

    def dismiss_capture(self, item_id: str) -> bool:
        """Drop one queued page; a run in another process keeps its project."""
        self._lock.acquire(f"capture:{item_id}")
        try:
            with self._registry.writer(self._project_key):
                return self._captures.dismiss(item_id)
        finally:
            self._lock.release(f"capture:{item_id}")

    def prepare(self) -> None:
        """Bring the project's runs and materials up to date at startup."""
        self.mark_interrupted()
        try:
            self._lock.acquire("startup-prepare")
        except ProjectBusy:
            # Another process owns the project; its staging is not ours to prune.
            return
        try:
            with self._registry.writer(self._project_key):
                self._materials.backfill()
                self._materials.clean(
                    self._store.list(), keep=self._keep_runs
                )
        finally:
            self._lock.release("startup-prepare")

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
            # An approval may have claimed the run while this waited for the
            # lock; the compile it started is now the worker to stop.
            if self._registry.cancel(run_id):
                return self._store.get(run_id) or run
            current = self._store.get(run_id) or run
            return self._save(replace(current, status="cancelled", error=""))
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

    def _record_login_walls(self, run: PipelineRun) -> None:
        """Queue the pages a stage met behind a sign-in, once per wall."""
        raw = run.detail.get("loginWallItems")
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            try:
                self.record_capture(
                    LoginWall(
                        url=str(item.get("url") or ""),
                        title=str(item.get("title") or ""),
                        reason=str(item.get("reason") or ""),
                        http_status=(
                            item.get("http_status")
                            if isinstance(item.get("http_status"), int)
                            else None
                        ),
                        run_id=str(item.get("run_id") or run.id),
                    )
                )
            except (OSError, ValueError):
                logger.exception("a sign-in page could not be queued")

    def _save(self, run: PipelineRun) -> PipelineRun:
        current = replace(run, updated_at=iso_timestamp(self._clock))
        with self._registry.writer(self._project_key):
            self._store.save(current)
        return current

    def _release_run(self, run: PipelineRun) -> None:
        """Give the project back when a worker, however it ended, is done."""
        self._lock.release(run.id)
        self._registry.release(self._project_key, run.id)

    def _work(self, run: PipelineRun, cancel: Event) -> None:
        current = run
        try:
            if cancel.is_set():
                raise PipelineCancelled("the run was cancelled")
            self._save(replace(current, status="succeeded", stage="done"))
        except (PipelineCancelled, JobCancelled):
            # A stop during planning surfaces from the shared job as
            # JobCancelled; either way the reader cancelled the run.
            self._save(replace(current, status="cancelled", error=""))
        except Exception as err:  # noqa: BLE001 - a failed run is a result
            logger.exception("pipeline run %s failed", current.id)
            self._save(replace(current, status="failed", error=str(err)[:500]))
        finally:
            self._release_run(current)

def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="pipeline-run", daemon=True).start()


__all__ = [
    "PipelineBusy",
    "PipelineCancelled",
    "PipelineConflict",
    "PipelineRegistry",
    "PipelineService",
]
