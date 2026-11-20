"""The task's single writer, and the bounded wait a tool puts around it.

A tool never applies a node's answer to the task itself. It writes a typed op,
the executor claims it, calls the pure node outside the task lock, and writes
the answer back. The tool then waits a bounded time for that boundary: a fast
search answers with its cards, a slow one answers ``requested``/``running`` and
the executor finishes it after the conversation has moved on.

The session-aware part is small on purpose. Each tool result carries the task
revision the run has seen; the next mutation names that revision, so a change
made in between is a ``conflict`` rather than a silent overwrite.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from threading import Event, Lock
from typing import Iterator, TypeAlias

from noteagent.acquire.quality import FIDELITY_FAILED
from noteagent.agent.clock import Clock, Ids, iso_timestamp
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import ToolError
from noteagent.ingest.page_history import blob_digest
from noteagent.ingest.wiki_paths import resolve_wiki_write_path
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.project.layout import ProjectLayout
from noteagent.task.model import (
    ACTIVE_OP_STATUSES,
    MAX_BLOCK_PREVIEW,
    MAX_CARD_SNIPPET,
    MAX_MATERIAL_TITLE,
    MAX_VIEW_BLOCKS,
    MAX_VIEW_CARDS,
    TASK_VIEWS,
    TERMINAL_OP_STATUSES,
    Approval,
    Card,
    FetchFn,
    Frame,
    Op,
    SearchCard,
    SearchFn,
    StagedMaterial,
    StageFn,
    Task,
    WriteRequest,
    canonical_page_spec,
    material_summary,
    task_overview,
    task_view,
)
from noteagent.task.store import TaskStore, TaskUnreadableError
from noteagent.task.writing import (
    ComposePageFn,
    ComposeRequest,
    PageBusy,
    PageLocks,
    SavePageFn,
    SaveRequest,
    TaskPageCompose,
    TaskPageSave,
    diff_counts,
    locators_for_frame,
    page_diff,
    page_path_for_spec,
)

logger = logging.getLogger(__name__)

#: How long a search waits for its op before it says requested/running.
DEFAULT_WAIT_S = 15.0

#: How long a pick waits: fetching a page and staging it is a network round
#: trip, not a local read, so the tool blocks longer before it hands the turn
#: back to the model with ``requested``/``running``.
DEFAULT_PICK_WAIT_S = 90.0

#: How long an approved compose waits for the page write. Composing is a model
#: run and saving is local; three minutes is the budget in which a normal page
#: finishes. Past it the tool answers requested/running and the executor carries
#: on (a later ticket resumes the conversation at that boundary).
DEFAULT_COMPOSE_WAIT_S = 180.0

#: How often the wait looks for the executor's write. It is a local file, so
#: this is a poll between two boundaries, not a network retry.
DEFAULT_POLL_S = 0.02


class TaskToolError(ToolError):
    """A task refusal the model is told about through the tool error channel."""


class TaskConflict(TaskToolError):
    """The caller's revision is not the task's current revision."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict")


class TaskOpFailed(TaskToolError):
    """The node behind an op failed; the task itself is still readable."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="op_failed")


class TaskNotFound(TaskToolError):
    """The thing an op names is not in the task any more."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found")


class TaskBadSpec(TaskToolError):
    """A frame or a writing spec names something that does not fit the task."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="bad_spec")


class TaskNeedsApproval(TaskToolError):
    """A person's decision names a request that is not the one pending now."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="needs_approval")


class TaskUnreadable(TaskToolError):
    """The task file exists but cannot be read; it is never replaced."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="task.unreadable")


@dataclass(frozen=True, slots=True)
class OpResumeNotice:
    """A finished op the session was waiting on, with its bounded delta.

    The delta is exactly what the resumed conversation needs: which op, what
    kind, how it ended, the task revision it belongs to, and one bounded result
    (the cards a search found, the material a pick staged, the page a write
    produced). It is never the task and never the op log.
    """

    session_id: str
    op_id: str
    kind: str
    status: str
    rev: int
    result: Mapping[str, object] = field(default_factory=dict)


#: What an executor calls when an op finishes and its session asked to be
#: resumed. The API composes one over the session store and runtime; a test
#: records the notice instead of starting a run.
TaskOpResumeFn: TypeAlias = Callable[[OpResumeNotice], None]

#: The revision a session's own transcript says it last saw. It is the restart
#: fallback for the in-memory observation, so a write made while the core was
#: down is still a conflict rather than a silent overwrite.
ObservedRevFn: TypeAlias = Callable[[str], int | None]


@dataclass(frozen=True, slots=True)
class OpOutcome:
    """A person's cancel or resume of one op, and what it left standing."""

    op: Op
    rev: int
    #: Whether this call changed the op. A second cancel of a finished op is a
    #: no-op, not an error: the UI may lose the race with the executor.
    changed: bool = True


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """What a tool gets back from a search: cards, or where the op stands."""

    op_id: str
    query: str
    status: str
    rev: int
    cards: tuple[Card, ...] = ()


@dataclass(frozen=True, slots=True)
class PickOutcome:
    """What a tool or a route gets back from a pick: a material, or a status."""

    op_id: str
    card_id: str
    status: str
    rev: int
    material: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FrameOutcome:
    """What a tool or a route gets back from drawing a frame."""

    op_id: str
    doc_id: str
    status: str
    rev: int
    frame: Frame
    #: Whether this frame write cleared an approval the person had given.
    approval_cleared: bool = False


@dataclass(frozen=True, slots=True)
class ComposeOutcome:
    """What ``compose_page`` gets back: the gate's answer or the page's fate.

    ``needs_approval`` carries the write request recorded in the task and no op.
    A matching approval writes one ``write`` op and waits; the outcome then says
    ``written``/``skipped``/``failed`` with a bounded result, or
    ``requested``/``running`` when the executor has not finished yet. Effect ids
    are the op's own record and are never part of this mapping.
    """

    status: str
    rev: int
    frame_id: str
    spec: Mapping[str, object]
    request: WriteRequest | None = None
    approval: Approval | None = None
    approval_cleared: bool = False
    op_id: str = ""
    result: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ApproveOutcome:
    """What a person's decision leaves behind: a go-ahead, or none."""

    decision: str
    rev: int
    approval: Approval | None = None


@dataclass(frozen=True, slots=True)
class EditOutcome:
    """What a person's recorded page save leaves behind.

    The page write itself is the editor's; this is only the task's record of it,
    the revision it moved to, and whether a standing approval or a pending write
    request was cleared because the writing basis changed.
    """

    op_id: str
    path: str
    revision: str
    rev: int
    approval_cleared: bool = False
    request_cleared: bool = False


class TaskRegistry:
    """The task's process-wide single writer and what each session has seen.

    A service is built per request, like the pipeline's, so anything that must
    survive between two calls lives here rather than on the service. The writer
    lock serialises every read-modify-write of the task file in this process;
    the task's own lock file covers the processes.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._writers: dict[str, Lock] = {}
        self._observed: dict[tuple[str, str], int] = {}
        self._op_cancels: dict[str, Event] = {}

    def writer(self, project_key: str) -> Lock:
        with self._lock:
            return self._writers.setdefault(project_key, Lock())

    def observe(self, project_key: str, session_id: str, rev: int) -> None:
        """Remember the revision a session's last tool result carried."""
        if not session_id:
            return
        with self._lock:
            self._observed[(project_key, session_id)] = rev

    def observed(self, project_key: str, session_id: str) -> int | None:
        with self._lock:
            return self._observed.get((project_key, session_id))

    def op_cancel(self, op_id: str) -> Event:
        """The per-op stop signal, shared by the cancel route and its executor."""
        with self._lock:
            return self._op_cancels.setdefault(op_id, Event())

    def cancel_op(self, op_id: str) -> None:
        self.op_cancel(op_id).set()

    def forget_op(self, op_id: str) -> None:
        with self._lock:
            self._op_cancels.pop(op_id, None)


class TaskService:
    """One project's task: inspect it, request an op, and wait at the edge."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        clock: Clock,
        ids: Ids,
        search: SearchFn,
        fetch: FetchFn,
        stage: StageFn,
        registry: TaskRegistry | None = None,
        lock: ProjectLock | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        wait_s: float = DEFAULT_WAIT_S,
        pick_wait_s: float = DEFAULT_PICK_WAIT_S,
        compose_wait_s: float = DEFAULT_COMPOSE_WAIT_S,
        poll_s: float = DEFAULT_POLL_S,
        compose: ComposePageFn | None = None,
        save: SavePageFn | None = None,
        page_locks: PageLocks | None = None,
        on_resume: TaskOpResumeFn | None = None,
        observed: ObservedRevFn | None = None,
        store: TaskStore | None = None,
    ) -> None:
        if wait_s < 0 or pick_wait_s < 0 or compose_wait_s < 0:
            raise ValueError("a bounded wait cannot be negative")
        self._layout = layout
        self._clock = clock
        self._ids = ids
        self._search = search
        self._fetch = fetch
        self._stage = stage
        self._registry = registry or TaskRegistry()
        self._lock = lock or ProjectLock(layout.task_lock, clock=clock)
        self._spawn = spawn or _spawn_thread
        self._wait_s = wait_s
        self._pick_wait_s = pick_wait_s
        self._compose_wait_s = compose_wait_s
        self._poll_s = poll_s
        self._compose = compose or TaskPageCompose()
        self._save = save or TaskPageSave()
        self._page_locks = page_locks or PageLocks()
        # What the executor calls when an op finished after its wait expired.
        # ``None`` leaves the op pending for a later trigger (startup recovery
        # or the route that owns the session).
        self._on_resume = on_resume
        # Where a restarted core recovers the revision a session last saw; the
        # in-process registry is the fast path that does not need it.
        self._observed = observed
        self._project_key = str(layout.root.resolve())
        self._owner = f"task-{id(self)}"
        # The one task file. A test hands in a store to watch or order its
        # reads; production uses the project's own.
        self._store = store or TaskStore(layout)

    @property
    def wait_s(self) -> float:
        """How long a search waits before it reports the op as still running."""
        return self._wait_s

    @property
    def pick_wait_s(self) -> float:
        """How long a pick waits before it reports the op as still running."""
        return self._pick_wait_s

    @property
    def compose_wait_s(self) -> float:
        """How long an approved compose waits before it says still running."""
        return self._compose_wait_s

    def inspect(
        self, view: str, *, session_id: str = ""
    ) -> tuple[dict[str, object], int]:
        """A bounded view and its rev; a missing task is made on first read."""
        if view not in TASK_VIEWS:
            raise ValueError(f"unknown task view: {view!r}")
        task = self._load()
        if task is None:
            with self._writing():
                task = self._load()
                if task is None:
                    task = self._create_locked()
        self._registry.observe(self._project_key, session_id, task.rev)
        return task_view(task, view), task.rev

    def overview(self) -> dict[str, object]:
        """Every bounded interface view from one task load, at one revision.

        The interface reads this instead of six separate views: a write landing
        between two of those reads would leave the panel a mix of two revisions.
        The model never asks for it; it calls ``inspect`` for one bounded view.
        """
        task = self._load()
        if task is None:
            with self._writing():
                task = self._load()
                if task is None:
                    task = self._create_locked()
        return task_overview(task)

    def _expected_rev(self, session_id: str) -> int | None:
        """The revision this session last saw, in memory or in its transcript.

        The registry is the fast path within one process. After a restart it is
        empty, so the last committed task tool result (whose card carried the
        revision the model read) is used: a person's change made while the core
        was down is then a ``conflict``, not a silent overwrite. A session that
        cannot be read keeps the old fallback, the task's current revision.
        """
        remembered = self._registry.observed(self._project_key, session_id)
        if remembered is not None:
            return remembered
        if not session_id or self._observed is None:
            return None
        try:
            return self._observed(session_id)
        except Exception as err:  # noqa: BLE001 - an unreadable session is not fatal
            logger.debug(
                "the observed task revision of session %s cannot be read: %s",
                session_id,
                err,
            )
            return None

    def search(
        self,
        query: str,
        *,
        session_id: str = "",
        cancel: Event | None = None,
        actor: str = "agent",
        expected_rev: int | None = None,
    ) -> SearchOutcome:
        """Write a ``search`` op, run the node behind it, and wait at most N s.

        The agent's wrapper names the revision its session last saw; a person's
        route names the revision of the card view they searched from, so a task
        that moved since then is a ``conflict`` rather than a search over it.
        """
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ValueError("a search needs a query")
        if actor not in ("agent", "human"):
            raise ValueError(f"unknown search actor: {actor!r}")
        if actor == "human" and expected_rev is None:
            raise ValueError("a human search names the revision it saw")
        expected = (
            expected_rev
            if expected_rev is not None
            else self._expected_rev(session_id)
        )
        op_id = self._request(
            kind="search",
            actor=actor,
            # The session travels on the op so the executor can resume it when
            # the search finishes after this tool has handed the turn back.
            # A person's search has no session: there is nothing to resume.
            input={"query": cleaned, "sessionId": session_id},
            expected_rev=expected,
            session_id=session_id,
        )
        observed = self._wait(
            op_id, f'the web search "{cleaned}"', cancel, wait_s=self._wait_s
        )
        observed = self._settle(
            op_id, f'the web search "{cleaned}"', observed
        )
        # A finished search moved the task; the session now knows that revision.
        self._registry.observe(self._project_key, session_id, observed.rev)
        cards: tuple[Card, ...] = ()
        if (
            observed.status == "done"
            and observed.task is not None
            and observed.op is not None
        ):
            ids = observed.op.result.get("cardIds")
            wanted = (
                {item for item in ids if isinstance(item, str)}
                if isinstance(ids, list)
                else set()
            )
            cards = tuple(card for card in observed.task.cards if card.id in wanted)
        return SearchOutcome(op_id, cleaned, observed.status, observed.rev, cards)

    def pick(
        self,
        card_id: str,
        *,
        session_id: str = "",
        expected_rev: int | None = None,
        actor: str = "agent",
        cancel: Event | None = None,
    ) -> PickOutcome:
        """Write a ``pick`` op and wait for the fetch and stage behind it.

        The agent names a card; the wrapper attaches the revision the session
        last saw. A person names the revision of the view they clicked from, so
        a change made since then is a ``conflict`` rather than a pick over it.
        """
        cleaned = (card_id or "").strip()
        if not cleaned:
            raise ValueError("a pick needs a card id")
        if actor not in ("agent", "human"):
            raise ValueError(f"unknown pick actor: {actor!r}")
        if actor == "human" and expected_rev is None:
            raise ValueError("a human pick names the revision it saw")
        expected = (
            expected_rev
            if expected_rev is not None
            else self._expected_rev(session_id)
        )
        op_id = self._request(
            kind="pick",
            actor=actor,  # type: ignore[arg-type]
            input={"cardId": cleaned, "sessionId": session_id},
            expected_rev=expected,
            session_id=session_id,
        )
        observed = self._wait(
            op_id,
            f'the pick of card "{cleaned}"',
            cancel,
            wait_s=self._pick_wait_s,
        )
        observed = self._settle(
            op_id, f'the pick of card "{cleaned}"', observed
        )
        self._registry.observe(self._project_key, session_id, observed.rev)
        material: Mapping[str, object] = {}
        if (
            observed.status == "done"
            and observed.task is not None
            and observed.op is not None
        ):
            material_id = observed.op.result.get("materialId")
            material = next(
                (
                    item
                    for item in observed.task.materials
                    if item.get("id") == material_id
                ),
                {},
            )
        return PickOutcome(op_id, cleaned, observed.status, observed.rev, material)

    def frame(
        self,
        doc_id: str,
        blocks: Sequence[object],
        *,
        session_id: str = "",
        expected_rev: int | None = None,
        actor: str = "agent",
    ) -> FrameOutcome:
        """Draw a frame on one material and write it straight into the task.

        A frame is pure state: no node runs behind it, so it is applied and
        logged under the task's single writer rather than spawned. The op is
        written with status ``done``, the revision moves once, and any approval
        the person had given is cleared because the writing basis changed.
        """
        cleaned_doc = (doc_id or "").strip()
        if not cleaned_doc:
            raise TaskBadSpec("a frame names the material it is drawn on")
        if actor not in ("agent", "human"):
            raise ValueError(f"unknown frame actor: {actor!r}")
        if actor == "human" and expected_rev is None:
            raise ValueError("a human frame names the revision it saw")
        wanted: list[str] = []
        for item in blocks:
            block = item.strip() if isinstance(item, str) else ""
            if not block:
                raise TaskBadSpec(
                    "a frame lists block ids, and one of them is blank or not "
                    "a string"
                )
            if block not in wanted:
                wanted.append(block)
        if not wanted:
            raise TaskBadSpec("a frame needs at least one block id")
        expected = (
            expected_rev
            if expected_rev is not None
            else self._expected_rev(session_id)
        )
        op_id = self._ids.new_id("op")
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._load() or self._create_locked()
            # A session that has not read the task yet names no revision; the
            # first mutation is accepted at the revision the task is at.
            expected = task.rev if expected is None else expected
            if expected != task.rev:
                raise TaskConflict(
                    "the writing task changed since this session last saw it "
                    f"(expected rev {expected}, now rev {task.rev}). "
                    "Do not retry blindly; call inspect and decide again."
                )
            material = next(
                (
                    item
                    for item in task.materials
                    if _text(item.get("id")) == cleaned_doc
                ),
                None,
            )
            if material is None:
                raise TaskNotFound(
                    f"the writing task has no material {cleaned_doc!r}. Do not "
                    'retry blindly; call inspect("materials") for the ids it '
                    "holds."
                )
            known = _material_block_ids(material)
            unknown = [block for block in wanted if block not in known]
            if unknown:
                shown = ", ".join(repr(block) for block in unknown[:5])
                raise TaskBadSpec(
                    f"block(s) {shown} do not belong to material "
                    f"{cleaned_doc!r}. Do not retry blindly; call "
                    'inspect("materials") for the ids it holds.'
                )
            order = {block: index for index, block in enumerate(known)}
            ordered = tuple(sorted(wanted, key=order.__getitem__))
            existing = next(
                (
                    frame
                    for frame in task.frames
                    if frame.doc_id == cleaned_doc
                ),
                None,
            )
            frame = Frame(
                id=existing.id
                if existing is not None
                else self._ids.new_id("frame"),
                doc_id=cleaned_doc,
                blocks=ordered,
                source=actor,
            )
            frames = tuple(
                frame if item.doc_id == cleaned_doc else item
                for item in task.frames
            )
            if existing is None:
                frames = frames + (frame,)
            # Any frame write changes the writing basis, so a person's earlier
            # go-ahead no longer covers what would be written, and a request
            # that named the old frame cannot be decided either.
            approval_cleared = task.approval is not None
            request_cleared = task.request is not None
            op = Op(
                id=op_id,
                kind="frame",
                actor=actor,  # type: ignore[arg-type]
                status="done",
                input={"docId": cleaned_doc, "blocks": list(wanted)},
                expected_rev=expected,
                created_at=now,
                updated_at=now,
                result={
                    "frameId": frame.id,
                    "docId": cleaned_doc,
                    "blocks": list(ordered),
                    "approvalCleared": approval_cleared,
                    "requestCleared": request_cleared,
                },
            )
            saved = replace(
                task,
                frames=frames,
                ops=task.ops + (op,),
                approval=None if approval_cleared else task.approval,
                request=None if request_cleared else task.request,
                status=(
                    "idle"
                    if (approval_cleared or request_cleared)
                    and task.status == "needs_approval"
                    else task.status
                ),
                rev=task.rev + 1,
                updated_at=now,
            )
            self._store.save(saved)
            self._registry.observe(self._project_key, session_id, saved.rev)
        return FrameOutcome(
            op_id=op_id,
            doc_id=cleaned_doc,
            status="done",
            rev=saved.rev,
            frame=frame,
            approval_cleared=approval_cleared,
        )

    def record_page_edit(
        self, path: str, revision: str = ""
    ) -> EditOutcome:
        """Record a person's real page save as a terminal human ``edit`` op.

        The page's own save has already succeeded when this runs: this is a
        record, not a second write, so it never touches the page or the task's
        ``result`` (which is the agent's last page write). Under the task's
        writer lock it moves the revision and clears a standing approval or a
        pending write request, because the page a later write would compose is
        no longer the one the person approved. The op is appended as ``done``:
        there is no node behind it and no executor claims it.
        """
        cleaned = (path or "").strip()
        if not cleaned:
            raise ValueError("a page edit names the page")
        digest = (revision or "").strip()
        op_id = self._ids.new_id("op")
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._load() or self._create_locked()
            approval_cleared = task.approval is not None
            request_cleared = task.request is not None
            op = Op(
                id=op_id,
                kind="edit",
                actor="human",
                status="done",
                input={"path": cleaned[:500], "revision": digest[:120]},
                expected_rev=task.rev,
                created_at=now,
                updated_at=now,
                result={
                    "path": cleaned[:500],
                    "revision": digest[:120],
                    "approvalCleared": approval_cleared,
                    "requestCleared": request_cleared,
                },
            )
            saved = replace(
                task,
                ops=task.ops + (op,),
                approval=None,
                request=None,
                # Clearing the pending request ends the waiting-for-approval
                # state; the task is idle again. Nothing else of its status is
                # rewritten by a page edit.
                status=(
                    "idle"
                    if (approval_cleared or request_cleared)
                    and task.status == "needs_approval"
                    else task.status
                ),
                rev=task.rev + 1,
                updated_at=now,
            )
            self._store.save(saved)
        return EditOutcome(
            op_id=op_id,
            path=cleaned,
            revision=digest,
            rev=saved.rev,
            approval_cleared=approval_cleared,
            request_cleared=request_cleared,
        )

    def compose(
        self,
        frame_id: str,
        spec: object,
        *,
        session_id: str = "",
        expected_rev: int | None = None,
        cancel: Event | None = None,
    ) -> ComposeOutcome:
        """Ask the gate whether this frame and spec may be written.

        A valid spec with no matching approval is recorded as the task's pending
        request and answered ``needs_approval``: no op is written and no page is
        touched. A matching approval writes one ``write`` op and waits at most
        ``compose_wait_s`` for the executor: a finished write answers with the
        page's fate and the new revision, a slow one with requested/running.
        *expected_rev* is the revision the session last saw; it is compared with
        the approval's binding, never trusted as a write token.
        """
        cleaned = (frame_id or "").strip()
        if not cleaned:
            raise TaskBadSpec(
                "compose_page needs the frame it writes from. Do not retry "
                'blindly; call inspect("frames") for its id.'
            )
        try:
            canonical = canonical_page_spec(spec)
            wiki_path = page_path_for_spec(canonical)
            # The page identity the person approved must stay inside the wiki.
            # Resolving it here answers a traversal or a program-owned page
            # before any request or op is recorded.
            resolve_wiki_write_path(self._layout, wiki_path)
        except ValueError as err:
            raise TaskBadSpec(str(err)) from err
        # The wrapper names the revision the session last saw; a caller with no
        # observation is accepted at the revision the task is at, like a frame.
        expected = (
            expected_rev
            if expected_rev is not None
            else self._expected_rev(session_id)
        )
        now = iso_timestamp(self._clock)
        op_id = ""
        with self._writing():
            task = self._load() or self._create_locked()
            expected = task.rev if expected is None else expected
            if expected != task.rev:
                raise TaskConflict(
                    "the writing task changed since this session last saw it "
                    f"(expected rev {expected}, now rev {task.rev}). Do not "
                    "retry blindly; call inspect and decide again."
                )
            frame = next(
                (item for item in task.frames if item.id == cleaned), None
            )
            if frame is None:
                raise TaskNotFound(
                    f"the writing task has no frame {cleaned!r}. Do not retry "
                    'blindly; call inspect("frames") for the ids it holds.'
                )
            known = set(frame.blocks)
            unknown: list[str] = []
            for section in canonical["sections"]:
                raw_blocks = section.get("blocks")
                for block in raw_blocks if isinstance(raw_blocks, list) else []:
                    if isinstance(block, str) and block not in known and block not in unknown:
                        unknown.append(block)
            if unknown:
                shown = ", ".join(repr(block) for block in unknown[:5])
                raise TaskBadSpec(
                    f"block(s) {shown} are not in frame {cleaned!r}. Do not "
                    'retry blindly; call inspect("frames") for the blocks that '
                    "frame covers."
                )
            # The gate: an approval covers exactly this frame, this canonical
            # spec, at exactly this revision. A match is the one door to a
            # write op; nothing else here can start a page.
            if _approval_covers(
                task.approval, cleaned, canonical, task.rev
            ):
                op = Op(
                    id=self._ids.new_id("op"),
                    kind="write",
                    actor="agent",
                    status="requested",
                    input={
                        "frameId": cleaned,
                        "spec": canonical,
                        # The binding the executor re-checks under the writer
                        # lock after the long compose, not just the frame/spec:
                        # any task write since this approval makes it stale.
                        "approvalRev": task.rev,
                        "sessionId": session_id,
                    },
                    expected_rev=task.rev,
                    created_at=now,
                    updated_at=now,
                )
                # Requesting the write does not move the revision: the op is
                # the record, and the approval must stay valid until the save.
                self._store.save(
                    replace(task, ops=task.ops + (op,), updated_at=now)
                )
                self._registry.observe(
                    self._project_key, session_id, task.rev
                )
                op_id = op.id
            else:
                # A standing approval did not match this request (or there is
                # none). It no longer covers what would be written, so it is
                # cleared rather than left to look usable.
                approval_cleared = task.approval is not None
                request = WriteRequest(
                    frame_id=cleaned,
                    spec=canonical,
                    rev=task.rev + 1,
                    at=now,
                )
                saved = replace(
                    task,
                    status="needs_approval",
                    approval=None,
                    request=request,
                    rev=task.rev + 1,
                    updated_at=now,
                )
                self._store.save(saved)
                self._registry.observe(
                    self._project_key, session_id, saved.rev
                )
                return ComposeOutcome(
                    status="needs_approval",
                    rev=saved.rev,
                    frame_id=cleaned,
                    spec=canonical,
                    request=request,
                    approval_cleared=approval_cleared,
                )
        # The long part runs outside the task lock; the executor is the only
        # writer from here on, and the handler only waits at the boundary.
        self._spawn(lambda: self._run_op(op_id))
        observed = self._wait(
            op_id,
            f'the write of page "{canonical["slug"]}"',
            cancel,
            wait_s=self._compose_wait_s,
        )
        observed = self._settle(
            op_id, f'the write of page "{canonical["slug"]}"', observed
        )
        self._registry.observe(self._project_key, session_id, observed.rev)
        if observed.status != "done" or observed.op is None:
            return ComposeOutcome(
                status=observed.status,
                rev=observed.rev,
                frame_id=cleaned,
                spec=canonical,
                op_id=op_id,
            )
        return ComposeOutcome(
            status=_text(observed.op.result.get("status")) or "failed",
            rev=observed.rev,
            frame_id=cleaned,
            spec=canonical,
            op_id=op_id,
            result=_public_write_result(observed.op.result),
        )

    def approve(
        self,
        task_id: str,
        rev: int,
        frame_id: str,
        spec: object,
        decision: str,
        *,
        by: str = "human",
    ) -> ApproveOutcome:
        """Record a person's decision on the pending write request.

        This is the one transition only a person may make. It refuses anything
        but the request that is pending at the current revision: a stale
        revision, another frame, another spec or no request at all is answered
        with ``conflict``/``needs_approval`` and changes nothing. An approval is
        written with the revision the decision creates, so the next task write
        makes it stale by construction.
        """
        cleaned_task = (task_id or "").strip()
        cleaned_frame = (frame_id or "").strip()
        if decision not in ("approve", "reject"):
            raise ValueError(f"unknown decision: {decision!r}")
        try:
            canonical = canonical_page_spec(spec)
        except ValueError as err:
            raise TaskBadSpec(str(err)) from err
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._load()
            if task is None or task.id != cleaned_task:
                raise TaskConflict(
                    "there is no active writing task with that id; read the "
                    "task again before deciding"
                )
            if rev != task.rev:
                raise TaskConflict(
                    "the writing task changed since this view was read "
                    f"(expected rev {rev}, now rev {task.rev}). Re-read the "
                    "request before deciding."
                )
            request = task.request
            if request is None:
                raise TaskNeedsApproval(
                    "there is no pending write request to decide on; the agent "
                    "must call compose_page again"
                )
            if request.rev != task.rev or request.frame_id != cleaned_frame:
                raise TaskNeedsApproval(
                    "the pending write request changed; re-read it before "
                    "deciding"
                )
            if not _same_page_spec(request.spec, canonical):
                raise TaskNeedsApproval(
                    "the pending write request proposes a different spec; "
                    "re-read it before deciding"
                )
            frame = next(
                (item for item in task.frames if item.id == cleaned_frame),
                None,
            )
            if frame is None:
                raise TaskNotFound(
                    f"the writing task has no frame {cleaned_frame!r}; re-read "
                    "the task before deciding"
                )
            approved_digest = ""
            if decision == "approve":
                try:
                    # The page the person is approving against, when it exists.
                    # A later writer compares this before saving, so an edit
                    # made after the approval is kept even if the task could
                    # not record that edit and clear this go-ahead.
                    approved_digest = _page_digest(self._layout, canonical)
                except ValueError as err:
                    raise TaskBadSpec(str(err)) from err
            approval = (
                Approval(
                    frame_id=cleaned_frame,
                    spec=canonical,
                    rev=task.rev + 1,
                    by=by,
                    at=now,
                    page_digest=approved_digest,
                )
                if decision == "approve"
                else None
            )
            op = Op(
                id=self._ids.new_id("op"),
                kind="approve",
                actor="human",
                status="done",
                input={
                    "frameId": cleaned_frame,
                    "spec": canonical,
                    "decision": decision,
                },
                expected_rev=task.rev,
                created_at=now,
                updated_at=now,
                result={"decision": decision, "frameId": cleaned_frame},
            )
            saved = replace(
                task,
                status="idle",
                approval=approval,
                request=None,
                ops=task.ops + (op,),
                rev=task.rev + 1,
                updated_at=now,
            )
            self._store.save(saved)
        return ApproveOutcome(decision, saved.rev, approval)

    def claim_resume(self, op_id: str) -> OpResumeNotice | None:
        """Set the once-only resume marker and return the op's delta, or none.

        The marker and the delta are read and written under the task's writer
        lock, before the continuation run starts. A duplicate completion, a
        second app instance or a reconnect therefore finds ``resume_started``
        and starts nothing; a cancelled op never resumes at all.
        """
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None:
                return None
            if op.resume_started or not op.resume_requested:
                return None
            if op.status == "cancelled" or op.error_code == "cancelled":
                return None
            if op.status not in TERMINAL_OP_STATUSES:
                return None
            session_id = _input_text(op.input, "sessionId")
            if not session_id:
                return None
            now = iso_timestamp(self._clock)
            started = replace(op, resume_started=True, updated_at=now)
            saved = replace(
                task,
                ops=_replace_op(task, started),
                updated_at=now,
            )
            self._store.save(saved)
            # The resumed run starts from the delta's revision: the revision the
            # op completed at, which the op recorded. Falling back to the task's
            # current revision keeps an older op (one finished before the record
            # carried it) resumable with the behaviour it was written under.
            rev = started.resume_rev or saved.rev
            self._registry.observe(self._project_key, session_id, rev)
            return OpResumeNotice(
                session_id=session_id,
                op_id=started.id,
                kind=started.kind,
                status=started.status,
                rev=rev,
                result=op_delta(saved, started),
            )

    def resume_pending(self, op_id: str) -> bool:
        """Whether this op still wants its one continuation (read only)."""
        task = self._load()
        op = task.op(op_id) if task is not None else None
        if task is None or op is None:
            return False
        if not op.resume_requested or op.resume_started:
            return False
        if op.status not in TERMINAL_OP_STATUSES:
            return False
        if op.status == "cancelled" or op.error_code == "cancelled":
            return False
        return bool(_input_text(op.input, "sessionId"))

    def record_resume_error(self, op_id: str, message: str) -> None:
        """Leave why a claimed resume could not run on the op; never retry.

        The op is already terminal and keeps its result; this only makes the
        failure (a deleted session, an unreadable model configuration) visible
        in the task the person and the agent read.
        """
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None:
                return
            now = iso_timestamp(self._clock)
            failed = replace(
                op,
                resume_error=(message or "the resume could not start")[:600],
                updated_at=now,
            )
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(task, failed),
                    updated_at=now,
                )
            )

    def cancel_op(self, op_id: str) -> OpOutcome:
        """Stop an unfinished op for good.

        The terminal state is written under the writer lock before the per-op
        stop signal is raised, so a late node cannot write its result back: the
        executor's finishing guard sees ``cancelled`` and drops it. A finished
        op is left as it is (a cancel that lost the race is not an error).
        """
        changed = False
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None:
                raise TaskNotFound(
                    f"the writing task has no op {op_id!r}. Read the task "
                    "again before cancelling it."
                )
            if op.status not in ACTIVE_OP_STATUSES:
                return OpOutcome(op, task.rev, changed=False)
            now = iso_timestamp(self._clock)
            cancelled = replace(
                op,
                status="cancelled",
                error="the op was cancelled by a person",
                error_code="cancelled",
                updated_at=now,
            )
            saved = replace(
                task,
                ops=_replace_op(task, cancelled),
                updated_at=now,
            )
            self._store.save(saved)
            changed = True
        if changed:
            # After the state write: an executor that checks the signal and
            # then its op sees cancelled either way.
            self._registry.cancel_op(op_id)
        return OpOutcome(cancelled, saved.rev, changed=True)

    def resume_op(self, op_id: str) -> OpOutcome:
        """Run an op a restart interrupted, and let its completion resume.

        ``interrupted`` is cleared and ``resume_requested`` set under the writer
        lock, so the new executor carries the same once-only contract as a tool
        that timed out: when it finishes it resumes the session once. Resuming
        a write re-runs its compose but the save node's effect journal makes the
        page land once.
        """
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None:
                raise TaskNotFound(
                    f"the writing task has no op {op_id!r}. Read the task "
                    "again before resuming it."
                )
            if op.status == "cancelled" or op.error_code == "cancelled":
                raise TaskConflict(
                    "the op was cancelled and will not run again. Do not retry "
                    "it; read the task as it stands."
                )
            if op.status not in ACTIVE_OP_STATUSES:
                raise TaskConflict(
                    "the op already finished; there is nothing to resume. Read "
                    "its result in the task."
                )
            if not op.interrupted:
                raise TaskConflict(
                    "the op still has an executor and is not interrupted; do "
                    "not resume it twice."
                )
            now = iso_timestamp(self._clock)
            resumed = replace(
                op,
                interrupted=False,
                resume_requested=True,
                # A new attempt replaces the record of an earlier failed one.
                resume_error="",
                updated_at=now,
            )
            saved = replace(
                task,
                ops=_replace_op(task, resumed),
                updated_at=now,
            )
            self._store.save(saved)
        self._spawn(lambda: self._run_op(op_id))
        return OpOutcome(resumed, saved.rev, changed=True)

    def recover_ops(self) -> tuple[OpResumeNotice, ...]:
        """Reconcile a restarted task and return the completions still owed.

        An op that was active has no executor now: it is marked ``interrupted``
        so the panel can offer resume or cancel, and it is never retried by
        itself. An op that finished while the process was gone but never started
        its continuation is returned as a notice; the caller hands each to the
        resumer, which sets the once-only marker before running it. A cancelled
        op is terminal and is left alone.
        """
        notices: list[OpResumeNotice] = []
        with self._writing():
            task = self._load()
            if task is None:
                return ()
            now = iso_timestamp(self._clock)
            ops = list(task.ops)
            changed = False
            for index, op in enumerate(ops):
                if op.status in ACTIVE_OP_STATUSES and not op.interrupted:
                    ops[index] = replace(
                        op, interrupted=True, updated_at=now
                    )
                    changed = True
            saved = (
                replace(task, ops=tuple(ops), updated_at=now)
                if changed
                else task
            )
            if changed:
                self._store.save(saved)
            for op in saved.ops:
                if op.status not in TERMINAL_OP_STATUSES:
                    continue
                if op.status == "cancelled" or op.error_code == "cancelled":
                    continue
                if not op.resume_requested or op.resume_started:
                    continue
                if not _input_text(op.input, "sessionId"):
                    continue
                notices.append(
                    OpResumeNotice(
                        session_id=_input_text(op.input, "sessionId"),
                        op_id=op.id,
                        kind=op.kind,
                        status=op.status,
                        # The completion's revision, like claim_resume: a write
                        # made while the process was down must not look seen.
                        rev=op.resume_rev or saved.rev,
                        result=op_delta(saved, op),
                    )
                )
        return tuple(notices)

    def deliver_resume(self, notice: OpResumeNotice) -> None:
        """Hand a completion found at startup to the session's resumer."""
        self._notify(notice)

    def _request(
        self,
        *,
        kind: str,
        actor: str,
        input: Mapping[str, object],
        expected_rev: int | None,
        session_id: str,
    ) -> str:
        """Write one typed op, after refusing a revision the caller never saw."""
        op_id = self._ids.new_id("op")
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._load() or self._create_locked()
            expected = task.rev if expected_rev is None else expected_rev
            if expected != task.rev:
                raise TaskConflict(
                    "the writing task changed since this session last saw it "
                    f"(expected rev {expected}, now rev {task.rev}). "
                    "Do not retry blindly; call inspect and decide again."
                )
            op = Op(
                id=op_id,
                kind=kind,  # type: ignore[arg-type]
                actor=actor,  # type: ignore[arg-type]
                status="requested",
                input=dict(input),
                expected_rev=expected,
                created_at=now,
                updated_at=now,
            )
            self._store.save(
                replace(task, ops=task.ops + (op,), updated_at=now)
            )
            self._registry.observe(self._project_key, session_id, expected)
        self._spawn(lambda: self._run_op(op_id))
        return op_id

    def _run_op(self, op_id: str) -> None:
        """The executor's side: claim, run the typed node(s), write the answer."""
        cancel = self._registry.op_cancel(op_id)
        try:
            _check_op_cancel(cancel)
            claimed = self._claim(op_id)
            if claimed is None:
                return
            try:
                answer = self._execute(*claimed, cancel)
            except _OpFailure as err:
                self._finish_failed(op_id, err.code, str(err))
                return
            except Exception as err:  # noqa: BLE001 - the op records the failure
                self._finish_failed(
                    op_id, "op_failed", f"{type(err).__name__}: {err}"
                )
                return
            self._finish_done(op_id, answer)
        except Exception as err:  # noqa: BLE001 - a writer error must not vanish
            try:
                self._finish_failed(
                    op_id, "op_failed", f"{type(err).__name__}: {err}"
                )
            except Exception:
                pass
        finally:
            self._registry.forget_op(op_id)

    def _execute(
        self, task: Task, op: Op, cancel: Event | None
    ) -> "_OpAnswer":
        """Dispatch one claimed op to its pure node(s). No task writes here."""
        if op.kind == "search":
            query = _input_text(op.input, "query")
            _check_op_cancel(cancel)
            try:
                cards = list(self._search(query))
            except Exception as err:  # noqa: BLE001 - named in the op
                raise _OpFailure(
                    "op_failed", f"{type(err).__name__}: {err}"
                ) from err
            _check_op_cancel(cancel)
            return _OpAnswer("search", {"hits": len(cards)}, cards=tuple(cards))
        if op.kind == "pick":
            card_id = _input_text(op.input, "cardId")
            card = next((item for item in task.cards if item.id == card_id), None)
            if card is None:
                raise _OpFailure(
                    "not_found",
                    f"the writing task has no card {card_id!r}; call "
                    'inspect("cards") for the ids it holds',
                )
            _check_op_cancel(cancel)
            capture = self._fetch(card.url)
            _check_op_cancel(cancel)
            if capture.fidelity == FIDELITY_FAILED:
                raise _OpFailure(
                    "op_failed",
                    f"the page at {card.url} failed the quality gate and was "
                    "not staged",
                )
            staged = self._stage(capture, task.id)
            _check_op_cancel(cancel)
            row = _material_row(staged, run_id=task.id)
            return _OpAnswer(
                "pick",
                {
                    "materialId": row["id"],
                    "title": row["title"],
                    "fidelity": row["fidelity"],
                    "blocks": row["blockCount"],
                },
                material=row,
            )
        if op.kind == "write":
            return self._execute_write(task, op, cancel)
        raise _OpFailure("op_failed", f"unknown op kind: {op.kind!r}")

    def _execute_write(
        self, task: Task, op: Op, cancel: Event | None
    ) -> "_OpAnswer":
        """Compose the approved page, then save it if the approval still holds.

        The compose is the long part; nothing is written until it returns. The
        save runs under the task's writer lock, re-reading the approval binding
        and comparing the page against the digest captured before composing, so
        a person's edit during the compose wins and an approval that went stale
        while the model was working does not write a page.
        """
        frame_id = _input_text(op.input, "frameId")
        session_id = _input_text(op.input, "sessionId")
        approval_rev = _input_count(op.input.get("approvalRev"))
        try:
            spec = canonical_page_spec(op.input.get("spec"))
            wiki_path = page_path_for_spec(spec)
            absolute = resolve_wiki_write_path(self._layout, wiki_path)
        except ValueError as err:
            raise _OpFailure("bad_spec", str(err)) from err
        frame = next((item for item in task.frames if item.id == frame_id), None)
        if frame is None:
            raise _OpFailure(
                "not_found",
                f"the writing task has no frame {frame_id!r} any more; "
                'call inspect("frames") for the ids it holds',
            )
        material = next(
            (
                item
                for item in task.materials
                if _text(item.get("id")) == frame.doc_id
            ),
            None,
        )
        if material is None:
            raise _OpFailure(
                "not_found",
                f"the writing task has no material {frame.doc_id!r} any more; "
                "the frame cannot be composed from it",
            )
        action = _text(spec.get("action"))
        exists = absolute.is_file()
        if action == "create" and exists:
            return self._page_answer(
                spec,
                wiki_path,
                status="skipped",
                summary=(
                    f"skipped {wiki_path}: the page already exists, and a "
                    "create does not overwrite it. Use action update."
                ),
                reason=(
                    "the page already exists, and a create does not "
                    "overwrite it"
                ),
            )
        if action == "update" and not exists:
            return self._page_answer(
                spec,
                wiki_path,
                status="skipped",
                summary=(
                    f"skipped {wiki_path}: the page does not exist, and an "
                    "update has nothing to change. Use action create."
                ),
                reason=(
                    "the page does not exist, and an update has nothing to "
                    "change"
                ),
            )
        before = absolute.read_text(encoding="utf-8") if exists else ""
        base_digest = blob_digest(before) if exists else ""
        request = ComposeRequest(
            task_id=task.id,
            intent=task.intent.strip() or _text(spec.get("title")),
            slug=_text(spec.get("slug")),
            page_name=_text(spec.get("title")),
            action=action,
            wiki_path=wiki_path,
            material=frame.doc_id,
            blocks=tuple(frame.blocks),
            locators=locators_for_frame(frame.doc_id, frame.blocks),
            spec=spec,
        )
        _check_op_cancel(cancel)
        try:
            # The compose node holds the same stop signal: a cancelled write
            # stops inside the long model run, not only after it.
            block = self._compose(self._layout, request, cancel)
        except Exception as err:  # noqa: BLE001 - named on the op
            raise _OpFailure(
                "op_failed", f"the page could not be composed: {err}"
            ) from err
        # Composing is the long part; a person may have cancelled it while the
        # model was working. Nothing is saved in that case.
        _check_op_cancel(cancel)
        after = ""
        with self._writing():
            _check_op_cancel(cancel)
            current = self._load()
            if (
                current is None
                or current.rev != approval_rev
                or not _approval_covers(
                    current.approval, frame_id, spec, current.rev
                )
            ):
                raise _OpFailure(
                    "conflict",
                    "the writing task changed while the page was being "
                    "composed, so the approval no longer covers it. Nothing "
                    "was written. Do not retry blindly; call inspect and ask "
                    "the person to approve again.",
                )
            try:
                # The digest is read and compared under the same page lock the
                # human editor takes, so a save that lands after the compose but
                # before this point is seen rather than overwritten.
                with self._page_locks.holding(self._project_key, wiki_path):
                    # Last checkpoint before the page lands: the cancel route
                    # sets the op terminal before it raises this signal.
                    _check_op_cancel(cancel)
                    now_text = (
                        absolute.read_text(encoding="utf-8")
                        if absolute.is_file()
                        else ""
                    )
                    now_digest = (
                        blob_digest(now_text) if absolute.is_file() else ""
                    )
                    if now_digest != base_digest:
                        raise _OpFailure(
                            "conflict",
                            f"the page {wiki_path} changed while it was being "
                            "composed. The person's edit was kept and nothing "
                            "was written. Do not retry blindly; call inspect "
                            "and read the page again.",
                        )
                    # The approval itself names the page it was given against.
                    # A page that changed since then (a person's edit, even one
                    # the task could not record) is not the page the person
                    # approved, so it is never overwritten.
                    approved_digest = (
                        current.approval.page_digest
                        if current.approval is not None
                        else ""
                    )
                    if approved_digest and now_digest != approved_digest:
                        raise _OpFailure(
                            "conflict",
                            f"the page {wiki_path} changed since the person "
                            "approved this write. The person's edit was kept "
                            "and nothing was written. Do not retry blindly; "
                            "call inspect and ask the person to approve the "
                            "page as it now stands.",
                        )
                    outcome = self._save(
                        self._layout,
                        SaveRequest(
                            task_id=task.id,
                            op_id=op.id,
                            session_id=session_id,
                            wiki_path=wiki_path,
                            action=action,
                            content=block.content,
                            material=frame.doc_id,
                            locators=request.locators,
                            expected_rev=approval_rev,
                        ),
                        cancel,
                    )
                    # The diff is read before the lock is released, so a later
                    # person's edit is never reported as part of this write.
                    after = (
                        absolute.read_text(encoding="utf-8")
                        if absolute.is_file()
                        else ""
                    )
            except PageBusy as err:
                raise _OpFailure(
                    "conflict",
                    f"{err}. A person is writing this page; nothing was "
                    "written. Do not retry blindly; read the page again before "
                    "deciding whether to compose.",
                ) from err
        return self._page_answer(
            spec,
            wiki_path,
            status=outcome.status,
            summary=outcome.summary,
            reason=outcome.reason,
            written=outcome.written,
            skipped=outcome.skipped,
            failed=outcome.failed,
            effect_ids=outcome.effect_ids,
            before=before,
            after=after,
        )

    def _page_answer(
        self,
        spec: Mapping[str, object],
        wiki_path: str,
        *,
        status: str,
        summary: str,
        reason: str = "",
        written: tuple[str, ...] = (),
        skipped: tuple[tuple[str, str], ...] = (),
        failed: tuple[tuple[str, str], ...] = (),
        effect_ids: tuple[str, ...] = (),
        before: str = "",
        after: str = "",
    ) -> "_OpAnswer":
        """One page's fate: the op's record (with effect ids) and the task's."""
        added, removed = diff_counts(before, after)
        result: dict[str, object] = {
            "status": status,
            "page": wiki_path,
            "slug": _text(spec.get("slug")),
            "title": _text(spec.get("title")),
            "action": _text(spec.get("action")),
            "summary": summary,
            "reason": reason,
            "written": list(written),
            "skipped": [
                {"path": path, "reason": text} for path, text in skipped
            ],
            "failed": [
                {"path": path, "reason": text} for path, text in failed
            ],
            "effectIds": list(effect_ids),
            "diff": page_diff(before, after),
            "added": added,
            "removed": removed,
        }
        # The task result is the reader's record of the page; the effect ids are
        # the op's own business (restore's, later) and never travel with it.
        task_result = {
            key: value
            for key, value in result.items()
            if key != "effectIds"
        }
        return _OpAnswer("write", result, task_result=task_result)

    def _claim(self, op_id: str) -> tuple[Task, Op] | None:
        """Move the op to running; ``None`` when it is no longer claimable."""
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return None
            now = iso_timestamp(self._clock)
            # Claiming an op is also how an interrupted op is resumed: the new
            # executor owns it now, so the restart mark no longer applies.
            claimed = replace(
                op, status="running", interrupted=False, updated_at=now
            )
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(task, claimed),
                    updated_at=now,
                )
            )
            return task, claimed

    def _finish_done(self, op_id: str, answer: "_OpAnswer") -> None:
        notice: OpResumeNotice | None = None
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return
            now = iso_timestamp(self._clock)
            cards = task.cards
            materials = task.materials
            result = dict(answer.result)
            if answer.kind == "search":
                new_cards = tuple(
                    Card(
                        id=self._ids.new_id("card"),
                        title=_text(card.title) or card.url,
                        url=_text(card.url),
                        snippet=_text(card.snippet),
                        site=_text(card.site),
                    )
                    for card in answer.cards
                    if _text(card.url)
                )
                cards = task.cards + new_cards
                result["cardIds"] = [card.id for card in new_cards]
            elif answer.material is not None:
                materials = _upsert_material(task.materials, answer.material)
            done = replace(
                op,
                status="done",
                result=result,
                # A finish that owes a resume records the revision it created:
                # the continuation must observe the completion, even if a person
                # writes while it is queued behind the session's running turn.
                resume_rev=task.rev + 1 if op.resume_requested else op.resume_rev,
                updated_at=now,
            )
            # Finishing an op moves the revision. An approval or a pending
            # request is bound to the old revision, so it no longer stands: a
            # person's go-ahead must not cover a task they did not approve.
            cleared = task.approval is not None or task.request is not None
            task_result = task.result
            status = (
                "idle"
                if cleared and task.status == "needs_approval"
                else task.status
            )
            if answer.kind == "write":
                # The page's fate is the task's result now: written, skipped or
                # failed, with the identity and a bounded summary/diff. A failed
                # page write is a failed task; a skip still finished the task.
                task_result = dict(answer.task_result or result)
                status = (
                    "failed" if task_result.get("status") == "failed" else "done"
                )
            saved = replace(
                task,
                cards=cards,
                materials=materials,
                ops=_replace_op(task, done),
                approval=None if cleared else task.approval,
                request=None if cleared else task.request,
                result=task_result,
                status=status,
                rev=task.rev + 1,
                updated_at=now,
            )
            self._store.save(saved)
            notice = self._resume_notice(saved, done)
        # The continuation starts outside the task lock: it loads the session
        # and asks the runtime for a run, and neither may hold this writer.
        if notice is not None:
            self._notify(notice)

    def _finish_failed(self, op_id: str, code: str, error: str) -> None:
        notice: OpResumeNotice | None = None
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return
            now = iso_timestamp(self._clock)
            result = task.result
            status = task.status
            # A failed write rewrites the task's result and status, so it is a
            # state write and moves the revision, exactly like a finished one.
            # A failed search or pick only changes the op record, which is not
            # task state: it keeps the no-bump semantics of an op-only
            # transition, so a wait that ends in failure does not look like a
            # task change to the views.
            changes_task = op.kind == "write"
            completion_rev = task.rev + 1 if changes_task else task.rev
            failed = replace(
                op,
                # A cancelled op is terminal under its own name: even if the
                # executor noticed the person's signal before the route wrote
                # the state, the op must not read as a retryable failure.
                status="cancelled" if code == "cancelled" else "failed",
                error=error[:1000],
                error_code=code,
                # An owed resume observes the failure at the revision it wrote,
                # not at whatever the task has moved to when it is claimed.
                resume_rev=(
                    completion_rev if op.resume_requested else op.resume_rev
                ),
                updated_at=now,
            )
            # A revision move makes any approval or pending request stale by
            # construction; like a finished op, the failure clears them rather
            # than leaving a go-ahead that no longer covers the task.
            cleared = changes_task and (
                task.approval is not None or task.request is not None
            )
            if changes_task:
                # A compose or save that failed still leaves the attempt on the
                # task: the reader sees the page it aimed at and why it is not
                # there. The op carries the same failure for the agent.
                result = _write_failure_result(op, code, error)
                status = "failed"
            saved = replace(
                task,
                ops=_replace_op(task, failed),
                approval=None if cleared else task.approval,
                request=None if cleared else task.request,
                result=result,
                status=status,
                rev=completion_rev,
                updated_at=now,
            )
            self._store.save(saved)
            notice = self._resume_notice(saved, failed)
        if notice is not None:
            self._notify(notice)

    def _resume_notice(
        self, task: Task, op: Op
    ) -> OpResumeNotice | None:
        """The continuation a finished op asks for, or ``None``.

        Only an op whose wait expired asks; the session it names must exist in
        the op's input. The agent's next observation is the delta's revision, so
        a change made after it is a ``conflict`` rather than a silent overwrite.
        The once-only marker is not set here: the resumer sets it under the
        writer lock immediately before the run, and a cancelled op never asks.
        """
        session_id = _input_text(op.input, "sessionId")
        if not session_id:
            return None
        self._registry.observe(self._project_key, session_id, task.rev)
        if op.status == "cancelled" or op.error_code == "cancelled":
            return None
        if not op.resume_requested or op.resume_started:
            return None
        return OpResumeNotice(
            session_id=session_id,
            op_id=op.id,
            kind=op.kind,
            status=op.status,
            rev=task.rev,
            result=op_delta(task, op),
        )

    def _notify(self, notice: OpResumeNotice) -> None:
        """Hand a completion to the session's resumer, outside every lock."""
        if self._on_resume is None:
            return
        try:
            self._on_resume(notice)
        except Exception:  # noqa: BLE001 - the op is already saved and readable
            logger.exception(
                "the resume callback for op %s failed", notice.op_id
            )

    def _wait(
        self,
        op_id: str,
        what: str,
        cancel: Event | None,
        *,
        wait_s: float,
    ) -> "_Observed":
        """Wait for the op's boundary; a failure is raised, a wait is returned."""
        deadline = time.monotonic() + wait_s
        status = "requested"
        rev = 0
        task: Task | None = None
        op: Op | None = None
        while True:
            if cancel is not None and cancel.is_set():
                raise RunCancelled("the run was cancelled")
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is not None:
                rev = task.rev
            if op is not None:
                status = op.status
            if op is not None and op.status in ("failed", "cancelled"):
                raise self._failure(op, what)
            if op is not None and op.status == "done":
                return _Observed("done", rev, task, op)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _Observed(status, rev, task, op)
            time.sleep(min(self._poll_s, remaining))

    def _settle(
        self, op_id: str, what: str, observed: "_Observed"
    ) -> "_Observed":
        """Decide the wait/completion race under the task's writer lock.

        The tool timed out. If the op is still active, it is marked so the
        executor will carry the completion back to the session. If the executor
        completed between the deadline and this lock, the tool returns the real
        completion instead: a finished op is never resumed and never reported
        as still running. The marker is source of truth across a duplicate
        completion and a restart, so it lives on the op, not in memory.
        """
        if observed.status not in ACTIVE_OP_STATUSES:
            return observed
        with self._writing():
            task = self._load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None:
                return observed
            if op.status in ACTIVE_OP_STATUSES:
                if not op.resume_requested:
                    now = iso_timestamp(self._clock)
                    requested = replace(
                        op, resume_requested=True, updated_at=now
                    )
                    self._store.save(
                        replace(
                            task,
                            ops=_replace_op(task, requested),
                            updated_at=now,
                        )
                    )
                return _Observed(op.status, task.rev, task, op)
        # The executor won the race: report its answer, not a stale wait.
        return self._completion(task, op, what)

    def _completion(self, task: Task, op: Op, what: str) -> "_Observed":
        """The op's terminal state: its result, or the failure it names."""
        if op.status in ("failed", "cancelled"):
            raise self._failure(op, what)
        return _Observed(op.status, task.rev, task, op)

    def _failure(self, op: Op, what: str) -> ToolError:
        """The model-facing error for an op that ended without an answer."""
        if op.status == "cancelled":
            return TaskOpFailed(
                f"{what} was cancelled by a person and will not run again. "
                "Do not retry it; call inspect to read the task as it stands."
            )
        detail = op.error or "the node behind it failed"
        if op.error_code == "conflict":
            return TaskConflict(
                f"{what} did not write because the task or the page "
                f"changed: {detail}. conflict. Do not retry blindly; "
                "call inspect and read the page again."
            )
        if op.error_code == "bad_spec":
            return TaskBadSpec(
                f"{detail}. bad_spec. Do not retry blindly; call "
                "inspect and use a page name it can write."
            )
        if op.error_code == "not_found":
            return TaskNotFound(
                f"{detail}. not_found. Do not retry blindly; call "
                "inspect and use an id it shows."
            )
        return TaskOpFailed(
            f"{what} could not finish: {detail}. op_failed. "
            "Do not retry blindly; call inspect before deciding "
            "whether to try again."
        )

    def _load(self) -> Task | None:
        """The task, or ``None`` when none was made yet.

        An unreadable task file is an error, never an absent task: creating one
        over it would replace the person's state with an empty task. Every
        reader and writer here goes through this one check, and the file is
        left exactly as it was found.
        """
        try:
            return self._store.load()
        except TaskUnreadableError as err:
            raise TaskUnreadable(
                f"{err}. The task was left untouched and must be repaired, not "
                "replaced. Do not retry blindly; read the task file."
            ) from err

    def _create_locked(self) -> Task:
        now = iso_timestamp(self._clock)
        task = Task(
            id=self._ids.new_id("task"),
            created_at=now,
            updated_at=now,
        )
        self._store.save(task)
        return task

    @contextmanager
    def _writing(self) -> Iterator[None]:
        """One writer for the task file, in this process and across processes."""
        writer = self._registry.writer(self._project_key)
        with writer:
            try:
                self._lock.acquire(self._owner)
            except ProjectBusy as err:
                raise TaskConflict(
                    "another writer is changing this project; do not retry "
                    "blindly, try again after it finishes"
                ) from err
            try:
                yield
            finally:
                self._lock.release(self._owner)


class _OpFailure(RuntimeError):
    """A named failure the executor records on the op it was running."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _check_op_cancel(cancel: Event | None) -> None:
    """Stop at an executor checkpoint when a person cancelled the op."""
    if cancel is not None and cancel.is_set():
        raise _OpFailure(
            "cancelled", "the operation was cancelled by a person"
        )


@dataclass(frozen=True, slots=True)
class _OpAnswer:
    """What a finished op writes back, in the shape its kind produces.

    ``result`` is the op's own record; ``task_result`` is what the task keeps as
    its one-page result. They differ by the effect ids, which are restore's and
    never the task view's.
    """

    kind: str
    result: Mapping[str, object]
    cards: tuple[SearchCard, ...] = ()
    material: Mapping[str, object] | None = None
    task_result: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _Observed:
    """The task and op as they stood when the wait reached its boundary."""

    status: str
    rev: int
    task: Task | None
    op: Op | None


def _material_row(staged: StagedMaterial, *, run_id: str) -> dict[str, object]:
    """The task's record of one staged material, with a bounded block index.

    The source text stays in the staging area. What the task keeps is the
    identity and the id/preview pairs a frame will name later. ``blockIds`` is
    the full identity list: a view is bounded, but a frame may name any block
    of the material, including one past the preview cut.
    """
    material = staged.material.strip()
    block_ids = list(
        dict.fromkeys(
            block.id.strip() for block in staged.blocks if block.id.strip()
        )
    )
    blocks = tuple(staged.blocks[:MAX_VIEW_BLOCKS])
    count = max(staged.block_count, len(staged.blocks))
    return {
        "id": material,
        "title": (staged.title.strip() or material)[:MAX_MATERIAL_TITLE],
        "url": staged.url.strip(),
        "kind": staged.kind.strip(),
        "fidelity": staged.fidelity.strip(),
        "state": staged.state.strip() or "staged",
        "runId": run_id,
        "blockCount": count,
        "blockIds": block_ids,
        "blocks": [
            {
                "id": block.id.strip(),
                "tag": block.tag.strip(),
                "preview": block.preview.strip()[:MAX_BLOCK_PREVIEW],
            }
            for block in blocks
            if block.id.strip()
        ],
    }


def _material_block_ids(material: Mapping[str, object]) -> list[str]:
    """Every block id a frame may name, in document order.

    Newer task files carry the full ``blockIds`` list. An older or hand-seeded
    material only has the bounded preview index, so that is the fallback.
    """
    ids: list[str] = []
    raw = material.get("blockIds")
    if isinstance(raw, list):
        for item in raw:
            block = item.strip() if isinstance(item, str) else ""
            if block and block not in ids:
                ids.append(block)
    if ids:
        return ids
    raw_blocks = material.get("blocks")
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            if not isinstance(item, Mapping):
                continue
            block = _text(item.get("id"))
            if block and block not in ids:
                ids.append(block)
    return ids


def _upsert_material(
    materials: tuple[Mapping[str, object], ...],
    row: Mapping[str, object] | None,
) -> tuple[Mapping[str, object], ...]:
    """A material is one object: picking it again replaces its reading."""
    if row is None:
        return materials
    for index, item in enumerate(materials):
        if item.get("id") == row.get("id"):
            return materials[:index] + (row,) + materials[index + 1 :]
    return materials + (row,)


def _page_digest(
    layout: ProjectLayout, spec: Mapping[str, object]
) -> str:
    """The digest of the page one spec names, or empty when it does not exist."""
    absolute = resolve_wiki_write_path(layout, page_path_for_spec(spec))
    if not absolute.is_file():
        return ""
    return blob_digest(absolute.read_text(encoding="utf-8"))


def _approval_covers(
    approval: Approval | None,
    frame_id: str,
    spec: Mapping[str, object],
    rev: int,
) -> bool:
    """Whether an approval is exactly for this frame, spec and revision.

    All three parts are compared: a different frame, a changed spec, or a task
    that moved to another revision is a different write, and the person has not
    approved it.
    """
    return (
        approval is not None
        and approval.frame_id == frame_id
        and approval.rev == rev
        and _same_page_spec(approval.spec, spec)
    )


def _same_page_spec(left: object, right: object) -> bool:
    """Whether two specs canonicalize to the same request.

    A stored spec was canonicalized when it was written; canonicalizing again is
    what makes a hand-seeded or older row comparable instead of trusted.
    """
    try:
        return canonical_page_spec(left) == canonical_page_spec(right)
    except ValueError:
        return False


def _input_text(values: Mapping[str, object], key: str) -> str:
    value = values.get(key)
    return value.strip() if isinstance(value, str) else ""


def _input_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


#: What a finished write may tell the model: the page's fate and identity, a
#: bounded summary, the compile-shaped lists and the diff's size. The diff text
#: and the effect ids stay behind: the model sees a page it can open, never the
#: raw storage the writer touched.
_PUBLIC_WRITE_KEYS = (
    "status",
    "page",
    "slug",
    "title",
    "action",
    "summary",
    "reason",
    "written",
    "skipped",
    "failed",
    "added",
    "removed",
)


def _public_write_result(result: Mapping[str, object]) -> dict[str, object]:
    return {key: result[key] for key in _PUBLIC_WRITE_KEYS if key in result}


def op_delta(task: Task, op: Op) -> dict[str, object]:
    """The bounded answer a finished op gives the session that resumes on it.

    This is the whole payload the model reads when the executor boundary brings
    the conversation back: the op's identity and one result shaped for its kind.
    It never carries the task, the op log, the effect ids or the page's text;
    the model calls ``inspect`` for anything more.
    """
    if op.status == "done":
        if op.kind == "search":
            raw_ids = op.result.get("cardIds")
            wanted = (
                [item for item in raw_ids if isinstance(item, str)]
                if isinstance(raw_ids, list)
                else []
            )
            by_id = {card.id: card for card in task.cards}
            cards: list[dict[str, object]] = []
            for ident in wanted:
                if len(cards) >= MAX_VIEW_CARDS:
                    break
                card = by_id.get(ident)
                if card is None:
                    continue
                cards.append(
                    {
                        "id": card.id,
                        "title": card.title,
                        "url": card.url,
                        "site": card.site,
                        "snippet": card.snippet[:MAX_CARD_SNIPPET],
                    }
                )
            hits = op.result.get("hits")
            return {
                "status": "done",
                "hits": (
                    hits
                    if isinstance(hits, int) and not isinstance(hits, bool)
                    else len(cards)
                ),
                "cards": cards,
            }
        if op.kind == "pick":
            material_id = _text(op.result.get("materialId"))
            row = next(
                (
                    item
                    for item in task.materials
                    if _text(item.get("id")) == material_id
                ),
                None,
            )
            if row is not None:
                return {
                    "status": "done",
                    "material": material_summary(row),
                }
            return {
                "status": "done",
                "material": {
                    "id": material_id,
                    "title": _text(op.result.get("title")),
                    "fidelity": _text(op.result.get("fidelity")),
                    "blockCount": _input_count(op.result.get("blocks")),
                },
            }
        if op.kind == "write":
            return _public_write_result(op.result)
        return {
            "status": "done",
            **{key: op.result[key] for key in ("hits",) if key in op.result},
        }
    delta: dict[str, object] = {
        "status": op.status,
        "errorCode": op.error_code or op.status,
        "error": op.error[:600],
    }
    if op.kind == "write":
        failure = _write_failure_result(
            op, op.error_code or op.status, op.error
        )
        for key in ("page", "slug", "title", "summary", "reason"):
            delta[key] = failure[key]
    return delta


def _write_failure_result(
    op: Op, code: str, error: str
) -> dict[str, object]:
    """The task's record of a write that produced no page.

    The page identity still comes from the op's spec, so the interface can say
    which page the attempt aimed at; the reason is the op's own error, bounded.
    """
    spec: Mapping[str, object] = {}
    try:
        spec = canonical_page_spec(op.input.get("spec"))
    except ValueError:
        pass
    wiki_path = ""
    try:
        if spec:
            wiki_path = page_path_for_spec(spec)
    except ValueError:
        wiki_path = ""
    reason = error.strip()[:600]
    summary = (
        f"could not write {wiki_path}: {reason}"
        if wiki_path
        else f"could not write the page: {reason}"
    )
    return {
        "status": "failed",
        "page": wiki_path,
        "slug": _text(spec.get("slug")),
        "title": _text(spec.get("title")),
        "action": _text(spec.get("action")),
        "summary": summary[:800],
        "reason": reason,
        "written": [],
        "skipped": [],
        "failed": [{"path": wiki_path, "reason": reason}] if wiki_path else [],
        "diff": "",
        "added": 0,
        "removed": 0,
    }


def _replace_op(task: Task, op: Op) -> tuple[Op, ...]:
    return tuple(op if item.id == op.id else item for item in task.ops)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _spawn_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


__all__ = [
    "DEFAULT_COMPOSE_WAIT_S",
    "DEFAULT_PICK_WAIT_S",
    "DEFAULT_POLL_S",
    "DEFAULT_WAIT_S",
    "ApproveOutcome",
    "ComposeOutcome",
    "EditOutcome",
    "FrameOutcome",
    "ObservedRevFn",
    "OpOutcome",
    "OpResumeNotice",
    "PickOutcome",
    "SearchOutcome",
    "TaskBadSpec",
    "TaskConflict",
    "TaskNeedsApproval",
    "TaskNotFound",
    "TaskOpFailed",
    "TaskOpResumeFn",
    "TaskRegistry",
    "TaskService",
    "TaskToolError",
    "TaskUnreadable",
    "op_delta",
]
