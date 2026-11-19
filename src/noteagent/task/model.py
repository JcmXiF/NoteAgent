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

#: The kinds of operation the executor knows. Later slices add frame/write.
OpKind: TypeAlias = Literal["search", "pick"]

OP_KINDS: tuple[OpKind, ...] = ("search", "pick")

OpStatus: TypeAlias = Literal["requested", "running", "done", "failed"]

OP_STATUSES: tuple[OpStatus, ...] = ("requested", "running", "done", "failed")

#: An op in one of these has not reached an answer yet.
ACTIVE_OP_STATUSES = frozenset({"requested", "running"})

#: Who asked for an op. ``approve`` is the one kind only a human may write.
Actor: TypeAlias = Literal["agent", "human"]

#: The views ``inspect`` may ask for. Each is bounded; none is the task file.
TASK_VIEWS: tuple[str, ...] = ("summary", "cards", "materials")

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
    frames: tuple[Mapping[str, object], ...] = ()
    plan: tuple[Mapping[str, object], ...] = ()
    approval: Mapping[str, object] | None = None
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
        "frames": [dict(item) for item in task.frames],
        "plan": [dict(item) for item in task.plan],
        "ops": [op_to_dict(op) for op in task.ops],
    }
    if task.approval is not None:
        body["approval"] = dict(task.approval)
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
        frames=_mappings(raw.get("frames")),
        plan=_mappings(raw.get("plan")),
        approval=dict(approval) if isinstance(approval, Mapping) else None,
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
    raise ValueError(f"unknown task view: {view!r}")


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
    "MAX_BLOCK_PREVIEW",
    "MAX_CARD_SNIPPET",
    "MAX_MATERIAL_TITLE",
    "MAX_VIEW_BLOCKS",
    "MAX_VIEW_CARDS",
    "MAX_VIEW_MATERIALS",
    "OP_KINDS",
    "OP_STATUSES",
    "TASK_STATUSES",
    "TASK_VIEWS",
    "Card",
    "FetchFn",
    "MaterialBlock",
    "Op",
    "SearchCard",
    "SearchFn",
    "StagedMaterial",
    "StageFn",
    "Task",
    "is_task_id",
    "material_summary",
    "material_view",
    "op_from_dict",
    "op_to_dict",
    "task_from_dict",
    "task_to_dict",
    "task_view",
]
