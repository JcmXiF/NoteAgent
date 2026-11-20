"""The writing task: one shared object, written only by its executor.

The task is the truth the agent and the interface both read. It is deliberately
small and typed: cards, materials, frames, an optional plan/approval/result, and
an append-only op log. ``rev`` counts revisions of that content; a caller that
mutates names the revision it observed, and a stale one is refused instead of
overwriting somebody else's change.

The op log is part of the stored shape but not part of any model-visible view:
the model sees bounded views, never the raw queue.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from noteagent.acquire.types import Capture

TaskStatus: TypeAlias = Literal[
    "idle",
    "running",
    "needs_approval",
    "done",
    "failed",
]

TASK_STATUSES: tuple[TaskStatus, ...] = (
    "idle",
    "running",
    "needs_approval",
    "done",
    "failed",
)

#: The kinds of operation the task knows. ``frame``, ``approve`` and ``edit``
#: are pure state: the tool or the person applies them themselves and logs them;
#: the executor never claims them. ``approve`` is written only by a person's
#: route. ``edit`` is a deliberate extension of the spec's first-batch kinds: it
#: records a person's real save through the page editor, which rides the same
#: task revision so a changed page invalidates a standing approval. It is not
#: the agent's ``write`` and carries no effect journal. ``write`` is the
#: executor's page write: compose one frame's spec and save the page, after
#: checking the approval that covers it is still the one.
OpKind: TypeAlias = Literal["search", "pick", "frame", "approve", "write", "edit"]

OP_KINDS: tuple[OpKind, ...] = (
    "search",
    "pick",
    "frame",
    "approve",
    "write",
    "edit",
)

#: Where a frame came from. A person and the agent draw frames in this slice;
#: ``model`` is the auto-framing node the pipeline already has, for later.
FrameSource: TypeAlias = Literal["human", "agent", "model"]

FRAME_SOURCES: tuple[FrameSource, ...] = ("human", "agent", "model")

OpStatus: TypeAlias = Literal[
    "requested", "running", "done", "failed", "cancelled"
]

OP_STATUSES: tuple[OpStatus, ...] = (
    "requested",
    "running",
    "done",
    "failed",
    "cancelled",
)

#: An op in one of these has not reached an answer yet.
ACTIVE_OP_STATUSES = frozenset({"requested", "running"})

#: An op in one of these will never change again: it is neither retried nor
#: resumed. ``cancelled`` is a person's decision and is terminal.
TERMINAL_OP_STATUSES = frozenset({"done", "failed", "cancelled"})

#: How many ops the summary view carries. Active and interrupted ops come
#: first, because those are the ones the panel can still cancel or resume.
MAX_VIEW_OPS = 8

#: Who asked for an op. ``approve`` is the one kind only a human may write.
Actor: TypeAlias = Literal["agent", "human"]

#: The views the task can answer with. Each is bounded; none is the task file.
#: ``approval`` carries the person's go-ahead (or none) and the pending write
#: request the confirmation panel shows. ``result`` is the one page the last
#: approved write produced, with its bounded summary and diff: both the
#: interface's routes and the model's ``inspect`` offer it.
TASK_VIEWS: tuple[str, ...] = (
    "summary",
    "cards",
    "materials",
    "frames",
    "approval",
    "result",
)

#: How many cards one bounded view carries.
MAX_VIEW_CARDS = 20

#: How much of one card's snippet a view or a tool result carries.
MAX_CARD_SNIPPET = 400

#: How many materials, and how many blocks of each, the task and a view carry.
#: The full index stays in the staging manifest; the task keeps a bounded copy,
#: so its file does not grow with a 700-page PDF, and the view is what a
#: model's context can afford to read.
MAX_VIEW_MATERIALS = 8
MAX_VIEW_BLOCKS = 120

#: How many frames a view carries, and how many of one frame's blocks. A frame
#: is a set, but a long document can make it a large set; the view shows the
#: first ids with their previews and says how many it left out.
MAX_VIEW_FRAMES = 8
MAX_VIEW_FRAME_BLOCKS = 60

#: How long a title or a block preview may be in a view. Manifest previews are
#: short already; a node is not trusted to keep them so.
MAX_MATERIAL_TITLE = 200
MAX_BLOCK_PREVIEW = 200

#: What a writing spec may ask for. A page carries at most twelve sections and
#: one section at most twenty passages; the spec is stored whole in the task and
#: shown whole to the person who approves it, so these bounds are also what keeps
#: that view bounded. ``MAX_SPEC_TITLE`` is the spec's stated title limit; a
#: section title is bounded less tightly because it is a heading, not a page name.
MAX_SPEC_SECTIONS = 12
MAX_SECTION_BLOCKS = 20
MAX_SPEC_TITLE = 120
MAX_SECTION_TITLE = 200
MAX_SPEC_SLUG = 80

#: The two things writing a page can mean in this slice. ``create`` makes a new
#: page; ``update`` rewrites one that exists. Whether the page really exists is
#: the writer's business (ticket 05), not the spec's.
PAGE_SPEC_ACTIONS: tuple[str, ...] = ("create", "update")

_SPEC_KEYS = frozenset({"slug", "title", "action", "sections"})
_SECTION_KEYS = frozenset({"title", "blocks"})
_SLUG_SAFE = re.compile(r"[^\w\-]+", re.UNICODE)

_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")


@dataclass(frozen=True, slots=True)
class SearchCard:
    """One search hit, as the pure node returns it.

    The node is stateless: it knows the query and the web, not the task. The
    executor is what gives the hit its card id and puts it in the task.
    """

    title: str
    url: str
    snippet: str = ""
    site: str = ""


#: A pure search node: query in, hits out. No task reads or writes.
SearchFn: TypeAlias = Callable[[str], Sequence[SearchCard]]

#: A pure fetch node: one public URL in, one capture out. No task reads or
#: writes; the executor decides what the capture becomes.
FetchFn: TypeAlias = Callable[[str], Capture]


@dataclass(frozen=True, slots=True)
class MaterialBlock:
    """One block of a material, as the staging manifest indexes it.

    The block source text stays in staging; the task carries the id and the
    short preview a later frame names.
    """

    id: str
    tag: str = ""
    preview: str = ""


@dataclass(frozen=True, slots=True)
class StagedMaterial:
    """What the stage node returns: a material is now in the staging area.

    The executor copies this into the task. The node never sees the task, so a
    test can script it without one.
    """

    material: str
    title: str
    fidelity: str
    state: str = "staged"
    kind: str = ""
    url: str = ""
    block_count: int = 0
    blocks: tuple[MaterialBlock, ...] = ()


#: A pure stage node: capture and the staging owner in, staged material out.
StageFn: TypeAlias = Callable[[Capture, str], StagedMaterial]


@dataclass(frozen=True, slots=True)
class Material:
    """One material recorded in the task, with a bounded block index.

    The source text stays in staging. The task keeps the identity, the state a
    later frame and write read, the full block id list (so a frame may name any
    block) and the short previews a view can afford. ``run_id`` is the run that
    staged it, kept so the stored shape does not change.
    """

    id: str
    title: str = ""
    url: str = ""
    kind: str = ""
    fidelity: str = ""
    state: str = "staged"
    run_id: str = ""
    block_count: int = 0
    block_ids: tuple[str, ...] = ()
    blocks: tuple[MaterialBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class Card:
    """A material card in the task, addressable by id for a later pick."""

    id: str
    title: str
    url: str
    snippet: str = ""
    site: str = ""


@dataclass(frozen=True, slots=True)
class Frame:
    """What a person or the agent marked as writing material on one document.

    A frame is a set of block ids from one material. One frame per document is
    enough for this slice: drawing again replaces the blocks and records who
    drew them. The block text stays in staging; the frame names ids only.
    """

    id: str
    doc_id: str
    blocks: tuple[str, ...] = ()
    source: str = "agent"


@dataclass(frozen=True, slots=True)
class Approval:
    """A person's go-ahead to write one frame with one writing spec.

    The approval is bound to the frame, the canonical spec and the task
    revision it was given at. ``page_digest`` is the page the person approved
    against, when it existed: a later writer compares it before saving, so a
    page that changed after the approval (a person's edit, say) is never
    overwritten even if the task's edit record could not be written. An empty
    digest is an older or hand-seeded approval with no page binding; the page
    digest captured at compose time still guards the compose itself.
    """

    frame_id: str
    spec: Mapping[str, object] = field(default_factory=dict)
    rev: int = 0
    by: str = "human"
    at: str = ""
    page_digest: str = ""


@dataclass(frozen=True, slots=True)
class WriteRequest:
    """A writing spec the agent proposed and a person has not answered yet.

    This is a deliberate extension of the spec's task shape: the approval lists
    the go-ahead, but the interface also needs the *proposal* to show before it
    is approved. The request is the same object for the chat card and the task
    panel, so no second store is built; it is cleared by the approval, the
    rejection, a frame change, or any other write that moves the revision.
    """

    frame_id: str
    spec: Mapping[str, object] = field(default_factory=dict)
    rev: int = 0
    at: str = ""


@dataclass(frozen=True, slots=True)
class Op:
    """One typed operation on the task.

    ``expected_rev`` is the task revision the caller observed when it asked.
    The executor refuses a stale one; the op is never applied over a change it
    never saw.
    """

    id: str
    kind: OpKind
    actor: Actor
    status: OpStatus
    input: Mapping[str, object]
    expected_rev: int
    created_at: str
    updated_at: str
    result: Mapping[str, object] = field(default_factory=dict)
    error: str = ""
    #: The situation a failed op names (``not_found``, ``op_failed``), so the
    #: tool that waited on it can put the right code in the model's error.
    error_code: str = ""
    #: The tool's bounded wait expired while this op was still active: the
    #: session has moved on and wants the completion to bring it back. The
    #: executor triggers a resume only when this is set.
    resume_requested: bool = False
    #: The revision the op finished at, recorded when it finishes with the
    #: resume request in force. It is the revision the continuation observes:
    #: the completion's, not whatever the task has moved to by the time the
    #: resume is claimed behind a busy turn. Zero when no resume was requested
    #: (or for an older op), where the claim falls back to the task's revision.
    resume_rev: int = 0
    #: How many times the executor boundary has tried to deliver this op's
    #: continuation. An attempt is recorded under the task's writer lock; a
    #: crash before the session appends its ``task_op_result`` is retried on
    #: the next start, so this is a count, not a consumed-once flag.
    resume_attempt: int = 0
    #: Whether the continuation is known to be in the session. It is set only
    #: after the resumer reads a committed ``task_op_result`` for this op (or
    #: after the run that appends it returns); a pre-consumed marker would lose
    #: a delivery whose process died before the append. Older task files carry
    #: it as an at-most-once marker; those entries load as delivered.
    resume_started: bool = False
    #: Why a requested resume could not start (the session is gone, say). The
    #: op stays inspectable and is not retried.
    resume_error: str = ""
    #: The process ended while this op was active. No executor owns it now; a
    #: person may resume it or cancel it, and a cancelled op is never retried.
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class Task:
    """The one writing task of a project."""

    id: str
    created_at: str
    updated_at: str
    kind: str = "writing"
    intent: str = ""
    status: TaskStatus = "idle"
    rev: int = 0
    cards: tuple[Card, ...] = ()
    materials: tuple[Material, ...] = ()
    frames: tuple[Frame, ...] = ()
    plan: tuple[Mapping[str, object], ...] = ()
    approval: Approval | None = None
    #: The pending write request, from the last ``compose_page`` that found no
    #: matching approval. It is not part of the spec's task shape; the shared
    #: confirmation panel needs the proposal itself, and this is the one place
    #: the chat card and the panel can read it from.
    request: WriteRequest | None = None
    result: Mapping[str, object] | None = None
    ops: tuple[Op, ...] = ()

    def op(self, op_id: str) -> Op | None:
        return next((item for item in self.ops if item.id == op_id), None)


def is_task_id(value: str) -> bool:
    """Whether *value* can name a task or one of its entities as a file name."""
    return _ID.fullmatch((value or "").strip()) is not None


def card_to_dict(card: Card) -> dict[str, object]:
    return {
        "id": card.id,
        "title": card.title,
        "url": card.url,
        "snippet": card.snippet,
        "site": card.site,
    }


def card_from_dict(raw: Mapping[str, object]) -> Card:
    card_id = _text(raw.get("id"))
    if not is_task_id(card_id):
        raise ValueError(f"not a card id: {card_id!r}")
    url = _text(raw.get("url"))
    if not url:
        raise ValueError("a card needs a url")
    return Card(
        id=card_id,
        title=_text(raw.get("title")) or url,
        url=url,
        snippet=_text(raw.get("snippet")),
        site=_text(raw.get("site")),
    )


def material_to_dict(material: Material) -> dict[str, object]:
    """One task material as the task file stores it and a route returns it."""
    return {
        "id": material.id,
        "title": material.title,
        "url": material.url,
        "kind": material.kind,
        "fidelity": material.fidelity,
        "state": material.state,
        "runId": material.run_id,
        "blockCount": material.block_count,
        "blockIds": list(material.block_ids),
        "blocks": [
            {"id": block.id, "tag": block.tag, "preview": block.preview}
            for block in material.blocks
        ],
    }


def material_from_dict(raw: Mapping[str, object]) -> Material:
    """One stored material, or ``ValueError`` when it cannot be read.

    A material id is a ``raw/`` path (``raw/web/entropy-a1.html``), not a task
    id: it carries slashes and a suffix, so it is only checked for emptiness.
    An older row without ``blockIds`` falls back to its bounded preview index,
    which is what a frame could already name.
    """
    material_id = _text(raw.get("id"))
    if not material_id:
        raise ValueError("a material needs an id")
    blocks = _material_blocks(raw.get("blocks"))
    block_ids = _material_ids(raw.get("blockIds"))
    if not block_ids:
        block_ids = tuple(
            dict.fromkeys(block.id for block in blocks if block.id)
        )
    return Material(
        id=material_id,
        title=_text(raw.get("title")),
        url=_text(raw.get("url")),
        kind=_text(raw.get("kind")),
        fidelity=_text(raw.get("fidelity")),
        state=_text(raw.get("state")) or "staged",
        run_id=_text(raw.get("runId")),
        block_count=_count(raw.get("blockCount")),
        block_ids=block_ids,
        blocks=blocks,
    )


def op_to_dict(op: Op, *, storage: bool = False) -> dict[str, object]:
    """One op as a route or storage record.

    ``resumeAttempt`` is an internal delivery counter; it belongs to the stored
    task so a restart can keep counting, but it is not part of the op record the
    cancel/resume routes already return.
    """
    body: dict[str, object] = {
        "id": op.id,
        "kind": op.kind,
        "actor": op.actor,
        "status": op.status,
        "input": dict(op.input),
        "expectedRev": op.expected_rev,
        "createdAt": op.created_at,
        "updatedAt": op.updated_at,
        "result": dict(op.result),
        "error": op.error,
        "errorCode": op.error_code,
        "resumeRequested": op.resume_requested,
        "resumeRev": op.resume_rev,
        "resumeStarted": op.resume_started,
        "resumeError": op.resume_error,
        "interrupted": op.interrupted,
    }
    if storage:
        body["resumeAttempt"] = op.resume_attempt
    return body


def op_from_dict(raw: Mapping[str, object]) -> Op:
    op_id = _text(raw.get("id"))
    if not is_task_id(op_id):
        raise ValueError(f"not an op id: {op_id!r}")
    kind = _choice(raw, "kind", OP_KINDS, "search")
    status = _choice(raw, "status", OP_STATUSES, "requested")
    actor = _choice(raw, "actor", ("agent", "human"), "agent")
    values = raw.get("input")
    result = raw.get("result")
    return Op(
        id=op_id,
        kind=kind,  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        input=dict(values) if isinstance(values, Mapping) else {},
        expected_rev=_count(raw.get("expectedRev")),
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        result=dict(result) if isinstance(result, Mapping) else {},
        error=_text(raw.get("error")),
        error_code=_text(raw.get("errorCode")),
        resume_requested=raw.get("resumeRequested") is True,
        resume_rev=_count(raw.get("resumeRev")),
        resume_attempt=_count(raw.get("resumeAttempt")),
        resume_started=raw.get("resumeStarted") is True,
        resume_error=_text(raw.get("resumeError")),
        interrupted=raw.get("interrupted") is True,
    )


def frame_to_dict(frame: Frame) -> dict[str, object]:
    return {
        "id": frame.id,
        "docId": frame.doc_id,
        "blocks": list(frame.blocks),
        "source": frame.source,
    }


def frame_from_dict(raw: Mapping[str, object]) -> Frame:
    frame_id = _text(raw.get("id"))
    if not is_task_id(frame_id):
        raise ValueError(f"not a frame id: {frame_id!r}")
    doc_id = _text(raw.get("docId"))
    if not doc_id:
        raise ValueError("a frame needs a document id")
    raw_blocks = raw.get("blocks")
    blocks: list[str] = []
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            block = _text(item)
            if block and block not in blocks:
                blocks.append(block)
    return Frame(
        id=frame_id,
        doc_id=doc_id,
        blocks=tuple(blocks),
        source=_choice(raw, "source", FRAME_SOURCES, "agent"),
    )


def approval_to_dict(approval: Approval) -> dict[str, object]:
    return {
        "frameId": approval.frame_id,
        "spec": dict(approval.spec),
        "rev": approval.rev,
        "by": approval.by,
        "at": approval.at,
        "pageDigest": approval.page_digest,
    }


def approval_from_dict(raw: Mapping[str, object]) -> Approval | None:
    """The stored approval, or ``None`` for one that cannot be read.

    A malformed approval is dropped rather than hiding the whole task: losing
    the go-ahead only makes the person approve again, which is the safe side
    of the gate.
    """
    frame_id = _text(raw.get("frameId"))
    if not is_task_id(frame_id):
        return None
    spec = raw.get("spec")
    return Approval(
        frame_id=frame_id,
        spec=dict(spec) if isinstance(spec, Mapping) else {},
        rev=_count(raw.get("rev")),
        by=_text(raw.get("by")) or "human",
        at=_text(raw.get("at")),
        page_digest=_text(raw.get("pageDigest")),
    )


def request_to_dict(request: WriteRequest) -> dict[str, object]:
    return {
        "frameId": request.frame_id,
        "spec": dict(request.spec),
        "rev": request.rev,
        "at": request.at,
    }


def request_from_dict(raw: Mapping[str, object]) -> WriteRequest | None:
    """The stored request, or ``None`` for one that cannot be read.

    Losing a pending request only makes the agent propose again, which is the
    safe side of the gate, so a malformed row is dropped like a malformed
    approval.
    """
    frame_id = _text(raw.get("frameId"))
    if not is_task_id(frame_id):
        return None
    spec = raw.get("spec")
    return WriteRequest(
        frame_id=frame_id,
        spec=dict(spec) if isinstance(spec, Mapping) else {},
        rev=_count(raw.get("rev")),
        at=_text(raw.get("at")),
    )


def canonical_page_spec(raw: object) -> dict[str, object]:
    """The one shape a writing spec is stored and compared in.

    A spec is exactly ``slug``, ``title``, ``action`` and ``sections``; a
    section is exactly ``title`` and ``blocks``. Anything else is refused rather
    than ignored, because a field the writer would not honour (``coverage``,
    ``wikiPath``, ...) must not look as if it were part of the request. The slug
    is normalized the way the wiki names pages, so two spellings of one page are
    one identity for the approval.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("a page spec must be an object")
    extra = sorted(str(key) for key in raw if key not in _SPEC_KEYS)
    if extra:
        raise ValueError(
            f"a page spec has no field {extra[0]!r}; it is exactly slug, title, "
            "action and sections"
        )
    slug = canonical_slug(raw.get("slug"))
    if not slug:
        raise ValueError(
            "a page spec needs a slug; use letters, digits, hyphens or '/'"
        )
    title = _text(raw.get("title")) or slug
    if len(title) > MAX_SPEC_TITLE:
        raise ValueError(
            f"a page title is at most {MAX_SPEC_TITLE} characters"
        )
    action = _text(raw.get("action"))
    if action not in PAGE_SPEC_ACTIONS:
        raise ValueError("a page action is create or update")
    raw_sections = raw.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ValueError("a page spec needs at least one section")
    if len(raw_sections) > MAX_SPEC_SECTIONS:
        raise ValueError(
            f"a page spec carries at most {MAX_SPEC_SECTIONS} sections"
        )
    sections: list[dict[str, object]] = []
    for index, item in enumerate(raw_sections, 1):
        if not isinstance(item, Mapping):
            raise ValueError(f"section {index} must be an object")
        extra = sorted(str(key) for key in item if key not in _SECTION_KEYS)
        if extra:
            raise ValueError(
                f"section {index} has no field {extra[0]!r}; it is exactly "
                "title and blocks"
            )
        section_title = _text(item.get("title"))
        if not section_title:
            raise ValueError(f"section {index} needs a title")
        if len(section_title) > MAX_SECTION_TITLE:
            raise ValueError(
                f"section {index}'s title is at most {MAX_SECTION_TITLE} "
                "characters"
            )
        raw_blocks = item.get("blocks")
        if not isinstance(raw_blocks, list) or not raw_blocks:
            raise ValueError(f"section {index} needs at least one block")
        blocks: list[str] = []
        for value in raw_blocks:
            block = _text(value)
            if not block:
                raise ValueError(
                    f"section {index} names a blank block id"
                )
            if block not in blocks:
                blocks.append(block)
        if len(blocks) > MAX_SECTION_BLOCKS:
            raise ValueError(
                f"section {index} carries at most {MAX_SECTION_BLOCKS} blocks"
            )
        sections.append({"title": section_title, "blocks": blocks})
    return {
        "slug": slug,
        "title": title,
        "action": action,
        "sections": sections,
    }


def canonical_slug(value: object) -> str:
    """A page name as the wiki names pages: lower-case, hyphenated, no folders.

    This mirrors ``ingest.topic_map.page_slug`` without importing the ingest
    layer: the task module only needs the identity, and ticket 05's writer will
    resolve the same name to a file. A name with nothing safe left in it is
    empty, which the caller refuses.
    """
    text = _text(value).replace("\\", "/").strip("/")
    if text.lower().startswith("wiki/"):
        text = text[len("wiki/") :]
    if text.lower().endswith(".md"):
        text = text[:-3]
    parts: list[str] = []
    for part in text.split("/"):
        item = part.strip().lower()
        item = re.sub(r"\s+", "-", item)
        item = _SLUG_SAFE.sub("", item)
        item = re.sub(r"-{2,}", "-", item).strip("-")
        if item:
            parts.append(item)
    return "/".join(parts)[:MAX_SPEC_SLUG]


def task_to_dict(task: Task) -> dict[str, object]:
    """The task as one shape, used by the task file and by nothing else.

    No route returns this: the model and the interface read bounded views.
    """
    body: dict[str, object] = {
        "version": 1,
        "id": task.id,
        "kind": task.kind,
        "intent": task.intent,
        "status": task.status,
        "rev": task.rev,
        "createdAt": task.created_at,
        "updatedAt": task.updated_at,
        "cards": [card_to_dict(card) for card in task.cards],
        "materials": [
            material_to_dict(material) for material in task.materials
        ],
        "frames": [frame_to_dict(frame) for frame in task.frames],
        "plan": [dict(item) for item in task.plan],
        "ops": [op_to_dict(op, storage=True) for op in task.ops],
    }
    if task.approval is not None:
        body["approval"] = approval_to_dict(task.approval)
    if task.request is not None:
        body["request"] = request_to_dict(task.request)
    if task.result is not None:
        body["result"] = dict(task.result)
    return body


def task_from_dict(raw: Mapping[str, object]) -> Task:
    if not isinstance(raw, Mapping):
        raise ValueError("a task must be an object")
    task_id = _text(raw.get("id"))
    if not is_task_id(task_id):
        raise ValueError(f"not a task id: {task_id!r}")
    cards = raw.get("cards")
    ops = raw.get("ops")
    approval = raw.get("approval")
    request = raw.get("request")
    result = raw.get("result")
    return Task(
        id=task_id,
        created_at=_text(raw.get("createdAt")),
        updated_at=_text(raw.get("updatedAt")),
        kind=_text(raw.get("kind")) or "writing",
        intent=_text(raw.get("intent")),
        status=_choice(raw, "status", TASK_STATUSES, "idle"),  # type: ignore[arg-type]
        rev=_count(raw.get("rev")),
        cards=tuple(
            card_from_dict(item)
            for item in cards
            if isinstance(item, Mapping)
        )
        if isinstance(cards, list)
        else (),
        materials=_materials(raw.get("materials")),
        frames=_frames(raw.get("frames")),
        plan=_mappings(raw.get("plan")),
        approval=(
            approval_from_dict(approval)
            if isinstance(approval, Mapping)
            else None
        ),
        request=(
            request_from_dict(request)
            if isinstance(request, Mapping)
            else None
        ),
        result=dict(result) if isinstance(result, Mapping) else None,
        ops=tuple(
            op_from_dict(item)
            for item in ops
            if isinstance(item, Mapping)
        )
        if isinstance(ops, list)
        else (),
    )


def task_view(task: Task, view: str) -> dict[str, object]:
    """A bounded reading of the task, the same for a tool and a route.

    ``summary`` carries counts and the metadata a reader needs to know what the
    task is; ``cards`` carries the cards with snippets clipped; ``materials``
    carries their identities and a bounded id/preview block index. The op log
    and the block source text are never copied.
    """
    if view == "summary":
        ops, ops_omitted = _ops_view(task)
        return {
            "view": "summary",
            "rev": task.rev,
            "id": task.id,
            "kind": task.kind,
            "intent": task.intent[:500],
            "status": task.status,
            "updatedAt": task.updated_at,
            "counts": {
                "cards": len(task.cards),
                "materials": len(task.materials),
                "frames": len(task.frames),
            },
            # Whether a person's go-ahead still stands, and what it covers. The
            # writing spec is not part of a bounded summary.
            "approval": _approval_summary(task),
            # The ops a reader can still act on, plus the latest finished ones.
            # The panel needs the ids to cancel or resume; the op log itself
            # stays in the task file.
            "ops": ops,
            "opsOmitted": ops_omitted,
        }
    if view == "cards":
        shown = task.cards[:MAX_VIEW_CARDS]
        return {
            "view": "cards",
            "rev": task.rev,
            "cards": [
                {
                    **card_to_dict(card),
                    "snippet": card.snippet[:MAX_CARD_SNIPPET],
                }
                for card in shown
            ],
            "omitted": max(0, len(task.cards) - len(shown)),
        }
    if view == "materials":
        shown = task.materials[:MAX_VIEW_MATERIALS]
        return {
            "view": "materials",
            "rev": task.rev,
            "materials": [material_view(item) for item in shown],
            "omitted": max(0, len(task.materials) - len(shown)),
        }
    if view == "frames":
        shown = task.frames[:MAX_VIEW_FRAMES]
        return {
            "view": "frames",
            "rev": task.rev,
            "frames": [frame_view(task, frame) for frame in shown],
            "omitted": max(0, len(task.frames) - len(shown)),
        }
    if view == "approval":
        # The approval that stands, with the spec it covers, and the write
        # request that is still waiting for a person. A spec is bounded by its
        # own section and block caps, so it travels whole; nothing else of the
        # task does. Both are bindings, not rows: one whose revision or frame
        # no longer matches is reported as none and flagged stale, so the agent
        # can see that it needs to ask again.
        approval_stands = _approval_stands(task)
        request_stands = _request_stands(task)
        return {
            "view": "approval",
            "rev": task.rev,
            "status": task.status,
            "approval": (
                _approval_detail(task.approval) if approval_stands else None
            ),
            "approvalStale": task.approval is not None and not approval_stands,
            "request": (
                _request_detail(task.request) if request_stands else None
            ),
            "requestStale": task.request is not None and not request_stands,
        }
    if view == "result":
        # The one page the last write produced, as the writer reported it: the
        # page identity, written/skipped/failed, a bounded summary and the diff
        # the interface shows. The op's effect ids stay in the op log: they are
        # restore's business, not the reader's and never the model's.
        return {
            "view": "result",
            "rev": task.rev,
            "status": task.status,
            "result": dict(task.result) if task.result is not None else None,
        }
    raise ValueError(f"unknown task view: {view!r}")


def task_overview(task: Task) -> dict[str, object]:
    """Every bounded interface view of one task, from one read, at one revision.

    Separate route reads can straddle a write and show the panel two revisions;
    this snapshot is built from the one task object, so each part carries the
    same revision. The model does not see it; ``inspect`` serves one view.
    """
    body: dict[str, object] = {
        "view": "overview",
        "rev": task.rev,
    }
    for view in TASK_VIEWS:
        body[view] = task_view(task, view)
    return body


def _ops_view(task: Task) -> tuple[list[dict[str, object]], int]:
    """The ops a summary carries: the actionable ones first, bounded.

    An op that is active or was interrupted by a restart is what a person can
    still cancel (and, when interrupted, resume), so it is never pushed out by
    newer finished ops. The rest fills the remaining slots newest first.
    """
    actionable = [
        op
        for op in task.ops
        if op.status in ACTIVE_OP_STATUSES or op.interrupted
    ]
    shown_ids = {op.id for op in actionable}
    recent = [
        op for op in reversed(task.ops) if op.id not in shown_ids
    ]
    rows = actionable + recent
    shown = rows[:MAX_VIEW_OPS]
    return (
        [_op_summary(op) for op in shown],
        max(0, len(rows) - len(shown)),
    )


def _op_summary(op: Op) -> dict[str, object]:
    return {
        "id": op.id,
        "kind": op.kind,
        "actor": op.actor,
        "status": op.status,
        "createdAt": op.created_at,
        "updatedAt": op.updated_at,
        "interrupted": op.interrupted,
        "resumeRequested": op.resume_requested,
        "resumeStarted": op.resume_started,
        "resumeError": op.resume_error[:200],
        "errorCode": op.error_code,
        "error": op.error[:200],
    }


def frame_view(task: Task, frame: Frame) -> dict[str, object]:
    """One frame as a bounded reading: what it is, who drew it, which blocks.

    Blocks are ids with the material's previews where the bounded material
    index still has them; the block text itself is never copied. The same shape
    serves a tool result and a route.
    """
    previews = _block_previews(task, frame.doc_id)
    shown = frame.blocks[:MAX_VIEW_FRAME_BLOCKS]
    return {
        "id": frame.id,
        "docId": frame.doc_id,
        "source": frame.source,
        "blocks": [
            {
                "id": block,
                "preview": previews.get(block, "")[:MAX_BLOCK_PREVIEW],
            }
            for block in shown
        ],
        "blockCount": len(frame.blocks),
        "blocksOmitted": max(0, len(frame.blocks) - len(shown)),
    }


def _approval_summary(task: Task) -> dict[str, object] | None:
    """The part of an approval a bounded view carries: what, who, when, at rev.

    An approval whose revision or frame no longer matches the task does not
    stand, and the summary says none rather than showing a go-ahead that the
    gate would refuse.
    """
    approval = task.approval
    if approval is None or not _approval_stands(task):
        return None
    return {
        "frameId": approval.frame_id,
        "rev": approval.rev,
        "by": approval.by,
        "at": approval.at,
    }


def _approval_stands(task: Task) -> bool:
    """Whether the stored approval is still bound to this exact task state."""
    approval = task.approval
    if approval is None:
        return False
    if approval.rev != task.rev:
        return False
    return any(frame.id == approval.frame_id for frame in task.frames)


def _request_stands(task: Task) -> bool:
    """Whether the stored request is still bound to this exact task state."""
    request = task.request
    if request is None:
        return False
    if request.rev != task.rev:
        return False
    return any(frame.id == request.frame_id for frame in task.frames)


def _approval_detail(approval: Approval | None) -> dict[str, object] | None:
    """The whole approval a person gave, including the spec it covers."""
    if approval is None:
        return None
    return approval_to_dict(approval)


def _request_detail(
    request: WriteRequest | None,
) -> dict[str, object] | None:
    """The pending write request, including the canonical spec it proposes."""
    if request is None:
        return None
    return request_to_dict(request)


def _block_previews(task: Task, doc_id: str) -> dict[str, str]:
    """The previews the task's bounded material index holds for one document."""
    material = next(
        (item for item in task.materials if item.id == doc_id), None
    )
    previews: dict[str, str] = {}
    if material is not None:
        for block in material.blocks:
            if block.id:
                previews[block.id] = block.preview
    return previews


def material_view(material: Material) -> dict[str, object]:
    """One material as a bounded reading: its identity and a block index.

    The block source text is never here. A preview is clipped, and the index is
    cut to ``MAX_VIEW_BLOCKS``; ``blocksOmitted`` says how much of the material
    this reading leaves out. The same shape serves a tool result and a route.
    """
    blocks = [
        {
            "id": block.id,
            "tag": block.tag,
            "preview": block.preview[:MAX_BLOCK_PREVIEW],
        }
        for block in material.blocks[:MAX_VIEW_BLOCKS]
    ]
    count = max(0, material.block_count)
    if count < len(blocks):
        count = len(blocks)
    return {
        "id": material.id,
        "title": material.title[:MAX_MATERIAL_TITLE],
        "url": material.url,
        "kind": material.kind,
        "fidelity": material.fidelity,
        "state": material.state,
        "blockCount": count,
        "blocks": blocks,
        "blocksOmitted": max(0, count - len(blocks)),
    }


def material_summary(view: Mapping[str, object]) -> dict[str, object]:
    """An already-bounded material view without its block index.

    An event card needs the identity, not the index; the view itself is what
    ``material_view`` built, so this only drops the one key.
    """
    summary = dict(view)
    summary.pop("blocks", None)
    return summary


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _materials(value: object) -> tuple[Material, ...]:
    """The materials that can be read; one bad row does not hide the task."""
    if not isinstance(value, list):
        return ()
    materials: list[Material] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            materials.append(material_from_dict(item))
        except ValueError:
            continue
    return tuple(materials)


def _material_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    ids: list[str] = []
    for item in value:
        block = _text(item)
        if block and block not in ids:
            ids.append(block)
    return tuple(ids)


def _material_blocks(value: object) -> tuple[MaterialBlock, ...]:
    if not isinstance(value, list):
        return ()
    blocks: list[MaterialBlock] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        block = _text(item.get("id"))
        if not block:
            continue
        blocks.append(
            MaterialBlock(
                id=block,
                tag=_text(item.get("tag")),
                preview=_text(item.get("preview")),
            )
        )
    return tuple(blocks)


def _frames(value: object) -> tuple[Frame, ...]:
    """The frames that can be read; one bad row does not hide the task."""
    if not isinstance(value, list):
        return ()
    frames: list[Frame] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            frames.append(frame_from_dict(item))
        except ValueError:
            continue
    return tuple(frames)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _choice(
    raw: Mapping[str, object],
    key: str,
    allowed: Sequence[str],
    fallback: str,
) -> str:
    value = _text(raw.get(key)) or fallback
    if value not in allowed:
        raise ValueError(f"unknown {key}: {value!r}")
    return value


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = [
    "ACTIVE_OP_STATUSES",
    "FRAME_SOURCES",
    "MAX_BLOCK_PREVIEW",
    "MAX_CARD_SNIPPET",
    "MAX_MATERIAL_TITLE",
    "MAX_SECTION_BLOCKS",
    "MAX_SECTION_TITLE",
    "MAX_SPEC_SECTIONS",
    "MAX_SPEC_SLUG",
    "MAX_SPEC_TITLE",
    "MAX_VIEW_BLOCKS",
    "MAX_VIEW_CARDS",
    "MAX_VIEW_FRAME_BLOCKS",
    "MAX_VIEW_FRAMES",
    "MAX_VIEW_MATERIALS",
    "MAX_VIEW_OPS",
    "OP_KINDS",
    "TERMINAL_OP_STATUSES",
    "OP_STATUSES",
    "PAGE_SPEC_ACTIONS",
    "TASK_STATUSES",
    "TASK_VIEWS",
    "Approval",
    "Card",
    "FetchFn",
    "Frame",
    "FrameSource",
    "Material",
    "MaterialBlock",
    "Op",
    "SearchCard",
    "SearchFn",
    "StagedMaterial",
    "StageFn",
    "Task",
    "WriteRequest",
    "approval_from_dict",
    "approval_to_dict",
    "canonical_page_spec",
    "canonical_slug",
    "frame_from_dict",
    "frame_to_dict",
    "frame_view",
    "is_task_id",
    "material_from_dict",
    "material_summary",
    "material_to_dict",
    "material_view",
    "op_from_dict",
    "op_to_dict",
    "request_from_dict",
    "request_to_dict",
    "task_from_dict",
    "task_overview",
    "task_to_dict",
    "task_view",
]
