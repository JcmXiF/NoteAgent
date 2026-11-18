"""The twelve end-to-end scenarios the recovery eval measures.

The list is fixed on purpose: every scenario names one failure mode the
recovery or rollback path claims to handle, and the checks are what a run must
observe. Prompts and seeds live in the suite runners; this module is the
catalogue a report, a filter and a reviewer read.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SCENARIOS", "Scenario"]


@dataclass(frozen=True, slots=True)
class Scenario:
    """One end-to-end scenario, its target boundary and its checks."""

    id: str
    suite: str
    title: str
    goal: str
    checks: tuple[str, ...]
    boundary: str = ""


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="R1",
        suite="recovery",
        title="Stream disconnect pauses the run",
        goal=(
            "Closing the SSE stream pauses the run instead of cancelling it, "
            "and a later resume reaches a terminal state."
        ),
        checks=(
            "interrupted surfaced with reason client_disconnect",
            "committed entries preserved",
            "no tool replayed",
            "resume reaches a terminal state",
        ),
        boundary="client_disconnect",
    ),
    Scenario(
        id="R2",
        suite="recovery",
        title="Crash between tool call and tool result",
        goal=(
            "Killing the server after tool calls are committed but before "
            "their results are leaves a run that resumes with outcome unknown."
        ),
        checks=(
            "startup scan marks the run interrupted",
            "resume synthesizes outcome unknown exactly once",
            "the tool is not replayed",
            "resume reaches a terminal state",
        ),
        boundary="tool_call_open",
    ),
    Scenario(
        id="R3",
        suite="recovery",
        title="Crash between question and start marker",
        goal=(
            "Killing the server after a new question is committed but before "
            "its run marker leaves a question the scan can recover, when a "
            "settled run precedes it."
        ),
        checks=(
            "a settled run precedes the question",
            "startup scan surfaces the question",
            "resume answers the question",
            "no old run is invented as interrupted",
        ),
        boundary="question_only",
    ),
    Scenario(
        id="R4",
        suite="recovery",
        title="A normal completion is not reported interrupted",
        goal=(
            "A run that finishes normally, followed by a restart, is neither "
            "marked interrupted nor offered a resume."
        ),
        checks=(
            "no interrupted marker",
            "session view offers no resume",
            "the terminal marker is unchanged",
        ),
        boundary="finished",
    ),
    Scenario(
        id="R5",
        suite="recovery",
        title="Fingerprint refuses or degrades a resume",
        goal=(
            "A changed model, toolset or config refuses the resume; a moved "
            "wiki or source set resumes with a stale-evidence warning."
        ),
        checks=(
            "config change returns session.notResumable",
            "wiki change resumes with a stale warning",
            "no unknown result is synthesized on refusal",
        ),
        boundary="fingerprint_change",
    ),
    Scenario(
        id="R6",
        suite="recovery",
        title="A model call retries before its first delta",
        goal=(
            "Two provider failures before the first content delta are retried, "
            "and the run finishes without replaying a tool."
        ),
        checks=(
            "the run reaches a terminal state",
            "retries are visible as status events",
            "no tool replayed",
        ),
        boundary="provider_retry",
    ),
    Scenario(
        id="B1",
        suite="rollback",
        title="Default restore protects a person's edit",
        goal=(
            "A page a run wrote and a person then edited is classified "
            "changed_by_person and skipped unless force is set."
        ),
        checks=(
            "preview classifies changed_by_person",
            "default restore skips the page",
            "forced restore returns the run's before version",
        ),
    ),
    Scenario(
        id="B2",
        suite="rollback",
        title="A later run's write is a conflict",
        goal=(
            "When run B rewrites a page run A wrote, restoring A classifies "
            "changed_by_run and skips it by default."
        ),
        checks=(
            "preview classifies changed_by_run",
            "default restore skips the page",
            "the later run's content is unchanged",
        ),
    ),
    Scenario(
        id="B3",
        suite="rollback",
        title="An untouched page restores, and restoring twice is idempotent",
        goal=(
            "An unchanged page restores to its before version, writes an undo "
            "effect and a restore history row, and a repeat writes nothing more."
        ),
        checks=(
            "restore succeeds and writes an undo effect",
            "page history gains a mode=restore row",
            "a repeat restore writes no second undo",
        ),
    ),
    Scenario(
        id="B4",
        suite="rollback",
        title="External, missing and renamed pages are classified",
        goal=(
            "unknown_external, missing and renamed pages are previewed "
            "correctly and skipped unless forced."
        ),
        checks=(
            "preview names the right classification",
            "default restore skips all three",
            "forced restore fails cleanly where it cannot restore",
        ),
    ),
    Scenario(
        id="B5",
        suite="rollback",
        title="A pipeline run rolls back through the same path",
        goal=(
            "A compile run returns the effect ids of the pages it wrote, and "
            "preview/restore through the pipeline routes is idempotent."
        ),
        checks=(
            "the run result carries effect ids",
            "preview classifies the written pages",
            "a repeat pipeline restore writes no second undo",
        ),
    ),
    Scenario(
        id="B6",
        suite="rollback",
        title="Two processes append to the journal without losing a line",
        goal=(
            "Concurrent writers to effects.jsonl keep every line and every "
            "effect queryable."
        ),
        checks=(
            "every appended effect is present",
            "queries return stable order",
            "no line is lost",
        ),
    ),
)
