"""write: topic map / confirmed ingest. Never a raw file editor."""

from __future__ import annotations

from typing import Any

from noteagent.api.topic_gate import run_ingest_confirmed_job, run_topic_map_job
from noteagent.chat.limits import ChatLimits
from noteagent.chat.tools.types import ToolObservation
from noteagent.ingest.clips import GATE_ACTIONS, ClipStore, load_clip_store
from noteagent.ingest.topic_map import TopicMap, parse_topic_map
from noteagent.llm.client import LlmError
from noteagent.llm.config import LlmConfigError
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import truncate

TOOL_NAME = "write"
TOOL_DESCRIPTION = (
    "Propose a topic map from current clip collections, or compile pages the "
    "human already checked. The workbench persist step stores selected "
    "originals under raw/ before compile; this tool does not write "
    "raw/ itself. Collections must already point at those files. "
    "Does not edit wiki files directly, and ignores any confirmed list you invent."
)
TOOL_ARGUMENT_HINT = (
    "action: propose_topic_map | ingest_confirmed; optional intent for planning"
)
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["propose_topic_map", "ingest_confirmed"],
            "description": (
                "propose_topic_map plans pages and stops for the human gate. "
                "ingest_confirmed writes only persisted write checkboxes."
            ),
        },
        "intent": {
            "type": "string",
            "description": (
                "Optional bias for this round (not a required page title). "
                "Empty is allowed: induce titles from clip collections."
            ),
        },
    },
    "required": ["action"],
}

_STOP = (
    "Stop here. Do not call ingest_confirmed until the human checks "
    "write/skip/mention on the plan board, or compiles the ticked pages there. "
    "Do not claim wiki pages were written."
)

#: The failures a planning or compile job reports as a result rather than a crash.
_JOB_ERRORS = (ValueError, FileNotFoundError, LlmConfigError, LlmError, OSError, RuntimeError)


def write(
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    action = str(arguments.get("action") or "").strip().lower()
    if action == "propose_topic_map":
        return _propose(layout, arguments, limits)
    if action == "ingest_confirmed":
        return _ingest(layout, limits)
    raise ValueError("write action must be propose_topic_map or ingest_confirmed")


def _propose(
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    store = load_clip_store(layout)
    if not store.collections:
        return _observation(
            "propose_topic_map",
            "No clip collections. The human must marquee blocks into the "
            "candidate list before planning. " + _STOP,
            needs_human=True,
        )
    raw_intent = arguments.get("intent")
    if raw_intent is None:
        intent = store.intent.strip()
    else:
        intent = str(raw_intent).strip()
    try:
        payload = run_topic_map_job(layout, intent=intent)
    except _JOB_ERRORS as err:
        return _observation("propose_topic_map", str(err), error=True)
    body = truncate(_render_map(payload), limits.max_pages_chars)
    return _observation(
        "propose_topic_map",
        body + "\n\n" + _STOP,
        needs_human=True,
        extra={
            "action": "propose_topic_map",
            "intent": payload.get("intent") or intent,
            "wrote_wiki": False,
        },
    )


def _ingest(layout: ProjectLayout, limits: ChatLimits) -> tuple[ToolObservation, ...]:
    # Ignore any confirmed list the model invented; only clips.json counts.
    store = load_clip_store(layout)
    if not store.collections:
        return _observation(
            "ingest_confirmed",
            "No clip collections to compile. " + _STOP,
            needs_human=True,
        )
    if store.topic_map is None:
        return _observation(
            "ingest_confirmed",
            "No topic map on disk. Call propose_topic_map first, then wait "
            "for the human gate. " + _STOP,
            needs_human=True,
        )
    try:
        topic_map = parse_topic_map(
            store.topic_map,
            intent=store.intent,
            collections=store.collections,
            layout=layout,
        )
        confirmed = _confirmed_from_store(store, topic_map)
    except ValueError as err:
        return _observation("ingest_confirmed", str(err), error=True)
    writes = [item for item in confirmed if item.get("action") == "write"]
    if not writes:
        return _observation(
            "ingest_confirmed",
            "No persisted write checkboxes. Defaults on the board are not a "
            "gate. The human must mark at least one page as write or update "
            "(saved to clips.json), or compile the ticked pages on the board. "
            + _STOP,
            needs_human=True,
            extra={"action": "ingest_confirmed", "wrote_wiki": False},
        )
    try:
        payload = run_ingest_confirmed_job(
            layout,
            confirmed,
            intent=store.intent or topic_map.intent,
        )
    except _JOB_ERRORS as err:
        return _observation("ingest_confirmed", str(err), error=True)
    results = payload.get("results") or []
    written: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        for row in item.get("written") or []:
            if isinstance(row, dict) and row.get("path"):
                written.append(str(row["path"]))
    summary = _render_ingest(confirmed, written, payload)
    return _observation(
        "ingest_confirmed",
        truncate(summary, limits.max_pages_chars),
        extra={
            "action": "ingest_confirmed",
            "wrote_wiki": bool(written),
            "written": written,
            "write_count": len(writes),
        },
    )


def _confirmed_from_store(store: ClipStore, topic_map: TopicMap) -> list[dict[str, Any]]:
    by_slug = {item.slug: item for item in store.decisions}
    pages: list[dict[str, Any]] = []
    for node in topic_map.nodes():
        decision = by_slug.get(node.slug)
        action = decision.action if decision is not None else "skip"
        if action not in GATE_ACTIONS:
            action = "skip"
        allowed = {section.id for section in node.outline}
        section_ids: list[str] = []
        if action == "write" and decision is not None:
            section_ids = [sid for sid in decision.section_ids if sid in allowed]
        pages.append(
            {
                "name": node.title,
                "slug": node.slug,
                "wikiPath": node.wiki_path,
                "action": action,
                "sectionIds": section_ids,
            }
        )
    return pages


def _render_map(payload: dict[str, Any]) -> str:
    notes = str(payload.get("notes") or "").strip()
    lines = [
        "Topic map saved to clips.json. Wiki was not written.",
        f"intent: {payload.get('intent') or '(none)'}",
    ]
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        lines.append("candidates:")
        for item in candidates:
            if isinstance(item, dict):
                lines.append("  " + _node_line("candidate", item))
    else:
        raw_primary = payload.get("primary")
        primary = raw_primary if isinstance(raw_primary, dict) else {}
        raw_also = payload.get("alsoInSources")
        also = raw_also if isinstance(raw_also, list) else []
        lines.append(_node_line("primary", primary))
        if also:
            lines.append("also_in_sources:")
            for item in also:
                if isinstance(item, dict):
                    lines.append("  " + _node_line("also", item))
    if notes:
        lines.append(f"notes: {notes}")
    edges = payload.get("edges")
    if isinstance(edges, list) and edges:
        lines.append("edges:")
        for item in edges:
            if not isinstance(item, dict):
                continue
            src = item.get("from") or item.get("source") or ""
            dst = item.get("to") or item.get("target") or ""
            rel = item.get("relation") or ""
            lines.append(f"  {src} -{rel}-> {dst}")
    return "\n".join(lines)


def _node_line(kind: str, node: dict[str, Any]) -> str:
    name = node.get("name") or "(unnamed)"
    slug = node.get("slug") or ""
    path = node.get("wikiPath") or ""
    coverage = node.get("coverage") or ""
    relation = node.get("relationToPrimary") or kind
    label = coverage or relation
    return f"{name} [{slug}] {path} ({label})"


def _render_ingest(
    confirmed: list[dict[str, Any]],
    written: list[str],
    payload: dict[str, Any],
) -> str:
    writes = [item["slug"] for item in confirmed if item.get("action") == "write"]
    skips = [item["slug"] for item in confirmed if item.get("action") == "skip"]
    mentions = [item["slug"] for item in confirmed if item.get("action") == "mention"]
    errors = payload.get("errors") or []
    lines = [
        "Compiled only persisted human write checkboxes.",
        "write: " + (", ".join(writes) if writes else "(none)"),
        "skip: " + (", ".join(skips) if skips else "(none)"),
        "mention: " + (", ".join(mentions) if mentions else "(none)"),
        "written: " + (", ".join(written) if written else "(none)"),
    ]
    if errors:
        lines.append("errors: " + "; ".join(str(item) for item in errors))
    doctor_lines = _render_doctor(payload)
    if doctor_lines:
        lines.extend(doctor_lines)
    if written:
        lines.append("Tell the human which [[slug]] pages were updated.")
    return "\n".join(lines)


def _render_doctor(payload: dict[str, Any]) -> list[str]:
    results = payload.get("results") or []
    lines: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        doctor = item.get("doctor")
        if not isinstance(doctor, dict):
            continue
        errors = doctor.get("errors") or 0
        warnings = doctor.get("warnings") or 0
        count = doctor.get("findingCount") or 0
        lines.append(
            f"doctor: {errors} error / {warnings} warning / {count} finding"
        )
        findings = doctor.get("findings") or []
        for row in findings[:5]:
            if not isinstance(row, dict):
                continue
            loc = row.get("path") or ""
            suffix = f" @ {loc}" if loc else ""
            lines.append(
                f"- [{row.get('severity')}/{row.get('code')}] "
                f"{row.get('message')}{suffix}"
            )
        if doctor.get("truncated"):
            lines.append("- (doctor findings truncated)")
    return lines


def _observation(
    target: str,
    content: str,
    *,
    needs_human: bool = False,
    error: bool = False,
    extra: dict[str, Any] | None = None,
) -> tuple[ToolObservation, ...]:
    meta: dict[str, Any] = {"action": target, "wrote_wiki": False}
    if extra:
        meta.update(extra)
    if needs_human:
        meta["needs_human"] = True
    if error:
        meta["error"] = True
    return (
        ToolObservation(
            tool=TOOL_NAME,
            title=target,
            target=target,
            content=content,
            meta=meta,
        ),
    )
