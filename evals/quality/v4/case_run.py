"""Run one V4 quality case through the real tools, then judge its artifact.

A page-writing case searches and fetches local fixture captures, then selects,
frames, composes and publishes through the product's tools. An answer case
seeds the real wiki pages, searches and reads them, then answers in the chat.
The deterministic checks cover only the requested shape; the judge grades the
content against the allowed material and the expected points.
"""

from __future__ import annotations

import json
import tempfile
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evals.harness import CaseRun
from evals.quality.judge_client import (
    JudgeClient,
    conversation_from,
    make_judge_client,
)
from evals.quality.v4.case_spec import (
    ANSWER_KIND,
    PAGE_KIND,
    QualityCase,
)
from evals.quality.v4.judge import (
    TaskCompliance,
    V4JudgeVerdict,
    judge_artifact,
)
from evals.quality.v4.runner import UsageCounter, preserve_case
from evals.report import PUBLISH, Check
from evals.support import remove_tree

from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.model import ModelClient
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient

AGENT_TEMPERATURE = 0.0


@dataclass(frozen=True, slots=True)
class CaseRunResult:
    """One quality case's deterministic checks and judge verdict."""

    case_id: str
    checks: tuple[Check, ...]
    artifact: str
    final_text: str
    verdict: V4JudgeVerdict
    duration_s: float
    tokens_in: int = 0
    tokens_out: int = 0
    error: str = ""
    artifacts: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and bool(self.checks)
            and all(check.passed for check in self.checks)
            and self.verdict.all_points_pass()
        )

    @property
    def failed_checks(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def task_compliance(self) -> TaskCompliance:
        return TaskCompliance(
            passed=all(check.passed for check in self.checks),
            checks=tuple(
                f"{check.name}: {check.detail}"
                if check.detail
                else check.name
                for check in self.checks
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "case": self.case_id,
            "passed": self.passed,
            "checks": [check.to_payload() for check in self.checks],
            "judge": self.verdict.to_payload(),
            "artifact": self.artifact,
            "finalAnswer": self.final_text,
            "error": self.error,
            "durationSeconds": round(self.duration_s, 3),
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "artifacts": self.artifacts,
        }


def shape_checks(
    case: QualityCase,
    artifact: str,
    final_text: str = "",
    *,
    entries: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> tuple[Check, ...]:
    """The deterministic task-compliance checks for one artifact."""
    if case.kind == PAGE_KIND:
        return _page_checks(case, artifact, final_text)
    if case.kind == ANSWER_KIND:
        return _answer_checks(case, artifact, entries)
    raise ValueError(f"unknown case kind: {case.kind!r}")


def _page_checks(
    case: QualityCase, page_text: str, final_text: str
) -> tuple[Check, ...]:
    parsed = parse_page(page_text) if page_text else None
    sources = (
        parse_inline_list(parsed.fields.get("sources", ""))
        if parsed is not None
        else []
    )
    body = parsed.body if parsed is not None else ""
    lowered = final_text.casefold()
    stem = Path(case.wiki_file).stem.casefold()
    cited = tuple(
        any(source.startswith(f"{material_id}#") for source in sources)
        for material_id in case.material_ids
    )

    def check(name: str, passed: bool, detail: str = "") -> Check:
        return Check(name, passed, detail, PUBLISH)

    language = _has_cjk(body)
    return (
        check(
            "the page exists at the requested path",
            bool(page_text),
            "" if page_text else case.wiki_file,
        ),
        check(
            "the page cites every allowed raw source",
            all(cited),
            f"sources: {sources}",
        ),
        check(
            "the page has a title and sections",
            body.lstrip().startswith("# ") and "\n## " in body,
            body[:80],
        ),
        check(
            "the page is in the task's language",
            language,
            "" if language else "no CJK text",
        ),
        check(
            "the final answer names the page written",
            bool(lowered)
            and (
                stem in lowered
                or case.wiki_file.casefold() in lowered
                or case.page_title.casefold() in lowered
            ),
            final_text[:120],
        ),
    )


def _answer_checks(
    case: QualityCase,
    final_text: str,
    entries: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[Check, ...]:
    language = _has_cjk(final_text)
    calls = _tool_calls(entries)
    searched = any(name == "search" for name, _args in calls)
    read = {
        _normalize_slug(args.get("slug"))
        for name, args in calls
        if name == "wiki_read"
    }
    required = {
        slug for slug in case.required_reads if not _was_read(slug, read)
    }
    return (
        Check(
            "an answer is present",
            bool(final_text.strip()),
            "" if final_text.strip() else "the run produced no answer",
            PUBLISH,
        ),
        Check(
            "the answer is in the task's language",
            language,
            "" if language else "no CJK text",
            PUBLISH,
        ),
        Check(
            "the agent searched the wiki",
            searched,
            "no search call",
            PUBLISH,
        ),
        Check(
            "the agent read the pages the task names",
            not required,
            "not read: " + ", ".join(sorted(required)),
            PUBLISH,
        ),
    )


def _tool_calls(
    entries: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        if entry.get("role") != "assistant":
            continue
        for raw in entry.get("toolCalls") or []:
            if not isinstance(raw, dict):
                continue
            arguments = raw.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            calls.append((str(raw.get("name") or ""), arguments))
    return calls


def _normalize_slug(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("[[") and text.endswith("]]"):
        text = text[2:-2]
    if text.startswith("wiki/"):
        text = text[5:]
    if text.endswith(".md"):
        text = text[:-3]
    return text


def _was_read(required: str, read: set[str]) -> bool:
    bare = required.rsplit("/", 1)[-1]
    return any(
        slug == required or slug.rsplit("/", 1)[-1] == bare
        for slug in read
    )


def run_case(
    case: QualityCase,
    config: LlmConfig,
    *,
    failures_root: Path,
    run_id: str,
    model: ModelClient | None = None,
    judge_client: JudgeClient | None = None,
) -> CaseRunResult:
    """Run one case through the real tools, then judge its artifact."""
    if case.kind == PAGE_KIND:
        return _run_page_case(
            case,
            config,
            failures_root=failures_root,
            run_id=run_id,
            model=model,
            judge_client=judge_client,
        )
    if case.kind == ANSWER_KIND:
        return _run_answer_case(
            case,
            config,
            failures_root=failures_root,
            run_id=run_id,
            model=model,
            judge_client=judge_client,
        )
    raise ValueError(f"unknown case kind: {case.kind!r}")


def _run_page_case(
    case: QualityCase,
    config: LlmConfig,
    *,
    failures_root: Path,
    run_id: str,
    model: ModelClient | None,
    judge_client: JudgeClient | None,
) -> CaseRunResult:
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix=f"noteagent-v4-case-{case.id}-"))
    run: CaseRun | None = None
    counter = UsageCounter()
    error = ""
    artifact = ""
    final_text = ""
    verdict = V4JudgeVerdict(error="the run did not reach the judge")
    checks: tuple[Check, ...] = (
        Check("the case started", False, "it did not start", PUBLISH),
    )
    try:
        run = CaseRun(tmp, pages=())
        agent_config = replace(config, temperature=AGENT_TEMPERATURE)
        raw_model = model or OpenAIModelClient(agent_config)
        captures = {
            material.url: material.capture
            for material in case.materials
            if material.capture is not None
        }

        def search(_query: str) -> list[CandidateCard]:
            return [
                CandidateCard(
                    title=material.title,
                    url=material.url,
                    site=urlsplit(material.url).hostname or "",
                    snippet="",
                    why="",
                    via="eval",
                )
                for material in case.materials
                if material.capture is not None
            ]

        def fetch(url: str) -> Capture:
            capture = captures.get(url) or captures.get(
                url.rstrip("/") + "/"
            )
            if capture is None:
                raise ValueError(f"no fixture for {url}")
            return capture

        api = run.start(
            counter.wrap(raw_model),
            model_name=agent_config.model,
            seed_system_sections=True,
            web_search=search,
            web_fetch=fetch,
        )
        session = api.create_session(title=f"case-{case.id}")
        api.query(session, case.task)
        final_text = _final_text(api.entries(session))
        page = run.layout.root / case.wiki_file
        artifact = (
            page.read_text(encoding="utf-8") if page.is_file() else ""
        )
        checks = shape_checks(case, artifact, final_text)
        result = CaseRunResult(
            case_id=case.id,
            checks=checks,
            artifact=artifact,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
        )
        verdict = judge_artifact(
            task=case.task,
            materials=case.judge_materials(),
            expected_points=case.expected_points,
            artifact_id=case.wiki_file,
            artifact=artifact or "(no page at the requested path)",
            task_compliance=result.task_compliance(),
            client=judge_client or make_judge_client(config),
            secret=config.api_key,
        )
        result = replace(
            result,
            verdict=verdict,
            duration_s=time.monotonic() - started,
        )
    except Exception:
        error = traceback.format_exc()
        result = CaseRunResult(
            case_id=case.id,
            checks=checks,
            artifact=artifact,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            error=error,
        )
    return _finish_case(
        case,
        run,
        result,
        failures_root=failures_root,
        run_id=run_id,
        tmp=tmp,
    )


def _run_answer_case(
    case: QualityCase,
    config: LlmConfig,
    *,
    failures_root: Path,
    run_id: str,
    model: ModelClient | None,
    judge_client: JudgeClient | None,
) -> CaseRunResult:
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix=f"noteagent-v4-case-{case.id}-"))
    run: CaseRun | None = None
    counter = UsageCounter()
    error = ""
    final_text = ""
    verdict = V4JudgeVerdict(error="the run did not reach the judge")
    checks: tuple[Check, ...] = (
        Check("the case started", False, "it did not start", PUBLISH),
    )
    try:
        run = CaseRun(tmp, pages=())
        for material in case.materials:
            run.write_raw_page(
                material.id, material.text.encode("utf-8")
            )
        agent_config = replace(config, temperature=AGENT_TEMPERATURE)
        raw_model = model or OpenAIModelClient(agent_config)
        api = run.start(
            counter.wrap(raw_model),
            model_name=agent_config.model,
            seed_system_sections=True,
            web_search=lambda _query: [],
            web_fetch=_offline_fetch,
        )
        session = api.create_session(title=f"case-{case.id}")
        api.query(session, case.task)
        entries = api.entries(session)
        final_text = _final_text(entries)
        checks = shape_checks(case, final_text, entries=entries)
        result = CaseRunResult(
            case_id=case.id,
            checks=checks,
            artifact=final_text,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
        )
        verdict = judge_artifact(
            task=case.task,
            materials=case.judge_materials(),
            expected_points=case.expected_points,
            artifact_id=f"case-{case.id} answer",
            artifact=final_text or "(no answer)",
            task_compliance=result.task_compliance(),
            client=judge_client or make_judge_client(config),
            secret=config.api_key,
        )
        result = replace(
            result,
            verdict=verdict,
            duration_s=time.monotonic() - started,
        )
    except Exception:
        error = traceback.format_exc()
        result = CaseRunResult(
            case_id=case.id,
            checks=checks,
            artifact=final_text,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            error=error,
        )
    return _finish_case(
        case,
        run,
        result,
        failures_root=failures_root,
        run_id=run_id,
        tmp=tmp,
    )


def _finish_case(
    case: QualityCase,
    run: CaseRun | None,
    result: CaseRunResult,
    *,
    failures_root: Path,
    run_id: str,
    tmp: Path,
) -> CaseRunResult:
    if not result.passed:
        try:
            target = failures_root / run_id / f"case-{case.id}"
            result = replace(result, artifacts=str(target))
            preserve_case(run, target, result.to_payload())
        except Exception as err:  # noqa: BLE001 - evidence is best effort
            result = replace(
                result,
                error=(
                    result.error
                    + f"\nthe failure artifacts could not be kept: {err}"
                ).strip(),
            )
    try:
        if run is not None:
            run.close()
    finally:
        remove_tree(tmp)
    return result


def _offline_fetch(_url: str) -> Capture:
    raise ValueError("this case answers from the wiki only")


def _has_cjk(text: str) -> bool:
    return any("一" <= char <= "鿿" for char in text)


def _final_text(entries: list[dict[str, Any]]) -> str:
    for role, text in reversed(conversation_from(entries)):
        if role == "assistant":
            return text
    return ""


__all__ = [
    "AGENT_TEMPERATURE",
    "CaseRunResult",
    "run_case",
    "shape_checks",
]
