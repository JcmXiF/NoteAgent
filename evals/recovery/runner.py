"""Run the recovery and rollback suites against a real server.

Each scenario is a small experiment: seed a project, drive the real HTTP/SSE
API, kill or disconnect at a known boundary, restart, resume, and measure. The
runner owns the retry rule: a run that never reaches its boundary is ``missed``
and is retried, but it never enters a product metric's denominator.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.recovery.boundary import (
    QUESTION_ONLY,
    TERMINAL,
    TOOL_CALL_OPEN,
    classify_file,
    pending_calls,
    read_entries,
    wait_for_boundary,
)
from evals.recovery.client import Client, Event, RequestFailed
from evals.recovery.dataset import SCENARIOS, Scenario
from evals.recovery.metrics import ScenarioRun
from evals.recovery.process import ServerHandle, start_server
from evals.recovery.proxy import FailingProxy
from evals.recovery.report import RecoveryReport
from noteagent.effects.journal import EffectJournal
from noteagent.ingest.frontmatter import parse_page
from noteagent.ingest.page_history import history_entries
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.write_file_blocks import WriteContext, write_file_blocks
from noteagent.project.init import init_project
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.editing import rename_page

__all__ = ["run_eval", "run_scenario"]

PROJECT_NAME = "eval"
MAX_MISSED_RETRIES = 3
INTERRUPT_TIMEOUT_S = 60.0
PIPELINE_TIMEOUT_S = 240.0

PROMPT_READ = (
    "Call the wiki_read tool with slug 'concepts/alpha', then explain what "
    "the page is about in three detailed paragraphs."
)
PROMPT_PLAIN = "Answer in one sentence: what is two plus two?"
PROMPT_QUESTION = "What is two plus two? Answer with one word."


@dataclass(slots=True)
class Attempt:
    """One scenario attempt's verdict and the fields the metrics need."""

    status: str = "passed"
    observed_boundary: str = ""
    detail: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def missed(self) -> bool:
        return self.status == "missed"


@dataclass(slots=True)
class RunContext:
    """Everything one attempt owns: its temp project, servers and clients."""

    scenario: Scenario
    rep: int
    attempt: int
    root: Path
    home: Path
    project: Path
    layout: ProjectLayout
    delay_ms: float
    servers: list[ServerHandle] = field(default_factory=list)
    clients: list[Client] = field(default_factory=list)

    def start(
        self, extra_env: dict[str, str] | None = None
    ) -> tuple[ServerHandle, Client]:
        server = start_server(
            self.project,
            artifacts=self.root,
            home=self.home,
            delay_ms=self.delay_ms,
            extra_env=extra_env,
        )
        client = Client(server.url)
        self.servers.append(server)
        self.clients.append(client)
        _wait_health(client)
        return server, client

    def session_path(self, session_id: str) -> Path:
        return self.project / ".noteagent" / "sessions" / f"{session_id}.jsonl"

    def cleanup(self) -> None:
        for client in self.clients:
            client.close()
        for server in self.servers:
            if server.child.alive:
                server.stop()


def run_eval(
    *,
    suites: tuple[str, ...] = ("recovery", "rollback"),
    reps: int = 3,
    filter_text: str = "",
    artifacts: Path,
    model: str = "configured",
    delay_ms: float = 300.0,
    progress: Any = print,
) -> RecoveryReport:
    """Run the selected suites and return the report's inputs."""
    artifacts = Path(artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    started_at = utcnow()
    started = time.monotonic()
    runs: list[ScenarioRun] = []
    for scenario in SCENARIOS:
        if scenario.suite not in suites:
            continue
        if filter_text and filter_text.lower() not in scenario.id.lower():
            continue
        for rep in range(1, reps + 1):
            run = run_scenario(
                scenario,
                rep,
                artifacts=artifacts,
                delay_ms=delay_ms,
            )
            runs.append(run)
            progress(
                f"{scenario.id} rep {rep}: {run.status}"
                + (f" ({run.detail})" if run.detail else "")
            )
    return RecoveryReport(
        runs=tuple(runs),
        started_at=started_at,
        duration_s=time.monotonic() - started,
        model=model,
        artifacts=str(artifacts),
    )


def run_scenario(
    scenario: Scenario,
    rep: int,
    *,
    artifacts: Path,
    delay_ms: float,
) -> ScenarioRun:
    """Run one scenario with the missed-boundary retry rule."""
    implementation = _IMPLEMENTATIONS[scenario.id]
    last: Attempt | None = None
    for attempt_number in range(1, MAX_MISSED_RETRIES + 2):
        root = (
            Path(artifacts)
            / scenario.id
            / f"rep-{rep}"
            / f"attempt-{attempt_number}"
        )
        root.mkdir(parents=True, exist_ok=True)
        home = root / "home"
        # With NOTEAGENT_HOME set, the home itself is the projects container.
        project = home / PROJECT_NAME
        layout = init_project(project)
        context = RunContext(
            scenario=scenario,
            rep=rep,
            attempt=attempt_number,
            root=root,
            home=home,
            project=project,
            layout=layout,
            delay_ms=delay_ms,
        )
        try:
            attempt = implementation(context)
        except Exception as error:  # noqa: BLE001 - reported as infra
            (root / "error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            attempt = Attempt(
                status="infra",
                detail=f"{type(error).__name__}: {error}",
            )
        finally:
            context.cleanup()
        attempt.observed_boundary = attempt.observed_boundary or classify_file(
            _first_session_file(context)
        )
        if not attempt.missed:
            return _record(scenario, rep, attempt)
        last = attempt
    return _record(
        scenario,
        rep,
        Attempt(
            status="infra",
            observed_boundary=last.observed_boundary if last else "",
            detail=(
                "never hit the target boundary after "
                f"{MAX_MISSED_RETRIES + 1} attempts"
                + (f": {last.detail}" if last and last.detail else "")
            ),
            fields=last.fields if last else {},
        ),
    )


def _record(scenario: Scenario, rep: int, attempt: Attempt) -> ScenarioRun:
    return ScenarioRun(
        scenario=scenario.id,
        suite=scenario.suite,
        rep=rep,
        status=attempt.status,
        intended_boundary=scenario.boundary,
        observed_boundary=attempt.observed_boundary,
        detail=attempt.detail,
        **attempt.fields,
    )


# ---------------------------------------------------------------------------
# Recovery suite
# ---------------------------------------------------------------------------


def _prepare(ctx: RunContext) -> tuple[ServerHandle, Client, str]:
    _seed_pages(ctx.layout)
    server, client = ctx.start()
    session_id = client.create_session(ctx.scenario.id)
    return server, client, session_id


def _r1(ctx: RunContext) -> Attempt:
    server, client, session_id = _prepare(ctx)
    try:
        with client.stream_query(session_id, PROMPT_READ) as stream:
            first_token = None
            for event in stream:
                if event.kind == "token":
                    first_token = event
                    break
        if first_token is None:
            return Attempt(status="failed", detail="no token before stream end")
        interrupted = _wait_interrupted(client, session_id)
        if interrupted is None:
            boundary = classify_file(ctx.session_path(session_id))
            if boundary == TERMINAL:
                return Attempt(
                    status="missed",
                    observed_boundary=boundary,
                    detail="the answer completed before the disconnect landed",
                )
            return _detection_failure(
                ctx,
                session_id,
                detail="the disconnect did not leave an interrupted run",
                observed=boundary,
            )
        before = read_entries(ctx.session_path(session_id))
        observed = classify_file(ctx.session_path(session_id))
        try:
            events = client.resume(session_id, str(interrupted["runId"]))
        except RequestFailed as error:
            return Attempt(
                status="failed",
                observed_boundary=observed,
                detail=f"resume refused: {error}",
                fields=_recovery_fields(
                    interrupted=True,
                    detected=True,
                    normal=False,
                    false_interrupt=None,
                    before=before,
                    after=before,
                    resume_attempted=True,
                    resume_succeeded=False,
                    budget_exceeded=None,
                    terminal=False,
                    recovery_seconds=None,
                ),
            )
        after = read_entries(ctx.session_path(session_id))
        done, error = _terminal_events(events)
        succeeded = _run_finished(done, error)
        fields = _recovery_fields(
            interrupted=True,
            detected=True,
            normal=False,
            false_interrupt=None,
            before=before,
            after=after,
            resume_attempted=True,
            resume_succeeded=succeeded,
            budget_exceeded=_budget_exceeded(after),
            terminal=succeeded,
            recovery_seconds=None,
        )
        reason_ok = interrupted.get("reason") == "client_disconnect"
        passed = (
            succeeded
            and reason_ok
            and fields["preserved_entries"] == fields["committed_entries"]
            and fields["replayed_tools"] == 0
        )
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=observed,
            detail=(
                ""
                if passed
                else f"resume finished={succeeded} reason={interrupted.get('reason')}"
            ),
            fields=fields,
        )
    finally:
        _close(server, client)


def _r2(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    server, client = ctx.start()
    session_id = client.create_session(ctx.scenario.id)
    path = ctx.session_path(session_id)
    outcome: dict[str, Any] = {}

    def ask() -> None:
        try:
            outcome["events"] = client.query(session_id, PROMPT_READ)
        except Exception as error:  # noqa: BLE001 - the kill is the point
            outcome["error"] = error

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    try:
        observed = wait_for_boundary(path, TOOL_CALL_OPEN, timeout_s=60.0)
        if observed is None:
            return Attempt(
                status="missed",
                observed_boundary=classify_file(path),
                detail="the tool-call boundary was not observed",
            )
        server.kill()
        client.close()
        thread.join(timeout=10.0)

        restart_started = time.monotonic()
        server2, client2 = ctx.start()
        interrupted = _wait_interrupted(client2, session_id)
        recovery_seconds = time.monotonic() - restart_started
        if interrupted is None:
            return _detection_failure(
                ctx,
                session_id,
                detail="the scan did not mark the crashed run interrupted",
                observed=observed.boundary,
                terminal=False,
                recovery_seconds=recovery_seconds,
            )
        before = read_entries(path)
        open_calls = pending_calls(before)
        try:
            events = client2.resume(session_id, str(interrupted["runId"]))
        except RequestFailed as refusal:
            after = read_entries(path)
            fields = _recovery_fields(
                interrupted=True,
                detected=True,
                normal=False,
                false_interrupt=None,
                before=before,
                after=after,
                resume_attempted=True,
                resume_succeeded=False,
                budget_exceeded=_budget_exceeded(after),
                terminal=False,
                recovery_seconds=recovery_seconds,
            )
            return Attempt(
                status="failed",
                observed_boundary=observed.boundary,
                detail=f"resume refused: {refusal}",
                fields=fields,
            )
        after = read_entries(path)
        done, error = _terminal_events(events)
        succeeded = _run_finished(done, error)
        unknown = {
            str(entry.get("toolCallId"))
            for entry in after
            if entry.get("type") == "message"
            and entry.get("role") == "toolResult"
            and (entry.get("meta") or {}).get("interrupted") is True
        }
        fields = _recovery_fields(
            interrupted=True,
            detected=True,
            normal=False,
            false_interrupt=None,
            before=before,
            after=after,
            resume_attempted=True,
            resume_succeeded=succeeded,
            budget_exceeded=_budget_exceeded(after),
            terminal=succeeded,
            recovery_seconds=recovery_seconds,
        )
        passed = (
            succeeded
            and bool(open_calls)
            and unknown == open_calls
            and fields["replayed_tools"] == 0
            and fields["preserved_entries"] == fields["committed_entries"]
        )
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=observed.boundary,
            detail=(
                ""
                if passed
                else f"unknown results {sorted(unknown)} vs open calls "
                f"{sorted(open_calls)}"
            ),
            fields=fields,
        )
    finally:
        thread.join(timeout=1.0)
        _close(server, client)


def _r3(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    server, client = ctx.start()
    session_id = client.create_session(ctx.scenario.id)
    path = ctx.session_path(session_id)

    # The scan only invents a recovered question when a settled marker precedes
    # it; a brand-new session with no marker is left alone by design.
    first = client.query(session_id, PROMPT_PLAIN)
    first_done, first_error = _terminal_events(first)
    if not _run_finished(first_done, first_error):
        return Attempt(
            status="failed", detail="the settling run did not finish"
        )

    outcome: dict[str, Any] = {}

    def ask() -> None:
        try:
            outcome["events"] = client.query(session_id, PROMPT_QUESTION)
        except Exception as error:  # noqa: BLE001 - the kill is the point
            outcome["error"] = error

    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    try:
        observed = wait_for_boundary(
            path, QUESTION_ONLY, timeout_s=5.0, poll_s=0.001
        )
        if observed is None:
            return Attempt(
                status="missed",
                observed_boundary=classify_file(path),
                detail="the question-only window was not observed",
            )
        server.kill()
        client.close()
        thread.join(timeout=10.0)

        landed = classify_file(path)
        if landed != QUESTION_ONLY:
            return Attempt(
                status="missed",
                observed_boundary=landed,
                detail=f"the kill landed at {landed}",
            )

        restart_started = time.monotonic()
        _, client2 = ctx.start()
        interrupted = _wait_interrupted(client2, session_id)
        recovery_seconds = time.monotonic() - restart_started
        if interrupted is None:
            return _detection_failure(
                ctx,
                session_id,
                detail="the scan did not surface the committed question",
                observed=observed.boundary,
                terminal=False,
                recovery_seconds=recovery_seconds,
            )
        before = read_entries(path)
        try:
            events = client2.resume(session_id, str(interrupted["runId"]))
        except RequestFailed as refusal:
            after = read_entries(path)
            fields = _recovery_fields(
                interrupted=True,
                detected=True,
                normal=False,
                false_interrupt=None,
                before=before,
                after=after,
                resume_attempted=True,
                resume_succeeded=False,
                budget_exceeded=_budget_exceeded(after),
                terminal=False,
                recovery_seconds=recovery_seconds,
            )
            return Attempt(
                status="failed",
                observed_boundary=observed.boundary,
                detail=f"resume refused: {refusal}",
                fields=fields,
            )
        after = read_entries(path)
        done, error = _terminal_events(events)
        succeeded = _run_finished(done, error)
        fields = _recovery_fields(
            interrupted=True,
            detected=True,
            normal=False,
            false_interrupt=None,
            before=before,
            after=after,
            resume_attempted=True,
            resume_succeeded=succeeded,
            budget_exceeded=_budget_exceeded(after),
            terminal=succeeded,
            recovery_seconds=recovery_seconds,
        )
        passed = (
            succeeded
            and str(interrupted["runId"]).startswith("recovered-")
            and fields["preserved_entries"] == fields["committed_entries"]
        )
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=observed.boundary,
            detail="" if passed else "the recovered question did not resume",
            fields=fields,
        )
    finally:
        thread.join(timeout=1.0)
        _close(server, client)


def _r4(ctx: RunContext) -> Attempt:
    server, client, session_id = _prepare(ctx)
    try:
        events = client.query(session_id, PROMPT_PLAIN)
        done, error = _terminal_events(events)
        if not _run_finished(done, error):
            return Attempt(status="failed", detail="the normal run did not finish")
        view = client.view(session_id)
        false_interrupted = bool(view.get("state", {}).get("interrupted"))
        before = read_entries(ctx.session_path(session_id))
        markers_before = _markers(before)
        server.kill()
        client.close()

        _, client2 = ctx.start()
        view2 = client2.view(session_id)
        after = read_entries(ctx.session_path(session_id))
        false_after = bool(view2.get("state", {}).get("interrupted"))
        markers_same = _markers(after) == markers_before
        fields = _recovery_fields(
            interrupted=False,
            detected=None,
            normal=True,
            false_interrupt=false_interrupted or false_after,
            before=before,
            after=after,
            resume_attempted=False,
            resume_succeeded=None,
            budget_exceeded=_budget_exceeded(after),
            terminal=True,
            recovery_seconds=None,
        )
        passed = not false_interrupted and not false_after and markers_same
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=classify_file(ctx.session_path(session_id)),
            detail=(
                ""
                if passed
                else "a completed run was reported interrupted or rewritten"
            ),
            fields=fields,
        )
    finally:
        _close(server, client)


def _r5(ctx: RunContext) -> Attempt:
    server, client, session_id = _prepare(ctx)
    try:
        with client.stream_query(session_id, PROMPT_READ) as stream:
            for event in stream:
                if event.kind == "token":
                    break
        interrupted = _wait_interrupted(client, session_id)
        if interrupted is None:
            return _detection_failure(
                ctx,
                session_id,
                detail="the disconnect did not leave an interrupted run",
            )
        run_id = str(interrupted["runId"])
        server.stop()
        client.close()

        _, client2 = ctx.start(
            {"NOTEAGENT_LLM_MODEL": "eval-fingerprint-change"}
        )
        refused = client2.post(
            f"/api/v1/sessions/{session_id}/resume", {"runId": run_id}
        )
        refused_ok = (
            refused.status_code == 409
            and refused.json().get("code") == "session.notResumable"
        )
        _close(ctx.servers[-1], client2)

        page = ctx.project / "wiki" / "concepts" / "alpha.md"
        page.write_text(
            page.read_text(encoding="utf-8")
            + "\nA line added after the run paused.\n",
            encoding="utf-8",
        )
        before = read_entries(ctx.session_path(session_id))
        restart_started = time.monotonic()
        _, client3 = ctx.start()
        discovered = _wait_interrupted(client3, session_id)
        recovery_seconds = time.monotonic() - restart_started
        try:
            events = client3.resume(session_id, run_id)
        except RequestFailed as refusal:
            after = read_entries(ctx.session_path(session_id))
            fields = _recovery_fields(
                interrupted=True,
                detected=True,
                normal=False,
                false_interrupt=None,
                before=before,
                after=after,
                resume_attempted=True,
                resume_succeeded=False,
                budget_exceeded=_budget_exceeded(after),
                terminal=False,
                recovery_seconds=recovery_seconds,
            )
            return Attempt(
                status="failed",
                observed_boundary=classify_file(ctx.session_path(session_id)),
                detail=f"resume refused: {refusal}",
                fields=fields,
            )
        after = read_entries(ctx.session_path(session_id))
        done, error = _terminal_events(events)
        succeeded = _run_finished(done, error)
        note = _resume_note(after, run_id)
        stale_ok = "changed since it paused" in note
        fields = _recovery_fields(
            interrupted=True,
            detected=True,
            normal=False,
            false_interrupt=None,
            before=before,
            after=after,
            resume_attempted=True,
            resume_succeeded=succeeded,
            budget_exceeded=_budget_exceeded(after),
            terminal=succeeded,
            recovery_seconds=recovery_seconds,
        )
        passed = (
            refused_ok
            and succeeded
            and stale_ok
            and discovered is not None
        )
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=classify_file(ctx.session_path(session_id)),
            detail=(
                ""
                if passed
                else f"refused={refused_ok} resumed={succeeded} stale={stale_ok}"
            ),
            fields=fields,
        )
    finally:
        _close(server, client)


def _r6(ctx: RunContext) -> Attempt:
    from noteagent.llm.config import load_llm_config

    config = load_llm_config()
    _seed_pages(ctx.layout)
    proxy = FailingProxy(config.chat_completions_url, fail_first=2).start()
    try:
        _, client = ctx.start({"NOTEAGENT_LLM_BASE_URL": proxy.base_url})
        session_id = client.create_session(ctx.scenario.id)
        events = client.query(session_id, PROMPT_READ)
        done, error = _terminal_events(events)
        entries = read_entries(ctx.session_path(session_id))
        retries = sum(
            1
            for event in events
            if event.kind == "status"
            and "retr" in str(event.data.get("message", "")).lower()
        )
        replayed = _replayed(entries)
        tool_results = sum(
            1
            for entry in entries
            if entry.get("type") == "message"
            and entry.get("role") == "toolResult"
        )
        finished = _run_finished(done, error)
        passed = (
            finished
            and retries >= 2
            and replayed == 0
            and tool_results >= 1
        )
        return Attempt(
            status="passed" if passed else "failed",
            observed_boundary=classify_file(ctx.session_path(session_id)),
            detail=(
                ""
                if passed
                else f"retries={retries} replayed={replayed} "
                f"tools={tool_results}"
            ),
            fields={
                "replayed_tools": replayed,
                "resume_attempted": False,
                "budget_exceeded": proxy.requests > 12,
                "terminal": finished,
            },
        )
    finally:
        proxy.stop()


# ---------------------------------------------------------------------------
# Rollback suite
# ---------------------------------------------------------------------------


def _b1(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    run_id = f"eval-B1-{ctx.rep}"
    path = "wiki/concepts/alpha.md"
    _write_run(ctx, run_id, {path: _page("Alpha", "Run A's version.")})
    _, client = ctx.start()
    try:
        _person_edit(
            client, path, _body("Alpha", "The person's version.")
        )
        preview = client.restore_preview(PROJECT_NAME, run_id)
        entry = _preview_page(preview, path)
        classified = entry.get("classification")
        default = _defaults(preview)
        skipped = client.restore(PROJECT_NAME, run_id, default)
        unchanged = _page_body(ctx, path) == _body(
            "Alpha", "The person's version."
        )
        forced = client.restore(
            PROJECT_NAME,
            run_id,
            [{"path": path, "action": "restore", "force": True}],
        )
        restored = _page_body(ctx, path) == _body(
            "Alpha", "Seeded version."
        )
        # The forced restore leaves a mode=restore history row; a later person
        # edit then classifies as changed_by_restore, the seventh class.
        _person_edit(client, path, _body("Alpha", "A later person version."))
        later_preview = client.restore_preview(PROJECT_NAME, run_id)
        later_entry = _preview_page(later_preview, path)
        later_classified = later_entry.get("classification")
        later_skipped = client.restore(
            PROJECT_NAME, run_id, _defaults(later_preview)
        )
        later_unchanged = _page_body(ctx, path) == _body(
            "Alpha", "A later person version."
        )
        correct = sum(
            1
            for value in (classified, later_classified)
            if value in {"changed_by_person", "changed_by_restore"}
        )
        passed = (
            classified == "changed_by_person"
            and entry.get("defaultAction") == "skip"
            and not skipped["restored"]
            and unchanged
            and bool(forced["restored"])
            and restored
            and later_classified == "changed_by_restore"
            and later_entry.get("defaultAction") == "skip"
            and not later_skipped["restored"]
            and later_unchanged
        )
        return Attempt(
            status="passed" if passed else "failed",
            detail=(
                ""
                if passed
                else f"classification={classified}/{later_classified} "
                f"skipped={skipped} forced={forced} later={later_skipped}"
            ),
            fields={
                "classified_correct": correct,
                "classified_total": 2,
                "user_changed_pages": 2,
                "user_pages_overwritten": sum(
                    1 for value in (unchanged, later_unchanged) if not value
                ),
                "restore_attempted": True,
                "restore_succeeded": bool(forced["restored"]),
            },
        )
    finally:
        _close(ctx.servers[-1], client)


def _b2(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    path = "wiki/concepts/alpha.md"
    run_a = f"eval-B2a-{ctx.rep}"
    run_b = f"eval-B2b-{ctx.rep}"
    _write_run(ctx, run_a, {path: _page("Alpha", "Run A's version.")})
    _write_run(ctx, run_b, {path: _page("Alpha", "Run B's version.")})
    _, client = ctx.start()
    try:
        preview = client.restore_preview(PROJECT_NAME, run_a)
        entry = _preview_page(preview, path)
        classified = entry.get("classification")
        skipped = client.restore(PROJECT_NAME, run_a, _defaults(preview))
        unchanged = _page_body(ctx, path) == _body(
            "Alpha", "Run B's version."
        )
        passed = (
            classified == "changed_by_run"
            and entry.get("defaultAction") == "skip"
            and not skipped["restored"]
            and unchanged
        )
        return Attempt(
            status="passed" if passed else "failed",
            detail=(
                ""
                if passed
                else f"classification={classified} skipped={skipped}"
            ),
            fields={
                "classified_correct": 1 if classified == "changed_by_run" else 0,
                "classified_total": 1,
                "user_changed_pages": 0,
                "user_pages_overwritten": 0,
                "restore_attempted": False,
                "restore_succeeded": None,
            },
        )
    finally:
        _close(ctx.servers[-1], client)


def _b3(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    path = "wiki/concepts/alpha.md"
    run_id = f"eval-B3-{ctx.rep}"
    _write_run(ctx, run_id, {path: _page("Alpha", "Run A's version.")})
    _, client = ctx.start()
    try:
        preview = client.restore_preview(PROJECT_NAME, run_id)
        entry = _preview_page(preview, path)
        classified = entry.get("classification")
        restored = client.restore(PROJECT_NAME, run_id, _defaults(preview))
        undo_before = _undo_count(ctx, run_id)
        content_ok = _page_body(ctx, path) == _body(
            "Alpha", "Seeded version."
        )
        history_ok = any(
            item.mode == "restore" and item.path == path
            for item in history_entries(ctx.layout)
        )
        repeat = client.restore(PROJECT_NAME, run_id, _defaults(preview))
        undo_after = _undo_count(ctx, run_id)
        passed = (
            classified == "unchanged"
            and bool(restored["restored"])
            and content_ok
            and history_ok
            and undo_before == 1
            and not repeat["restored"]
            and undo_after == undo_before
        )
        return Attempt(
            status="passed" if passed else "failed",
            detail=(
                ""
                if passed
                else f"classification={classified} restored={restored} "
                f"repeat={repeat} undo={undo_before}->{undo_after}"
            ),
            fields={
                "classified_correct": 1 if classified == "unchanged" else 0,
                "classified_total": 1,
                "restore_attempted": True,
                "restore_succeeded": bool(restored["restored"]),
                "repeat_restore": True,
                "extra_undo_after_repeat": undo_after - undo_before,
            },
        )
    finally:
        _close(ctx.servers[-1], client)


def _b4(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    run_id = f"eval-B4-{ctx.rep}"
    alpha = "wiki/concepts/alpha.md"
    beta = "wiki/concepts/beta.md"
    gamma = "wiki/concepts/gamma.md"
    _write_run(
        ctx,
        run_id,
        {
            alpha: _page("Alpha", "Run A's version."),
            beta: _page("Beta", "Run A's version."),
            gamma: _page("Gamma", "Run A's version."),
        },
    )
    (ctx.project / alpha).write_text(
        _page("Alpha", "An external edit."), encoding="utf-8"
    )
    (ctx.project / beta).unlink()
    rename_page(ctx.layout, gamma, "wiki/concepts/gamma-moved.md")
    _, client = ctx.start()
    try:
        preview = client.restore_preview(PROJECT_NAME, run_id)
        expected = {
            alpha: "unknown_external",
            beta: "missing",
            gamma: "renamed",
        }
        correct = sum(
            1
            for path, wanted in expected.items()
            if _preview_page(preview, path).get("classification") == wanted
        )
        skipped = client.restore(PROJECT_NAME, run_id, _defaults(preview))
        forced = client.restore(
            PROJECT_NAME,
            run_id,
            [
                {"path": path, "action": "restore", "force": True}
                for path in expected
            ],
        )
        alpha_restored = _page_body(ctx, alpha) == _body(
            "Alpha", "Seeded version."
        )
        beta_failed = any(
            item.get("path") == beta for item in forced["failed"]
        )
        renamed_failed = any(
            item.get("path") == gamma and item.get("currentPath")
            for item in forced["failed"]
        )
        passed = (
            correct == 3
            and not skipped["restored"]
            and alpha_restored
            and beta_failed
            and renamed_failed
        )
        return Attempt(
            status="passed" if passed else "failed",
            detail=(
                ""
                if passed
                else f"correct={correct}/3 skipped={skipped} forced={forced}"
            ),
            fields={
                "classified_correct": correct,
                "classified_total": 3,
                "user_changed_pages": 0,
                "user_pages_overwritten": 0,
                "restore_attempted": True,
                "restore_succeeded": alpha_restored,
            },
        )
    finally:
        _close(ctx.servers[-1], client)


def _b5(ctx: RunContext) -> Attempt:
    _seed_pages(ctx.layout)
    _, client = ctx.start()
    try:
        session_id = client.create_session("B5")
        created = client.post(
            "/api/v1/pipeline/runs",
            {
                "intent": "Alpha 是本项目的第一个概念页；请基于它写一篇简短的 wiki 笔记。",
                "startedBy": "session",
                "sessionId": session_id,
                "queryMode": "precise",
            },
        )
        if created.status_code != 201:
            return Attempt(
                status="failed",
                detail=f"pipeline start: {created.status_code} {created.text}",
            )
        run_id = str(created.json()["id"])
        run = _wait_pipeline(client, run_id)
        if run is None:
            return Attempt(
                status="missed", detail="the pipeline run timed out"
            )
        if run.get("status") == "awaiting_confirmation":
            # The old approve endpoint is retired; the old run is not restarted.
            return None
            approved = client.post(
                f"/api/v1/pipeline/runs/{run_id}/approve",
                {"planRevision": run.get("planRevision", 1)},
            )
            if approved.status_code not in (200, 202):
                return Attempt(
                    status="missed",
                    detail=f"pipeline approve: {approved.status_code} "
                    f"{approved.text}",
                )
            run = _wait_pipeline(client, run_id)
        if run is None:
            return Attempt(
                status="missed", detail="the pipeline run timed out after approval"
            )
        effect_ids = list(run.get("result", {}).get("effectIds") or ())
        if run.get("status") != "succeeded" and not effect_ids:
            return Attempt(
                status="missed",
                detail=(
                    "the pipeline never wrote a page: "
                    f"{run.get('status')} {run.get('error') or ''}"
                ).strip(),
            )
        if not effect_ids:
            return Attempt(
                status="missed",
                detail="the pipeline result carries no effect ids",
            )
        preview = client.get(
            f"/api/v1/pipeline/runs/{run_id}/restore/preview"
        )
        if preview.status_code != 200:
            return Attempt(
                status="failed",
                detail=f"pipeline preview: {preview.status_code}",
            )
        body = preview.json()
        pages = body.get("pages") or []
        classified_total = len(pages)
        classified_correct = sum(
            1 for page in pages if page.get("classification") == "unchanged"
        )
        classification_ok = (
            classified_total > 0 and classified_correct == classified_total
        )
        restored = client.post(
            f"/api/v1/pipeline/runs/{run_id}/restore",
            {"runId": run_id, "pages": _defaults(body)},
        )
        if restored.status_code != 200:
            return Attempt(
                status="failed",
                detail=f"pipeline restore: {restored.status_code}",
            )
        undo_before = _undo_count(ctx, run_id)
        repeat = client.post(
            f"/api/v1/pipeline/runs/{run_id}/restore",
            {"runId": run_id, "pages": _defaults(body)},
        )
        undo_after = _undo_count(ctx, run_id)
        passed = (
            classification_ok
            and bool(restored.json()["restored"])
            and not repeat.json()["restored"]
            and undo_after == undo_before
        )
        return Attempt(
            status="passed" if passed else "failed",
            detail=(
                ""
                if passed
                else f"classification={classified_correct}/{classified_total} "
                f"restored={restored.json()} repeat={repeat.json()}"
            ),
            fields={
                "classified_correct": classified_correct,
                "classified_total": classified_total,
                "restore_attempted": True,
                "restore_succeeded": bool(restored.json()["restored"]),
                "repeat_restore": True,
                "extra_undo_after_repeat": undo_after - undo_before,
            },
        )
    finally:
        _close(ctx.servers[-1], client)


def _b6(ctx: RunContext) -> Attempt:
    import subprocess
    import sys

    _seed_pages(ctx.layout)
    journal_path = ctx.project / ".noteagent" / "effects.jsonl"
    if journal_path.exists():
        journal_path.unlink()
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "evals.recovery.journal_child",
                "--project",
                str(ctx.project),
                "--actor",
                actor,
                "--count",
                "10",
            ],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_child_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for actor in ("a", "b")
    ]
    try:
        codes = [child.wait(timeout=60) for child in children]
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
    lines = [
        line
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    journal = EffectJournal(ctx.layout)
    effects = {
        actor: journal.query(run_id=f"eval-{actor}") for actor in ("a", "b")
    }
    counts = {actor: len(items) for actor, items in effects.items()}
    ordered = all(
        [effect.target for effect in effects[actor]]
        == [f"wiki/eval/{actor}-{index:02d}.md" for index in range(10)]
        for actor in ("a", "b")
    )
    passed = (
        all(code == 0 for code in codes)
        and len(lines) == 20
        and counts == {"a": 10, "b": 10}
        and ordered
    )
    return Attempt(
        status="passed" if passed else "failed",
        detail=(
            ""
            if passed
            else f"codes={codes} lines={len(lines)} counts={counts} "
            f"ordered={ordered}"
        ),
        fields={
            "restore_attempted": False,
            "restore_succeeded": None,
        },
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _seed_pages(layout: ProjectLayout) -> None:
    _write_page(layout, "concepts/alpha", "Alpha", "Seeded version.")
    _write_page(layout, "concepts/beta", "Beta", "Seeded version.")
    _write_page(layout, "concepts/gamma", "Gamma", "Seeded version.")


def _write_page(layout: ProjectLayout, slug: str, title: str, body: str) -> None:
    path = layout.wiki / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_page(title, body), encoding="utf-8")


def _page(title: str, body: str) -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        "type: concept\n"
        "sources: []\n"
        "---\n\n"
        f"{_body(title, body)}"
    )


def _body(title: str, text: str) -> str:
    return f"# {title}\n\n{text}\n"


def _write_run(
    ctx: RunContext, run_id: str, pages: dict[str, str]
) -> None:
    write_file_blocks(
        ctx.layout,
        [FileBlock(path=path, content=text) for path, text in pages.items()],
        # The eval is not measuring merge quality; it needs a run-written
        # version on disk so the rollback path has something to restore.
        merger=lambda existing, incoming, identity: incoming,
        write_context=WriteContext(
            run_id=run_id,
            session_id=PROJECT_NAME,
            actor="eval",
            plan_revision="1",
        ),
    )


def _person_edit(client: Client, path: str, body: str) -> None:
    history = client.get(
        f"/api/projects/{PROJECT_NAME}/page/history",
        params={"path": path},
    )
    history.raise_for_status()
    versions = history.json().get("versions") or []
    base = str(versions[0]["id"]) if versions else ""
    saved = client.post(
        f"/api/projects/{PROJECT_NAME}/page/save",
        {
            "path": path,
            "body": body,
            "baseRevision": base,
            "preview": False,
        },
    )
    if saved.status_code != 200:
        raise RuntimeError(f"person edit failed: {saved.status_code} {saved.text}")


def _preview_page(preview: dict[str, Any], path: str) -> dict[str, Any]:
    for page in preview.get("pages") or ():
        if page.get("path") == path:
            return page
    raise KeyError(f"page not in preview: {path}")


def _defaults(preview: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"path": page["path"], "action": page["defaultAction"]}
        for page in preview.get("pages") or ()
    ]


def _page_body(ctx: RunContext, path: str) -> str:
    text = (ctx.project / path).read_text(encoding="utf-8")
    return parse_page(text).body


def _undo_count(ctx: RunContext, run_id: str) -> int:
    return len(EffectJournal(ctx.layout).query(run_id=run_id, kind="undo"))


def _wait_pipeline(client: Client, run_id: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + PIPELINE_TIMEOUT_S
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/pipeline/runs/{run_id}")
        if response.status_code != 200:
            return None
        run = response.json()
        if run.get("status") in {
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
            "awaiting_confirmation",
        }:
            return run
        time.sleep(0.5)
    return None


def _wait_health(client: Client, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            client.health()
            return
        except Exception:  # noqa: BLE001 - the server may still be starting
            time.sleep(0.05)
    raise RuntimeError("the eval server never became healthy")


def _wait_interrupted(
    client: Client, session_id: str, timeout_s: float = INTERRUPT_TIMEOUT_S
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            view = client.view(session_id)
        except Exception:  # noqa: BLE001 - the server may be restarting
            time.sleep(0.05)
            continue
        state = view.get("state") or {}
        interrupted = state.get("interrupted")
        if interrupted and not state.get("busy"):
            return interrupted
        time.sleep(0.05)
    return None


def _terminal_events(events: tuple[Event, ...]) -> tuple[Event | None, Event | None]:
    done = next((item for item in reversed(events) if item.kind == "done"), None)
    error = next(
        (item for item in reversed(events) if item.kind == "error"), None
    )
    return done, error


def _run_finished(done: Event | None, error: Event | None) -> bool:
    if error is not None or done is None:
        return False
    run = done.data.get("run") or {}
    return run.get("status") in {"completed", "waiting", "interrupted"}


def _detection_failure(
    ctx: RunContext,
    session_id: str | None,
    *,
    detail: str,
    observed: str = "",
    terminal: bool = False,
    recovery_seconds: float | None = None,
) -> Attempt:
    """A run that should have been interrupted and was not.

    It stays in the detection denominator: dropping it would report 100% by
    removing the failure.
    """
    entries = read_entries(ctx.session_path(session_id)) if session_id else []
    return Attempt(
        status="failed",
        observed_boundary=observed,
        detail=detail,
        fields=_recovery_fields(
            interrupted=True,
            detected=False,
            normal=False,
            false_interrupt=None,
            before=entries,
            after=entries,
            resume_attempted=False,
            resume_succeeded=None,
            budget_exceeded=_budget_exceeded(entries),
            terminal=terminal,
            recovery_seconds=recovery_seconds,
        ),
    )


def _recovery_fields(
    *,
    interrupted: bool,
    detected: bool | None,
    normal: bool,
    false_interrupt: bool | None,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    resume_attempted: bool,
    resume_succeeded: bool | None,
    budget_exceeded: bool | None,
    terminal: bool | None = None,
    recovery_seconds: float | None,
) -> dict[str, Any]:
    preserved, committed = _preservation(before, after)
    return {
        "interrupted_expected": interrupted,
        "interrupted_detected": detected,
        "normal_completion": normal,
        "false_interrupted": false_interrupt,
        "preserved_entries": preserved,
        "committed_entries": committed,
        "replayed_tools": _replayed(after),
        "resume_attempted": resume_attempted,
        "resume_succeeded": resume_succeeded,
        "budget_exceeded": budget_exceeded,
        "terminal": terminal,
        "recovery_seconds": recovery_seconds,
    }


def _preservation(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[int, int]:
    after_ids = {entry.get("id") for entry in after if entry.get("id")}
    committed = sum(1 for entry in before if entry.get("id"))
    preserved = sum(
        1 for entry in before if entry.get("id") in after_ids
    )
    return preserved, committed


def _replayed(entries: list[dict[str, Any]]) -> int:
    results = Counter(
        entry.get("toolCallId")
        for entry in entries
        if entry.get("type") == "message"
        and entry.get("role") == "toolResult"
        and entry.get("toolCallId")
    )
    return sum(count - 1 for count in results.values() if count > 1)


def _budget_exceeded(entries: list[dict[str, Any]]) -> bool:
    calls = sum(
        1
        for entry in entries
        if entry.get("type") == "message" and entry.get("role") == "assistant"
    )
    return calls > 12


def _markers(entries: list[dict[str, Any]]) -> list[tuple[Any, Any]]:
    return [
        (entry.get("runId"), entry.get("state"))
        for entry in entries
        if entry.get("type") == "run_marker"
    ]


def _resume_note(entries: list[dict[str, Any]], run_id: str) -> str:
    for entry in reversed(entries):
        if (
            entry.get("type") == "run_marker"
            and entry.get("resumes") == run_id
            and entry.get("state") == "running"
        ):
            return str(entry.get("note") or "")
    return ""


def _first_session_file(ctx: RunContext) -> Path:
    sessions = sorted((ctx.project / ".noteagent" / "sessions").glob("*.jsonl"))
    return sessions[0] if sessions else ctx.project / "missing.jsonl"


def _close(server: ServerHandle, client: Client) -> None:
    client.close()
    if server.child.alive:
        server.stop()


def _child_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "src")
    return env


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_IMPLEMENTATIONS = {
    "R1": _r1,
    "R2": _r2,
    "R3": _r3,
    "R4": _r4,
    "R5": _r5,
    "R6": _r6,
    "B1": _b1,
    "B2": _b2,
    "B3": _b3,
    "B4": _b4,
    "B5": _b5,
    "B6": _b6,
}
