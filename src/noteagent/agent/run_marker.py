"""The run's durable pointer, beside the transcript that holds its content.

A marker says which run a stretch of entries belongs to, where it stopped, and
what configuration it ran with. It is a session entry, not a message: the
projection ignores it except for the note a resumed run leaves for the model.

The marker is a hint, not the truth. A process that dies before writing its
terminal marker still leaves a transcript whose shape says whether the run
finished, stopped at a tool call, or went to a wait; :func:`infer_terminal` reads
that shape. The terminal marker is what a clean run writes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

MARKER_TYPE = "run_marker"

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"
WAITING = "waiting"
INTERRUPTED = "interrupted"
ABANDONED = "abandoned"

#: A state a run does not leave on its own. ``interrupted`` is settled only in
#: the sense that a person must now choose resume or abandon.
SETTLED_STATES = frozenset(
    {FINISHED, FAILED, CANCELLED, WAITING, INTERRUPTED, ABANDONED}
)


@dataclass(frozen=True, slots=True)
class RunFingerprint:
    """What a run assumed: the model, the tools, and the project's digests."""

    model: str = ""
    toolset: str = ""
    config_hash: str = ""
    project_root: str = ""
    wiki_generation: str = ""
    source_fingerprint: str = ""

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
        *,
        model: str = "",
        toolset: str = "",
        config_hash: str = "",
    ) -> "RunFingerprint":
        """The marker's typed fingerprint from the composition root's mapping.

        The project terms live here, where the marker schema is defined; the
        kernel only passes the mapping and its own model/toolset through.
        """
        return cls(
            model=model or values.get("model", ""),
            toolset=toolset or values.get("toolset", ""),
            config_hash=config_hash or values.get("configHash", ""),
            project_root=values.get("projectRoot", ""),
            wiki_generation=values.get("wikiGeneration", ""),
            source_fingerprint=values.get("sourceFingerprint", ""),
        )


@dataclass(frozen=True, slots=True)
class RunMarker:
    """One run's pointer: identity, state, last committed node, environment."""

    run_id: str
    state: str
    last_entry_id: str = ""
    fingerprint: RunFingerprint = field(default_factory=RunFingerprint)
    #: The run this one continues, when it is a resume.
    resumes: str = ""
    #: A sentence the projection renders to the model on a resume.
    note: str = ""
    created_at: str = ""
    #: The marker's own entry id; filled by parsing a committed entry.
    entry_id: str = ""


@dataclass(frozen=True, slots=True)
class TerminalInference:
    """What the transcript says about the latest run when no terminal marker is there."""

    state: str
    last_entry_id: str = ""
    reason: str = ""


def marker_entry(marker: RunMarker) -> dict[str, object]:
    """The JSONL entry for *marker*. The store adds id, parentId and ts."""
    body: dict[str, object] = {
        "type": MARKER_TYPE,
        "runId": marker.run_id,
        "state": marker.state,
        "lastEntryId": marker.last_entry_id,
    }
    fingerprint = marker.fingerprint
    for key, value in (
        ("model", fingerprint.model),
        ("toolset", fingerprint.toolset),
        ("configHash", fingerprint.config_hash),
        ("projectRoot", fingerprint.project_root),
        ("wikiGeneration", fingerprint.wiki_generation),
        ("sourceFingerprint", fingerprint.source_fingerprint),
        ("resumes", marker.resumes),
        ("note", marker.note),
        ("createdAt", marker.created_at),
    ):
        if value:
            body[key] = value
    return body


def parse_marker(entry: Mapping[str, object]) -> RunMarker | None:
    """A committed marker, or ``None`` when the entry is not a usable one."""
    if entry.get("type") != MARKER_TYPE:
        return None
    run_id = _text(entry.get("runId"))
    state = _text(entry.get("state"))
    if not run_id or not state:
        return None
    return RunMarker(
        run_id=run_id,
        state=state,
        last_entry_id=_text(entry.get("lastEntryId")),
        fingerprint=RunFingerprint(
            model=_text(entry.get("model")),
            toolset=_text(entry.get("toolset")),
            config_hash=_text(entry.get("configHash")),
            project_root=_text(entry.get("projectRoot")),
            wiki_generation=_text(entry.get("wikiGeneration")),
            source_fingerprint=_text(entry.get("sourceFingerprint")),
        ),
        resumes=_text(entry.get("resumes")),
        note=_text(entry.get("note")),
        created_at=_text(entry.get("createdAt")) or _text(entry.get("ts")),
        entry_id=_text(entry.get("id")),
    )


def markers_by_run(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, RunMarker]:
    """The latest marker for each run, in the order the runs first appeared."""
    out: dict[str, RunMarker] = {}
    for entry in entries:
        marker = parse_marker(entry)
        if marker is not None:
            out[marker.run_id] = marker
    return out


def last_marker(
    entries: Sequence[Mapping[str, object]],
) -> RunMarker | None:
    """The last marker written, whichever run it belongs to."""
    for entry in reversed(entries):
        marker = parse_marker(entry)
        if marker is not None:
            return marker
    return None


def infer_terminal(
    entries: Sequence[Mapping[str, object]],
) -> TerminalInference:
    """What the latest run's shape says when its terminal marker is missing.

    A terminal marker wins only while no newer question follows it: a crash can
    leave an old run's ``finished`` marker ahead of a fresh user turn, and that
    turn is unanswered. Without a usable marker, the last content entry decides.
    """
    marker_index: int | None = None
    for index in range(len(entries) - 1, -1, -1):
        if parse_marker(entries[index]) is not None:
            marker_index = index
            break
    marker = (
        parse_marker(entries[marker_index])
        if marker_index is not None
        else None
    )
    if marker is not None and marker.state in SETTLED_STATES:
        later = entries[marker_index + 1 :] if marker_index is not None else ()
        if not any(_is_user_turn(entry) for entry in later):
            return TerminalInference(
                marker.state,
                marker.last_entry_id,
                "terminal marker",
            )

    content = [
        entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("type") != MARKER_TYPE
    ]
    if not content:
        return TerminalInference(INTERRUPTED, "", "empty session")

    last = content[-1]
    last_id = _text(last.get("id"))
    kind = last.get("type")

    if kind == "message" and last.get("role") == "assistant":
        if _text(last.get("stopReason")) == "aborted":
            return TerminalInference(CANCELLED, last_id, "aborted answer")
        calls = last.get("toolCalls")
        if isinstance(calls, list) and calls:
            return TerminalInference(
                INTERRUPTED, last_id, "tool call without result"
            )
        return TerminalInference(FINISHED, last_id, "final answer")

    if kind == "message" and last.get("role") == "toolResult":
        return TerminalInference(
            INTERRUPTED, last_id, "tool result without next turn"
        )

    if kind in ("wait_proposed", "wait_entered"):
        return TerminalInference(WAITING, last_id, "wait")

    if kind == "wait_completed":
        return TerminalInference(
            INTERRUPTED, last_id, "job result waiting for resume"
        )

    if kind == "message" and last.get("role") == "user":
        return TerminalInference(INTERRUPTED, last_id, "question without answer")

    return TerminalInference(INTERRUPTED, last_id, f"last entry {kind!r}")


def run_steps(
    entries: Sequence[Mapping[str, object]], run_id: str
) -> int:
    """How many tool-call rounds the run completed after its start marker."""
    start: int | None = None
    for index, entry in enumerate(entries):
        marker = parse_marker(entry)
        if (
            marker is not None
            and marker.run_id == run_id
            and marker.state == RUNNING
        ):
            start = index
    if start is None:
        return 0
    return sum(
        1
        for entry in entries[start + 1 :]
        if entry.get("type") == "message"
        and entry.get("role") == "assistant"
        and isinstance(entry.get("toolCalls"), list)
        and entry.get("toolCalls")
    )


def _is_user_turn(entry: Mapping[str, object]) -> bool:
    return entry.get("type") == "message" and entry.get("role") == "user"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = [
    "ABANDONED",
    "CANCELLED",
    "FAILED",
    "FINISHED",
    "INTERRUPTED",
    "MARKER_TYPE",
    "RUNNING",
    "RunFingerprint",
    "RunMarker",
    "SETTLED_STATES",
    "TerminalInference",
    "WAITING",
    "infer_terminal",
    "last_marker",
    "marker_entry",
    "markers_by_run",
    "parse_marker",
    "run_steps",
]
