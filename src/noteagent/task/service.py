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

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from threading import Event, Lock
from typing import Iterator

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
    MAX_MATERIAL_TITLE,
    MAX_VIEW_BLOCKS,
    TASK_VIEWS,
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
    task_view,
)
from noteagent.task.store import TaskStore
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
        self._project_key = str(layout.root.resolve())
        self._owner = f"task-{id(self)}"
        self._store = TaskStore(layout)

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
        task = self._store.load()
        if task is None:
            with self._writing():
                task = self._store.load()
                if task is None:
                    task = self._create_locked()
        self._registry.observe(self._project_key, session_id, task.rev)
        return task_view(task, view), task.rev

    def search(
        self,
        query: str,
        *,
        session_id: str = "",
        cancel: Event | None = None,
    ) -> SearchOutcome:
        """Write a ``search`` op, run the node behind it, and wait at most N s.

        The op is accepted only at the revision this session last saw. A stale
        observation is refused before anything is written.
        """
        cleaned = " ".join(query.split())
        if not cleaned:
            raise ValueError("a search needs a query")
        expected = self._registry.observed(self._project_key, session_id)
        op_id = self._request(
            kind="search",
            actor="agent",
            input={"query": cleaned},
            expected_rev=expected,
            session_id=session_id,
        )
        observed = self._wait(
            op_id, f'the web search "{cleaned}"', cancel, wait_s=self._wait_s
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
            else self._registry.observed(self._project_key, session_id)
        )
        op_id = self._request(
            kind="pick",
            actor=actor,  # type: ignore[arg-type]
            input={"cardId": cleaned},
            expected_rev=expected,
            session_id=session_id,
        )
        observed = self._wait(
            op_id,
            f'the pick of card "{cleaned}"',
            cancel,
            wait_s=self._pick_wait_s,
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
            else self._registry.observed(self._project_key, session_id)
        )
        op_id = self._ids.new_id("op")
        now = iso_timestamp(self._clock)
        with self._writing():
            task = self._store.load() or self._create_locked()
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
            else self._registry.observed(self._project_key, session_id)
        )
        now = iso_timestamp(self._clock)
        op_id = ""
        with self._writing():
            task = self._store.load() or self._create_locked()
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
            task = self._store.load()
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
            approval = (
                Approval(
                    frame_id=cleaned_frame,
                    spec=canonical,
                    rev=task.rev + 1,
                    by=by,
                    at=now,
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
            task = self._store.load() or self._create_locked()
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
        try:
            claimed = self._claim(op_id)
            if claimed is None:
                return
            try:
                answer = self._execute(*claimed)
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

    def _execute(self, task: Task, op: Op) -> "_OpAnswer":
        """Dispatch one claimed op to its pure node(s). No task writes here."""
        if op.kind == "search":
            query = _input_text(op.input, "query")
            try:
                cards = list(self._search(query))
            except Exception as err:  # noqa: BLE001 - named in the op
                raise _OpFailure(
                    "op_failed", f"{type(err).__name__}: {err}"
                ) from err
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
            capture = self._fetch(card.url)
            if capture.fidelity == FIDELITY_FAILED:
                raise _OpFailure(
                    "op_failed",
                    f"the page at {card.url} failed the quality gate and was "
                    "not staged",
                )
            staged = self._stage(capture, task.id)
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
            return self._execute_write(task, op)
        raise _OpFailure("op_failed", f"unknown op kind: {op.kind!r}")

    def _execute_write(self, task: Task, op: Op) -> "_OpAnswer":
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
        try:
            block = self._compose(self._layout, request, None)
        except Exception as err:  # noqa: BLE001 - named on the op
            raise _OpFailure(
                "op_failed", f"the page could not be composed: {err}"
            ) from err
        with self._writing():
            current = self._store.load()
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
            now_text = (
                absolute.read_text(encoding="utf-8") if absolute.is_file() else ""
            )
            now_digest = blob_digest(now_text) if absolute.is_file() else ""
            if now_digest != base_digest:
                raise _OpFailure(
                    "conflict",
                    f"the page {wiki_path} changed while it was being "
                    "composed. The person's edit was kept and nothing was "
                    f"written. Do not retry blindly; call inspect and read the "
                    "page again.",
                )
            try:
                with self._page_locks.holding(self._project_key, wiki_path):
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
                        None,
                    )
            except PageBusy as err:
                raise _OpFailure(
                    "conflict",
                    f"{err}. A person is writing this page; nothing was "
                    "written. Do not retry blindly; read the page again before "
                    "deciding whether to compose.",
                ) from err
        after = absolute.read_text(encoding="utf-8") if absolute.is_file() else ""
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
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return None
            now = iso_timestamp(self._clock)
            claimed = replace(op, status="running", updated_at=now)
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(task, claimed),
                    updated_at=now,
                )
            )
            return task, claimed

    def _finish_done(self, op_id: str, answer: "_OpAnswer") -> None:
        with self._writing():
            task = self._store.load()
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
            done = replace(op, status="done", result=result, updated_at=now)
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
            self._store.save(
                replace(
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
            )

    def _finish_failed(self, op_id: str, code: str, error: str) -> None:
        with self._writing():
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is None or op is None or op.status not in ACTIVE_OP_STATUSES:
                return
            now = iso_timestamp(self._clock)
            result = task.result
            status = task.status
            if op.kind == "write":
                # A compose or save that failed still leaves the attempt on the
                # task: the reader sees the page it aimed at and why it is not
                # there. The op carries the same failure for the agent.
                result = _write_failure_result(op, code, error)
                status = "failed"
            self._store.save(
                replace(
                    task,
                    ops=_replace_op(
                        task,
                        replace(
                            op,
                            status="failed",
                            error=error[:1000],
                            error_code=code,
                            updated_at=now,
                        ),
                    ),
                    result=result,
                    status=status,
                    updated_at=now,
                )
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
            task = self._store.load()
            op = task.op(op_id) if task is not None else None
            if task is not None:
                rev = task.rev
            if op is not None:
                status = op.status
            if op is not None and op.status == "failed":
                detail = op.error or "the node behind it failed"
                if op.error_code == "conflict":
                    raise TaskConflict(
                        f"{what} did not write because the task or the page "
                        f"changed: {detail}. conflict. Do not retry blindly; "
                        "call inspect and read the page again."
                    )
                if op.error_code == "bad_spec":
                    raise TaskBadSpec(
                        f"{detail}. bad_spec. Do not retry blindly; call "
                        "inspect and use a page name it can write."
                    )
                if op.error_code == "not_found":
                    raise TaskNotFound(
                        f"{detail}. not_found. Do not retry blindly; call "
                        "inspect and use an id it shows."
                    )
                raise TaskOpFailed(
                    f"{what} could not finish: {detail}. op_failed. "
                    "Do not retry blindly; call inspect before deciding "
                    "whether to try again."
                )
            if op is not None and op.status == "done":
                return _Observed("done", rev, task, op)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _Observed(status, rev, task, op)
            time.sleep(min(self._poll_s, remaining))

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
    "FrameOutcome",
    "PickOutcome",
    "SearchOutcome",
    "TaskBadSpec",
    "TaskConflict",
    "TaskNeedsApproval",
    "TaskNotFound",
    "TaskOpFailed",
    "TaskRegistry",
    "TaskService",
    "TaskToolError",
]
