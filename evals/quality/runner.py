"""Run a quality case against a live model and keep what it did.

The case gets its own temporary project, seeded with the case's wiki. A real
``OpenAIModelClient`` is injected through ``AppDeps``, so the run takes the same
HTTP/SSE path as the app. Every case keeps its project, transcript, trace and
assertion verdicts under the run directory, whether it passed or failed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from evals.harness import CaseRun
from evals.quality.assertions import (
    AssertionResult,
    evidence_from,
    evaluate,
)
from evals.quality.cases import CaseSpec
from evals.quality.judge import (
    JudgeClient,
    JudgeVerdict,
    judge_case,
    make_judge_client,
)
from evals.quality.report import CaseSummary

from noteagent.agent.model import ModelClient
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient


def run_case(
    spec: CaseSpec,
    config: LlmConfig,
    artifacts_root: Path,
    *,
    model: ModelClient | None = None,
    judge_client: JudgeClient | None = None,
) -> CaseSummary:
    """Run one case; *model* is the test seam, production leaves it ``None``."""
    started = time.monotonic()
    target = Path(artifacts_root) / "cases" / spec.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    run: CaseRun | None = None
    session_id: str | None = None
    entries: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    session: dict[str, Any] | None = None
    turns: list[dict[str, Any]] = []
    assertions: tuple[AssertionResult, ...] = ()
    judge: JudgeVerdict | None = None
    error = ""

    with tempfile.TemporaryDirectory(
        prefix=f"noteagent-quality-{spec.name}-"
    ) as tmp:
        try:
            run = CaseRun(
                Path(tmp),
                pages=[(page.slug, page.title, page.body) for page in spec.wiki],
            )
            api = run.start(model or OpenAIModelClient(config))
            session_id = api.create_session(title=spec.title)
            for content in spec.turns:
                reply = api.query(session_id, content)
                turns.append(
                    {
                        "content": content,
                        "status": reply.status_code,
                        "events": [
                            {"event": kind, "data": data}
                            for kind, data in reply.events
                        ],
                    }
                )
            entries = api.entries(session_id)
            trace = api.trace(session_id)
            try:
                session = api.get_session(session_id)
            except AssertionError:
                session = None
            evidence = evidence_from(
                entries=entries,
                trace=trace,
                session=session,
                events=_events(turns),
            )
            assertions = evaluate(spec.assertions, evidence)
            judge = judge_case(
                spec,
                entries,
                client=judge_client or make_judge_client(config),
                secret=config.api_key,
            )
        except Exception:
            error = traceback.format_exc()
        finally:
            if run is not None:
                _copy_artifacts(run, session_id, target)
                run.close()

    passed = (
        bool(assertions)
        and all(item.passed for item in assertions)
        and judge is not None
        and judge.passed
        and not error
    )
    result = CaseSummary(
        name=spec.name,
        title=spec.title,
        passed=passed,
        duration_s=time.monotonic() - started,
        assertions=assertions,
        judge=judge,
        error=error,
    )
    _write_case_json(
        target,
        result,
        turns=turns,
        entries=entries,
        trace=trace,
        session=session,
    )
    return result


def _events(turns: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(item["event"]), item["data"])
        for turn in turns
        for item in turn["events"]
    ]


def _copy_artifacts(
    run: CaseRun, session_id: str | None, target: Path
) -> None:
    if run.layout.root.is_dir():
        shutil.copytree(
            run.layout.root, target / "project", dirs_exist_ok=True
        )
    if session_id is None:
        return
    session_path = run.layout.session_path(session_id)
    if session_path.is_file():
        shutil.copyfile(session_path, target / "session.jsonl")
    events_path = run.layout.session_events_path(session_id)
    if events_path.is_file():
        shutil.copyfile(events_path, target / "events.jsonl")


def _write_case_json(
    target: Path,
    result: CaseSummary,
    *,
    turns: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    session: dict[str, Any] | None,
) -> None:
    payload = {
        "case": result.name,
        "title": result.title,
        "passed": result.passed,
        "durationSeconds": round(result.duration_s, 3),
        "assertions": [
            item.to_payload() for item in result.assertions
        ],
        "judge": (
            None
            if result.judge is None
            else result.judge.to_payload()
        ),
        "turns": turns,
        "entries": entries,
        "trace": trace,
        "session": session,
        "error": result.error,
    }
    (target / "case.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "judge.json").write_text(
        json.dumps(
            None if result.judge is None else result.judge.to_payload(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = ["run_case"]
