"""The session entry an executor boundary writes when a task op finishes.

A task op is not a tool call: no model asked for it inside the conversation's
current run, and its result belongs to the task. The entry is the continuation
record: the bounded delta the model reads when the session resumes, and the
notice a reconnecting reader sees. It is append-only and names its op, so a
duplicate delivery or a restart can recognise that this op already continued the
conversation once.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

TASK_OP_RESULT_TYPE = "task_op_result"

#: How many cards, and how much of one card's text, the resumed model reads.
#: A card is a pointer to the task view, not the whole search result.
MAX_RESUMED_CARDS = 10
MAX_RESUMED_CARD_TEXT = 300

#: How much of a summary, a reason or an error the resumed model reads.
MAX_RESUMED_TEXT = 600


def task_op_result_entry(
    *,
    op_id: str,
    kind: str,
    status: str,
    rev: int,
    result: Mapping[str, object],
) -> dict[str, object]:
    """The committed entry that delivers one finished op's delta."""
    return {
        "type": TASK_OP_RESULT_TYPE,
        "opId": op_id,
        "kind": kind,
        "status": status,
        "rev": rev,
        "result": dict(result),
    }


def has_task_op_result(
    entries: Sequence[Mapping[str, object]], op_id: str
) -> bool:
    """Whether this op already continued the session once."""
    return any(
        entry.get("type") == TASK_OP_RESULT_TYPE and entry.get("opId") == op_id
        for entry in entries
    )


def latest_task_op(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    """The latest op delivery, as the session view carries it, or ``None``."""
    for entry in reversed(entries):
        if entry.get("type") != TASK_OP_RESULT_TYPE:
            continue
        payload = task_op_payload(entry)
        if payload is not None:
            return payload
    return None


def task_op_payload(
    entry: Mapping[str, object],
) -> dict[str, object] | None:
    """One delivery as a bounded payload, or ``None`` when it cannot be read."""
    op_id = _text(entry.get("opId"))
    if not op_id:
        return None
    result = entry.get("result")
    return {
        "opId": op_id,
        "kind": _text(entry.get("kind")),
        "status": _text(entry.get("status")),
        "rev": entry.get("rev") if isinstance(entry.get("rev"), int) else 0,
        "result": dict(result) if isinstance(result, Mapping) else {},
    }


def task_observed_rev(
    entries: Sequence[Mapping[str, object]],
) -> int | None:
    """The newest task revision a session's committed entries show it saw.

    A task tool result carries the revision in its card meta; a delivered op
    result carries it at the entry's top level. Scanning backwards finds the
    revision the conversation last read, which a restarted core uses in place of
    the in-memory observation it lost: a change made while the core was down is
    then a ``conflict`` rather than a silent overwrite. ``None`` means the
    session never saw the task.
    """
    for entry in reversed(entries):
        if entry.get("type") == TASK_OP_RESULT_TYPE:
            value = entry.get("rev")
        elif entry.get("role") == "toolResult":
            meta = entry.get("meta")
            value = meta.get("rev") if isinstance(meta, Mapping) else None
        else:
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def render_task_op_result(entry: Mapping[str, object]) -> str:
    """What the model reads when an executor-finished op resumes the session.

    The rendering is a whitelist: the delta's known scalar fields and bounded
    cards or material identity. The task file, the op log, the effect ids and
    the page's text are not part of what a resumed model can see here; it calls
    ``inspect`` for anything more.
    """
    op_id = _text(entry.get("opId")) or "the finished op"
    kind = _text(entry.get("kind")) or "task"
    status = _text(entry.get("status")) or "done"
    rev = entry.get("rev") if isinstance(entry.get("rev"), int) else 0
    result = entry.get("result")
    result = result if isinstance(result, Mapping) else {}
    lines = [
        f"Task op {op_id} ({kind}) finished: {status}, at task rev {rev}."
    ]
    summary = _text(result.get("summary"))
    page = _text(result.get("page"))
    error = _text(result.get("error"))
    reason = _text(result.get("reason"))
    if summary:
        lines.append(summary[:MAX_RESUMED_TEXT])
    elif page:
        lines.append(f"Page: {page}.")
    if error:
        lines.append(f"Error: {error[:MAX_RESUMED_TEXT]}")
    elif reason and status != "done" and not summary:
        lines.append(f"Reason: {reason[:MAX_RESUMED_TEXT]}")
    if kind == "search":
        lines.extend(_search_lines(result))
    elif kind == "pick":
        lines.extend(_material_lines(result))
    lines.append(_next_step(kind))
    return "\n".join(lines)


def _search_lines(result: Mapping[str, object]) -> list[str]:
    raw = result.get("cards")
    cards = (
        [item for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, list)
        else []
    )
    hit_count = result.get("hits")
    count = (
        hit_count
        if isinstance(hit_count, int) and not isinstance(hit_count, bool)
        else len(cards)
    )
    if not cards:
        return [f"The search returned {count} card(s)."]
    lines = [f"The search returned {count} card(s):"]
    for card in cards[:MAX_RESUMED_CARDS]:
        title = _text(card.get("title"))[:MAX_RESUMED_CARD_TEXT]
        url = _text(card.get("url"))[:MAX_RESUMED_CARD_TEXT]
        card_id = _text(card.get("id"))
        label = f"[{card_id}] " if card_id else ""
        lines.append(f"- {label}{title} — {url}" if url else f"- {label}{title}")
    omitted = len(cards) - MAX_RESUMED_CARDS
    if omitted > 0:
        lines.append(f"{omitted} more card(s) are in the task.")
    return lines


def _material_lines(result: Mapping[str, object]) -> list[str]:
    material = result.get("material")
    if not isinstance(material, Mapping):
        return []
    ident = _text(material.get("id"))
    title = _text(material.get("title"))[:MAX_RESUMED_CARD_TEXT]
    return [f"The material is [{ident}] {title}." if ident else f"The material is {title}."]


def _next_step(kind: str) -> str:
    view = {"search": "cards", "pick": "materials", "write": "result"}.get(
        kind, "summary"
    )
    return (
        f'The op has finished; do not run it again. Call inspect("{view}") '
        "if you need the current task view."
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "MAX_RESUMED_CARDS",
    "MAX_RESUMED_CARD_TEXT",
    "MAX_RESUMED_TEXT",
    "TASK_OP_RESULT_TYPE",
    "has_task_op_result",
    "latest_task_op",
    "render_task_op_result",
    "task_observed_rev",
    "task_op_payload",
    "task_op_result_entry",
]
