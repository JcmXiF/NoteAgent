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

#: The kinds of operation the task knows. ``frame`` is pure state: the tool
#: applies it itself and logs it; the executor never claims it.
OpKind: TypeAlias = Literal["search", "pick", "frame"]

OP_KINDS: tuple[OpKind, ...] = ("search", "pick", "frame")

#: Where a frame came from. A person and the agent draw frames in this slice;
#: ``model`` is the auto-framing node the pipeline already has, for later.
FrameSource: TypeAlias = Literal["human", "agent", "model"]

FRAME_SOURCES: tuple[FrameSource, ...] = ("human", "agent", "model")

OpStatus: TypeAlias = Literal["requested", "running", "done", "failed"]

OP_STATUSES: tuple[OpStatus, ...] = ("requested", "running", "done", "failed")

#: An op in one of these has not reached an answer yet.
ACTIVE_OP_STATUSES = frozenset({"requested", "running"})

#: Who asked for an op. ``approve`` is the one kind only a human may write.
Actor: TypeAlias = Literal["agent", "human"]

#: The views ``inspect`` may ask for. Each is bounded; none is the task file.
TASK_VIEWS: tuple[str, ...] = ("summary", "cards", "materials", "frames")

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

    The shape the task stores now, before the human approve route exists: the
    frame and spec it is bound to, and the task revision it was given at.
    Approving again is ticket 04's job; invalidating it is this slice's.
    """

    frame_id: str
    spec: Mapping[str, object] = field(default_factory=dict)
    rev: int = 0
    by: str = "human"
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
    materials: tuple[Mapping[str, object], ...] = ()
    frames: tuple[Frame, ...] = ()
    plan: tuple[Mapping[str, object], ...] = ()
    approval: Approval | None = None
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


def op_to_dict(op: Op) -> dict[str, object]:
    return {
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
    }


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
    )


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
        "materials": [dict(item) for item in task.materials],
        "frames": [frame_to_dict(frame) for frame in task.frames],
        "plan": [dict(item) for item in task.plan],
        "ops": [op_to_dict(op) for op in task.ops],
    }
    if task.approval is not None:
        body["approval"] = approval_to_dict(task.approval)
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
        materials=_mappings(raw.get("materials")),
        frames=_frames(raw.get("frames")),
        plan=_mappings(raw.get("plan")),
        approval=(
            approval_from_dict(approval)
            if isinstance(approval, Mapping)
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
            "approval": _approval_summary(task.approval),
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
    raise ValueError(f"unknown task view: {view!r}")


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


def _approval_summary(approval: Approval | None) -> dict[str, object] | None:
    """The part of an approval a bounded view carries: what, who, when, at rev."""
    if approval is None:
        return None
    return {
        "frameId": approval.frame_id,
        "rev": approval.rev,
        "by": approval.by,
        "at": approval.at,
    }


def _block_previews(task: Task, doc_id: str) -> dict[str, str]:
    """The previews the task's bounded material index holds for one document."""
    material = next(
        (item for item in task.materials if _text(item.get("id")) == doc_id),
        None,
    )
    raw_blocks = material.get("blocks") if material is not None else None
    previews: dict[str, str] = {}
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            if not isinstance(item, Mapping):
                continue
            block = _text(item.get("id"))
            if block:
                previews[block] = _text(item.get("preview"))
    return previews


def material_view(raw: Mapping[str, object]) -> dict[str, object]:
    """One material as a bounded reading: its identity and a block index.

    The block source text is never here. A preview is clipped, and the index is
    cut to ``MAX_VIEW_BLOCKS``; ``blocksOmitted`` says how much of the material
    this reading leaves out. The same shape serves a tool result and a route.
    """
    raw_blocks = raw.get("blocks")
    blocks: list[dict[str, object]] = []
    if isinstance(raw_blocks, list):
        for item in raw_blocks[:MAX_VIEW_BLOCKS]:
            if not isinstance(item, Mapping):
                continue
            blocks.append(
                {
                    "id": _text(item.get("id")),
                    "tag": _text(item.get("tag")),
                    "preview": _text(item.get("preview"))[:MAX_BLOCK_PREVIEW],
                }
            )
    count = _count(raw.get("blockCount"))
    if count < len(blocks):
        count = len(blocks)
    return {
        "id": _text(raw.get("id")),
        "title": _text(raw.get("title"))[:MAX_MATERIAL_TITLE],
        "url": _text(raw.get("url")),
        "kind": _text(raw.get("kind")),
        "fidelity": _text(raw.get("fidelity")),
        "state": _text(raw.get("state")),
        "blockCount": count,
        "blocks": blocks,
        "blocksOmitted": max(0, count - len(blocks)),
    }


def material_summary(raw: Mapping[str, object]) -> dict[str, object]:
    """The part of a material an event card needs: no block index at all."""
    view = material_view(raw)
    view.pop("blocks", None)
    return view


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


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
    "MAX_VIEW_BLOCKS",
    "MAX_VIEW_CARDS",
    "MAX_VIEW_FRAME_BLOCKS",
    "MAX_VIEW_FRAMES",
    "MAX_VIEW_MATERIALS",
    "OP_KINDS",
    "OP_STATUSES",
    "TASK_STATUSES",
    "TASK_VIEWS",
    "Approval",
    "Card",
    "FetchFn",
    "Frame",
    "FrameSource",
    "MaterialBlock",
    "Op",
    "SearchCard",
    "SearchFn",
    "StagedMaterial",
    "StageFn",
    "Task",
    "approval_from_dict",
    "approval_to_dict",
    "frame_from_dict",
    "frame_to_dict",
    "frame_view",
    "is_task_id",
    "material_summary",
    "material_view",
    "op_from_dict",
    "op_to_dict",
    "task_from_dict",
    "task_to_dict",
    "task_view",
]
