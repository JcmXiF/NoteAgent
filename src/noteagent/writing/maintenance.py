"""The derived wiki files that follow a published page.

A publish writes one page through the page-write seam. The catalog around it —
``index.md``, ``log.md``, and the structure doctor — is derived state: it
follows the page and never gates it. Every step is idempotent and best effort,
so a crash or a retry finishes the catalog without writing the page twice, and
a derived file that cannot be written is reported, not rolled back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from noteagent.agent.clock import Clock, iso_timestamp
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.update_index import update_wiki_index
from noteagent.ingest.write_file_blocks import write_file_blocks
from noteagent.project.doctor import doctor_after_write
from noteagent.project.layout import WIKI_LOG, ProjectLayout
from noteagent.util.run_log import RunLogger

#: Doctor findings listed in the publish answer. The run log keeps the count;
#: a project with many old findings must not turn one publish into a wall.
DOCTOR_FINDINGS_LIMIT = 20


@dataclass(frozen=True, slots=True)
class PublishedPage:
    """One draft version's page, and how the catalog names it."""

    target_file: str
    title: str
    draft_version: int
    content: str


def maintain_published_page(
    layout: ProjectLayout,
    page: PublishedPage,
    *,
    moment: Clock,
) -> tuple[dict[str, str], ...]:
    """Update the index, append the log entry, and run the structure doctor.

    Returns the warnings the publish carries; each step runs even when an
    earlier one failed, and none of them can take the written page back.
    """
    warnings: list[dict[str, str]] = []
    run_logger = _open_run_log(layout)
    steps: tuple[tuple[str, str, Callable[[], None]], ...] = (
        (
            "index_failed",
            "wiki/index.md was not updated",
            lambda: _update_index(layout, page, run_logger),
        ),
        (
            "log_failed",
            "wiki/log.md was not updated",
            lambda: _append_log(layout, page, moment, run_logger),
        ),
        (
            "doctor_failed",
            "the structure doctor did not run",
            lambda: _run_doctor(layout, run_logger, warnings),
        ),
    )
    for code, label, step in steps:
        try:
            step()
        except Exception as err:  # noqa: BLE001 - the page is already on disk
            message = f"{label}: {err}"
            warnings.append({"code": code, "message": message})
            run_logger.warning(message)
    return tuple(warnings)


def _open_run_log(layout: ProjectLayout) -> RunLogger:
    """The run log, or a silent one when even its folder cannot be made."""
    try:
        return RunLogger.for_layout(layout)
    except OSError:
        return RunLogger.disabled()


def _update_index(
    layout: ProjectLayout,
    page: PublishedPage,
    run_logger: RunLogger,
) -> None:
    if not page.content.strip():
        raise ValueError("the published page could not be read back")
    updated = update_wiki_index(
        layout, [(page.target_file, page.content)]
    )
    run_logger.append(
        "publish.index",
        path=page.target_file,
        added=len(updated.added),
        skipped=len(updated.skipped),
    )


def _append_log(
    layout: ProjectLayout,
    page: PublishedPage,
    moment: Clock,
    run_logger: RunLogger,
) -> None:
    """Append this publish to ``wiki/log.md``; a retry adds nothing.

    The bullet naming the page and its draft version is the dedupe key: one
    draft version publishes once, so a crash-and-retry does not log twice.
    """
    marker, entry = _log_entry(page, moment)
    existing = (
        layout.wiki_log.read_text(encoding="utf-8")
        if layout.wiki_log.is_file()
        else ""
    )
    appended = marker not in existing
    if appended:
        result = write_file_blocks(
            layout, [FileBlock(path=WIKI_LOG, content=entry)]
        )
        if not result.written:
            message = (
                result.warnings[0].message
                if result.warnings
                else "the log entry was not written"
            )
            raise OSError(message)
    run_logger.append(
        "publish.log",
        path=WIKI_LOG,
        appended=appended,
        draft_version=page.draft_version,
    )


def _log_entry(page: PublishedPage, moment: Clock) -> tuple[str, str]:
    """The dedupe bullet, and the section it introduces."""
    marker = f"- `{page.target_file}` (draft {page.draft_version})"
    heading = " ".join(page.title.split()) or Path(page.target_file).stem
    day = iso_timestamp(moment)[:10]
    return marker, f"## [{day}] write | {heading}\n\n{marker}"


def _run_doctor(
    layout: ProjectLayout,
    run_logger: RunLogger,
    warnings: list[dict[str, str]],
) -> None:
    report = doctor_after_write(layout, dry_run=False)
    if report is None:
        return
    run_logger.append(
        "publish.doctor",
        errors=report.errors,
        warnings=report.warnings,
    )
    findings = [
        finding
        for finding in report.findings
        if finding.severity in {"error", "warning"}
    ]
    for finding in findings[:DOCTOR_FINDINGS_LIMIT]:
        item = {
            "severity": finding.severity,
            "code": finding.code,
            "message": finding.message,
        }
        if finding.path:
            item["path"] = finding.path
        warnings.append(item)
    omitted = len(findings) - DOCTOR_FINDINGS_LIMIT
    if omitted > 0:
        warnings.append(
            {
                "code": "doctor_findings_omitted",
                "message": (
                    f"{omitted} more doctor findings were not listed"
                ),
            }
        )


__all__ = [
    "DOCTOR_FINDINGS_LIMIT",
    "PublishedPage",
    "maintain_published_page",
]
