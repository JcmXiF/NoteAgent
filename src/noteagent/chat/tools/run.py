"""run: allowlisted product jobs (fetch, doctor). Not a shell."""

from __future__ import annotations

from typing import Any

from noteagent.acquire.run_fetch import run_web_fetch
from noteagent.chat.limits import ChatLimits
from noteagent.chat.tools.types import ToolObservation
from noteagent.project.doctor import doctor_project, format_doctor_report
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import truncate
from noteagent.util.run_log import RunLogger

TOOL_NAME = "run"
TOOL_DESCRIPTION = (
    "Run a named product job: doctor (wiki lint, no writes), or "
    "fetch (public GET preview, no raw/ write). Not a shell."
)
TOOL_ARGUMENT_HINT = "job: doctor | fetch; fetch needs url"
TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "job": {
            "type": "string",
            "enum": ["doctor", "fetch"],
            "description": (
                "doctor = lint current wiki; "
                "fetch = preview one public URL"
            ),
        },
        "url": {
            "type": "string",
            "description": "Required when job=fetch",
        },
    },
    "required": ["job"],
}


def run(
    layout: ProjectLayout,
    arguments: dict[str, Any],
    limits: ChatLimits,
) -> tuple[ToolObservation, ...]:
    job = str(arguments.get("job") or "").strip().lower()
    if job == "doctor":
        return _run_doctor(layout, limits)
    if job == "fetch":
        url = str(arguments.get("url") or "").strip()
        if not url:
            raise ValueError("run fetch requires url")
        return _run_fetch(layout, url, limits)
    raise ValueError("run job must be doctor or fetch")


def _run_doctor(
    layout: ProjectLayout, limits: ChatLimits
) -> tuple[ToolObservation, ...]:
    report = doctor_project(layout)
    text = format_doctor_report(report)
    body = truncate(text, limits.max_pages_chars)
    return (
        ToolObservation(
            tool=TOOL_NAME,
            title="doctor",
            target="doctor",
            content=body,
            meta={
                "job": "doctor",
                "errors": report.errors,
                "warnings": report.warnings,
                "findings": len(report.findings),
                "content_pages": report.content_pages,
                "truncated": body != text,
            },
        ),
    )


def _run_fetch(
    layout: ProjectLayout, url: str, limits: ChatLimits
) -> tuple[ToolObservation, ...]:
    try:
        capture = run_web_fetch(
            url,
            run_logger=RunLogger.for_layout(layout),
            caller="chat",
        )
    except Exception as err:
        return (
            ToolObservation(
                tool=TOOL_NAME,
                title="fetch",
                target=url,
                content=str(err),
                meta={"job": "fetch", "error": True, "url": url},
            ),
        )
    # Reason codes, not labels: the label is the reader's language, and this text
    # is read by the model rather than shown.
    labels = ", ".join(capture.reasons) or "(none)"
    raw_md = capture.markdown.strip()
    preview = truncate(raw_md, limits.max_page_chars)
    http_status = capture.http_status if capture.http_status is not None else "(none)"
    lines = [
        f"title: {capture.title or '(none)'}",
        f"url: {capture.url}",
        f"final_url: {capture.final_url or capture.url}",
        f"fidelity: {capture.fidelity}",
        f"http_status: {http_status}",
        f"method: {capture.capture_method}",
        f"reasons: {labels}",
        f"chars: {capture.char_count}",
        "Preview only — not written to raw/, not wiki. "
        "Do not treat this as an ingested note.",
        "",
        preview or "(no text)",
    ]
    return (
        ToolObservation(
            tool=TOOL_NAME,
            title=capture.title or url,
            target=url,
            content="\n".join(lines),
            meta={
                "job": "fetch",
                "url": capture.url,
                "fidelity": capture.fidelity,
                "http_status": capture.http_status,
                "not_source": True,
                "truncated": preview != raw_md,
            },
        ),
    )
