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

from noteagent.acquire.types import Capture
from noteagent.agent.clock import Clock, Ids, iso_timestamp
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    load_clip_store,
    save_clip_store,
)
from noteagent.pipeline.captures import CaptureInbox, LoginWall
from noteagent.pipeline.confirm import (
    CompileFn,
    Confirmation,
    NothingToWrite,
    PlanMissing,
    compile_outcome,
    confirmation_digest,
    default_compile,
    derive_confirmation,
)
from noteagent.pipeline.frame_model import ModelFraming
from noteagent.pipeline.framing import Framing
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.pipeline.materials import (
    DEFAULT_KEEP_RUNS,
    MaterialState,
    Materials,
    hydrate_collection,
)
from noteagent.pipeline.planning import PlanError, PlanFn, Planning
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
from noteagent.pipeline.stage import (
    FrameSummary,
    PipelineCancelled,
    PipelineStage,
    PlanSummary,
    StageContext,
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
        keep_runs: int = DEFAULT_KEEP_RUNS,
        frame_model: ModelFraming | None = None,
        planner: PlanFn | None = None,
        compile: CompileFn | None = None,
    ) -> None:
        self._layout = layout
        self._clock = clock
        self._ids = ids
        self._stages = ()
        self.use_stages(stages)
        if keep_runs < 1:
            raise ValueError("a pipeline keeps at least one run's staging")
        self._keep_runs = keep_runs
        self._registry = registry or PipelineRegistry()
        self._lock = lock or ProjectLock(layout.project_lock, clock=clock)
        self._spawn = spawn or _spawn_thread
        self._store = PipelineRunStore(layout)
        self._materials = Materials(layout, clock=clock)
        self._framing = Framing(
            layout, materials=self._materials, model=frame_model
        )
        self._planning = Planning(
            layout, materials=self._materials, planner=planner
        )
        self._compile = compile or default_compile
        self._captures = CaptureInbox(layout.capture_inbox)
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

    def _waiting_run(self) -> PipelineRun | None:
        for run in self._store.list():
            if run.status == "awaiting_confirmation":
                return run
        return None

    def use_stages(self, stages: Sequence[PipelineStage]) -> None:
        """Wire the stages, before a run starts; a mistyped name is refused."""
        checked = tuple(stages)
        for stage in checked:
            # A stage the run file cannot read back would make a run vanish from
            # the store, so a mistyped name is refused where it is wired.
            if stage.name not in STAGES:
                raise ValueError(f"unknown pipeline stage: {stage.name!r}")
        self._stages = checked

    def materials(self) -> Materials:
        return self._materials

    def materials_for_run(self, run_id: str) -> list[MaterialState]:
        return self._materials.for_run(run_id)

    def material_blocks(
        self, material: str, *, run_id: str
    ) -> list[dict[str, object]] | None:
        return self._materials.blocks(material, run_id=run_id)

    def frame_materials(self, run_id: str) -> FrameSummary:
        """Frame the run's staged materials, serialized with every other write."""
        with self._registry.writer(self._project_key):
            return self._framing.frame_run(run_id)

    def plan_materials(
        self, run_id: str, cancel: Event | None = None
    ) -> PlanSummary:
        """Plan the run's frames, serialized with every other write."""
        run = self._store.get(run_id)
        if run is None:
            raise PlanError(f"no pipeline run: {run_id}")
        with self._registry.writer(self._project_key):
            return self._planning.plan_run(run, cancel=cancel)

    def confirmation(self, run_id: str) -> Confirmation | None:
        """What this run proposes to write, while it waits for a person."""
        run = self._store.get(run_id)
        if run is None:
            return None
        return self._confirmation_for(run)

    def sync_waiting(self) -> bool:
        """Bring a waiting run's confirmation in line with the board.

        A plan board edit is the person changing what they will confirm. When the
        plan or its answers changed, the run gets a new revision, so an approval
        of the old one is refused instead of writing pages nobody saw. A frame or
        intent change clears the plan; the run then re-plans before waiting again.
        """
        run = self._waiting_run()
        if run is None:
            return False
        store = load_clip_store(self._layout)
        if store.topic_map is None:
            return self.resume_planning()
        digest = confirmation_digest(store)
        if digest == _recorded_digest(run):
            return False
        self._lock.acquire(run.id)
        try:
            current = self._store.get(run.id) or run
            if current.status != "awaiting_confirmation":
                return False
            store = load_clip_store(self._layout)
            digest = confirmation_digest(store)
            if store.topic_map is not None and digest != _recorded_digest(current):
                self._save(
                    replace(
                        current,
                        plan_revision=current.plan_revision + 1,
                        detail={**dict(current.detail), "planDigest": digest},
                    )
                )
                return True
        finally:
            self._lock.release(run.id)
        return self.resume_planning() if store.topic_map is None else False

    def resume_planning(self) -> bool:
        """Plan a waiting run again after its frames or intent changed."""
        run = self._waiting_run()
        if run is None:
            return False
        if load_clip_store(self._layout).topic_map is not None:
            return False
        try:
            cancel = self._registry.claim(self._project_key, run.id)
        except PipelineBusy:
            return False
        try:
            self._lock.acquire(run.id)
        except ProjectBusy:
            self._registry.release(self._project_key, run.id)
            return False
        started = False
        try:
            current = self._store.get(run.id) or run
            if current.status != "awaiting_confirmation":
                return False
            if load_clip_store(self._layout).topic_map is not None:
                return False
            running = replace(
                current,
                status="running",
                stage="plan",
                error="",
                updated_at=iso_timestamp(self._clock),
            )
            with self._registry.writer(self._project_key):
                self._store.save(running)
            started = True
        finally:
            if not started:
                self._lock.release(run.id)
                self._registry.release(self._project_key, run.id)
        self._spawn(lambda: self._replan_work(running, cancel))
        return True

    def approve(
        self,
        run_id: str,
        *,
        plan_revision: int,
        overrides: Sequence[Mapping[str, object]] = (),
    ) -> PipelineRun | None:
        """Approve a run's plan revision and start its compile.

        The pages come from the run and its plan; the caller names a revision,
        never a list of pages. A revision that no longer matches is a conflict,
        and nothing is written.
        """
        run = self._store.get(run_id)
        if run is None:
            return None
        cancel = self._registry.claim(self._project_key, run.id)
        try:
            self._lock.acquire(run.id)
        except ProjectBusy:
            self._registry.release(self._project_key, run.id)
            raise
        started = False
        try:
            current = self._store.get(run_id) or run
            if current.plan_revision != plan_revision:
                raise PipelineConflict(
                    "the plan has changed since this revision; review it again"
                )
            store = load_clip_store(self._layout)
            confirmation = self._confirmation_for(current, overrides, store)
            if not confirmation.writes:
                raise NothingToWrite("select at least one page to write")
            if cancel.is_set():
                self._save(replace(current, status="cancelled", error=""))
                return self._store.get(run_id) or current
            with self._registry.writer(self._project_key):
                # The confirmation becomes the stored gate; the person's own
                # answers stay for the next plan to carry over.
                save_clip_store(
                    self._layout,
                    replace(
                        store,
                        choices=confirmation.choices,
                        decisions=confirmation.decisions,
                    ),
                )
                current = replace(
                    current,
                    status="compiling",
                    stage="compile",
                    error="",
                    updated_at=iso_timestamp(self._clock),
                )
                self._store.save(current)
            started = True
        finally:
            if not started:
                self._lock.release(run.id)
                self._registry.release(self._project_key, run.id)
        self._spawn(lambda: self._compile_work(current, confirmation, cancel))
        return self._store.get(run_id) or current

    def _confirmation_for(
        self,
        run: PipelineRun,
        overrides: Sequence[Mapping[str, object]] = (),
        store=None,
    ) -> Confirmation:
        if run.status != "awaiting_confirmation":
            raise PipelineConflict("this run is not waiting for confirmation")
        clip_store = store or load_clip_store(self._layout)
        digest = confirmation_digest(clip_store)
        if digest != _recorded_digest(run):
            raise PipelineConflict(
                "the plan changed since this revision; review it again"
            )
        try:
            hydrated = self._store_with_blocks(run, clip_store)
            return derive_confirmation(
                self._layout,
                run_id=run.id,
                intent=run.intent,
                revision=run.plan_revision,
                store=hydrated,
                overrides=overrides,
            )
        except PlanMissing as err:
            raise PipelineConflict(str(err)) from err

    def _store_with_blocks(self, run: PipelineRun, store: ClipStore) -> ClipStore:
        """The store as this run sees it: its selections carry their block texts.

        Framing stores block ids, not texts; a confirmation that needs the text
        reads it from the run's staging, where the capture put it. The saved
        selection keeps only ids, as the material identity says it should.
        """
        run_materials = {
            state.material for state in self._materials.for_run(run.id)
        }
        collections: list[ClipCollection] = []
        for collection in store.collections:
            blocks = self._materials.blocks(
                collection.source_key, run_id=run.id
            )
            if blocks is None:
                if collection.source_key in run_materials:
                    raise PlanMissing(
                        "the staged blocks for "
                        f"{collection.source_key} are gone"
                    )
                collections.append(collection)
                continue
            hydrated = hydrate_collection(collection, blocks)
            collections.append(
                collection if hydrated is None else hydrated
            )
        return replace(store, collections=tuple(collections))

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

    def stage_material(
        self,
        capture: Capture,
        *,
        run_id: str,
        captured_at: str = "",
    ) -> MaterialState:
        """Stage one capture as a material, serialized with every other write."""
        with self._registry.writer(self._project_key):
            state = self._materials.stage(
                capture, run_id=run_id, captured_at=captured_at
            )
            # Staging is where an unpromoted material accumulates; pruning here
            # keeps a long-lived core from growing without a restart.
            self._materials.clean(self._store.list(), keep=self._keep_runs)
            return state

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
                    stage_material=lambda capture: self.stage_material(
                        capture, run_id=current.id
                    ),
                    frame_materials=lambda: self.frame_materials(current.id),
                    plan_materials=lambda: self.plan_materials(
                        current.id, cancel
                    ),
                )
                try:
                    stage(context)
                finally:
                    # A stage that raises has usually written what it found to the
                    # run already; keep that detail on the failure.
                    current = context.run
                    self._record_login_walls(current)
                # A stage that pauses the run (or fails it) ends this worker; the
                # run is not walked any further.
                if current.status != "running":
                    return
            if cancel.is_set():
                raise PipelineCancelled("the run was cancelled")
            if current.plan_revision > 0:
                # A plan is a proposal until a person approves it. The run waits
                # with the project released, so the board can still be changed.
                self._save(
                    replace(
                        current,
                        status="awaiting_confirmation",
                        stage="confirm",
                    )
                )
            else:
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

    def _compile_work(
        self,
        run: PipelineRun,
        confirmation: Confirmation,
        cancel: Event,
    ) -> None:
        current = run
        try:
            # Only the pages being written need durable originals; everything
            # else this run collected stays in staging for retention.
            self._promote_for(current, confirmation)
            result = self._compile(self._layout, confirmation, cancel)
            self._save(
                replace(
                    current,
                    status="succeeded",
                    stage="done",
                    result=compile_outcome(confirmation, result),
                )
            )
        except (PipelineCancelled, JobCancelled):
            self._save(replace(current, status="cancelled", error=""))
        except Exception as err:  # noqa: BLE001 - a failed compile is a result
            logger.exception("pipeline compile %s failed", current.id)
            self._save(replace(current, status="failed", error=str(err)[:500]))
        finally:
            self._release_run(current)

    def _promote_for(
        self, run: PipelineRun, confirmation: Confirmation
    ) -> None:
        """Make the write pages' originals durable; unused material stays staged."""
        sources = {
            collection.source_key.replace("\\", "/").strip()
            for collection in confirmation.selections
        }
        with self._registry.writer(self._project_key):
            for source in sorted(sources):
                if not source.startswith("raw/"):
                    # A preview key belongs to the manual path's storage step.
                    continue
                self._materials.promote(source, run_id=run.id)

    def _replan_work(self, run: PipelineRun, cancel: Event) -> None:
        current = run
        try:
            summary = self.plan_materials(current.id, cancel)
            self._save(
                replace(
                    current,
                    status="awaiting_confirmation",
                    stage="confirm",
                    plan_revision=current.plan_revision + 1,
                    detail={**dict(current.detail), **summary.detail()},
                )
            )
        except (PipelineCancelled, JobCancelled):
            self._save(replace(current, status="cancelled", error=""))
        except Exception as err:  # noqa: BLE001 - a failed replan is a result
            logger.exception("pipeline replan %s failed", current.id)
            self._save(replace(current, status="failed", error=str(err)[:500]))
        finally:
            self._release_run(current)


def _recorded_digest(run: PipelineRun) -> str:
    value = run.detail.get("planDigest")
    return value.strip() if isinstance(value, str) else ""


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="pipeline-run", daemon=True).start()


__all__ = [
    "PipelineBusy",
    "PipelineCancelled",
    "PipelineConflict",
    "PipelineRegistry",
    "PipelineService",
    "PipelineStage",
    "StageContext",
]
