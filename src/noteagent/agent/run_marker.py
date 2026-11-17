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
from dataclasses import dataclass

MARKER_TYPE = "run_marker"

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
CANCELLED = "cancelled"
WAITING = "waiting"
INTERRUPTED = "interrupted"
RESUMED = "resumed"
ABANDONED = "abandoned"

#: A state a run does not leave on its own. ``interrupted`` is settled only in
#: the sense that a person must now choose resume or abandon.
SETTLED_STATES = frozenset(
    {FINISHED, FAILED, CANCELLED, WAITING, INTERRUPTED, ABANDONED}
)


@dataclass(frozen=True, slots=True)
class RunMarker:
    """One run's pointer: identity, state, last committed node, environment."""

    run_id: str
    state: str
    last_entry_id: str = ""
    model: str = ""
    toolset: str = ""
    config_hash: str = ""
    project_root: str = ""
    wiki_generation: str = ""
    source_fingerprint: str = ""
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
    for key, value in (
        ("model", marker.model),
        ("toolset", marker.toolset),
        ("configHash", marker.config_hash),
        ("projectRoot", marker.project_root),
        ("wikiGeneration", marker.wiki_generation),
        ("sourceFingerprint", marker.source_fingerprint),
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
        model=_text(entry.get("model")),
        toolset=_text(entry.get("toolset")),
        config_hash=_text(entry.get("configHash")),
        project_root=_text(entry.get("projectRoot")),
        wiki_generation=_text(entry.get("wikiGeneration")),
        source_fingerprint=_text(entry.get("sourceFingerprint")),
        resumes=_text(entry.get("resumes")),
        note=_text(entry.get("note")),
        created_at=_text(entry.get("createdAt")) or _text(entry.get("ts")),
        entry_id=_text(entry.get("id")),
    )


def latest_markers(
    entries: Sequence[Mapping[str, object]],
) -> dict[str, RunMarker]:
    """The latest marker for each run, in the order the runs first appeared."""
    out: dict[str, RunMarker] = {}
    for entry in entries:
        marker = parse_marker(entry)
        if marker is not None:
            out[marker.run_id] = marker
    return out


def latest_marker(
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

    A terminal marker wins: it is the explicit record. Without one, the last
    content entry decides. The marker is a hint; the transcript is the truth.
    """
    marker = latest_marker(entries)
    if marker is not None and marker.state in SETTLED_STATES:
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


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = [
    "ABANDONED",
    "CANCELLED",
    "FAILED",
    "FINISHED",
    "INTERRUPTED",
    "MARKER_TYPE",
    "RESUMED",
    "RUNNING",
    "RunMarker",
    "SETTLED_STATES",
    "TerminalInference",
    "WAITING",
    "infer_terminal",
    "latest_marker",
    "latest_markers",
    "marker_entry",
    "parse_marker",
]
