"""Run the golden set against the real agent loop, one temp project per run.

Each query gets a new session in a project seeded from the corpus snapshot: the
pages are copied byte for byte, purpose and schema included, and a system
section says who the agent is, what it may touch, and how strictly it must stay
inside the wiki. The loop, the tools and the HTTP/SSE path are the app's own.

The runner keeps evidence, not conclusions: transcript, trace, trace checks,
G-Eval scores and the artifacts a reader needs to understand a failure. A live
provider is the only thing it cannot stand in for, so tests inject a model and a
judge through the same seams.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evals.golden.dataset import (
    CorpusPage,
    GoldenError,
    GoldenQuery,
    GoldenSet,
)
from evals.golden.geval import (
    DIMENSIONS,
    GevalVerdict,
    ScoreClient,
    judge,
    judge_config,
    shared_prompt_sha256,
)
from evals.golden.manifest import (
    QueryRecord,
    RunRecord,
    ScoreRecord,
    git_commit,
    iso_timestamp,
    run_id_for,
    utc_now,
)
from evals.golden.trace import (
    Evidence,
    TraceCheck,
    evaluate,
    evidence_from,
)

from noteagent.agent.model import ModelClient, ModelUsageEvent
from noteagent.llm.client import LlmClient
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.project.layout import ProjectLayout
from noteagent.util.redact import redact

if TYPE_CHECKING:
    from evals.harness import CaseRun

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
VARIANTS = ("base", "strict")

IDENTITY = (
    "You are NoteAgent, a notes assistant working on one wiki. Answer the "
    "reader's question from that wiki, concretely: give the definitions, "
    "formulas and relationships, not a pointer to them. Write in the language "
    "of the question."
)
TOOLS = (
    "You may call `search` and `wiki_read`. Search finds page slugs and "
    "excerpts; read a page before quoting or summarizing it. You cannot write "
    "or edit pages. Answer questions that ask you to explain, compare, "
    "analyze, or state what is missing directly in prose; do not propose a "
    "job just because the answer could become a page. Only when the reader "
    "explicitly asks you to create, write, compile or propose a page/job "
    "(for example \"整理成一个对比页\", \"做一份学习路径页面\", \"发起一次编译\"), "
    "use `propose` with job `plan` (from `source` or `plan_board`) or "
    "`compile` (from `plan_board` or `compile_board`), alone and never beside "
    "another call. If the wiki lacks a page, say exactly what is missing; "
    "propose only when the reader asks you to fill it. Say that the human "
    "starts the job in the original interface. Never claim a job ran, a page "
    "was written or a compilation finished."
)
_SECTION_ORDER = ("identity", "tools", "project", "grounding")


@dataclass(frozen=True, slots=True)
class QueryRun:
    """One query's answer, evidence, checks and judge verdict."""

    case: GoldenQuery
    answer: str
    entries: tuple[Mapping[str, object], ...]
    trace: tuple[Mapping[str, object], ...]
    session: Mapping[str, object] | None
    events: tuple[tuple[str, Mapping[str, object]], ...]
    evidence: Evidence
    checks: tuple[TraceCheck, ...]
    verdict: GevalVerdict
    latency_s: float
    input_tokens: int
    output_tokens: int
    error: str = ""

    @property
    def trace_passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def judge_ok(self) -> bool:
        return self.verdict.ok

    @property
    def passed(self) -> bool:
        return self.trace_passed and self.judge_ok and not self.error

    @property
    def soft_scores(self) -> dict[str, float]:
        return {
            item.dimension: item.soft
            for item in self.verdict.scores
            if not item.error
        }

    @property
    def quality_warning(self) -> bool:
        """Any dimension scored 1–2; the run still ran, but quality is weak."""
        return any(
            not item.error and item.score <= 2
            for item in self.verdict.scores
        )


@dataclass(frozen=True, slots=True)
class VariantRun:
    """One prompt variant over the whole golden set."""

    variant: str
    run_id: str
    started_at: str
    duration_s: float
    temperature: float
    sections: Mapping[str, str]
    prompt_sha256: str
    judge_prompt_sha256: str
    queries: tuple[QueryRun, ...]
    run_dir: Path


@dataclass(frozen=True, slots=True)
class PairedCase:
    """One query scored under both prompt variants."""

    id: str
    type: str
    difficulty: str
    base_scores: Mapping[str, float]
    strict_scores: Mapping[str, float]
    deltas: Mapping[str, float]
    mean_delta: float
    winner: str
    base_passed: bool
    strict_passed: bool
    base_trace_passed: bool
    strict_trace_passed: bool
    base_quality_warning: bool = False
    strict_quality_warning: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "difficulty": self.difficulty,
            "baseScores": _round_map(self.base_scores),
            "strictScores": _round_map(self.strict_scores),
            "deltas": _round_map(self.deltas),
            "meanDelta": round(self.mean_delta, 4),
            "winner": self.winner,
            "basePassed": self.base_passed,
            "strictPassed": self.strict_passed,
            "baseTracePassed": self.base_trace_passed,
            "strictTracePassed": self.strict_trace_passed,
            "baseQualityWarning": self.base_quality_warning,
            "strictQualityWarning": self.strict_quality_warning,
        }


@dataclass(frozen=True, slots=True)
class PairedSummary:
    """The paired A/B reading: per case, per dimension and per slice."""

    cases: tuple[PairedCase, ...]
    strict_wins: int
    base_wins: int
    ties: int
    mean_delta: float
    dimension_deltas: Mapping[str, float]
    by_type: Mapping[str, Mapping[str, float | int]]
    by_difficulty: Mapping[str, Mapping[str, float | int]]

    def to_payload(self) -> dict[str, object]:
        return {
            "cases": [case.to_payload() for case in self.cases],
            "strictWins": self.strict_wins,
            "baseWins": self.base_wins,
            "ties": self.ties,
            "meanDelta": round(self.mean_delta, 4),
            "dimensionDeltas": _round_map(self.dimension_deltas),
            "byType": dict(self.by_type),
            "byDifficulty": dict(self.by_difficulty),
        }


class RecordingModel:
    """A model that adds up the usage its streams report."""

    def __init__(self, model: ModelClient) -> None:
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

    def stream(self, request):
        for event in self.model.stream(request):
            if isinstance(event, ModelUsageEvent):
                self.input_tokens += event.usage.input_tokens
                self.output_tokens += event.usage.output_tokens
            yield event


def system_sections(golden: GoldenSet, variant: str) -> dict[str, str]:
    """The four blocks a prompt variant writes before the question."""
    if variant not in VARIANTS:
        raise ValueError(
            f"variant must be one of {', '.join(VARIANTS)}, got {variant!r}"
        )
    grounding = (PROMPT_DIR / f"{variant}.md").read_text(encoding="utf-8")
    return {
        "identity": IDENTITY,
        "tools": TOOLS,
        "project": (
            "What this project is for:\n\n"
            + golden.corpus.purpose.strip()
        ),
        "grounding": grounding.strip(),
    }


def sections_sha256(sections: Mapping[str, str]) -> str:
    """A stable hash of the section names and their text."""
    digest = hashlib.sha256()
    for name in _SECTION_ORDER:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sections.get(name, "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def seed_corpus(layout: ProjectLayout, golden: GoldenSet) -> None:
    """Copy the snapshot into a temp project, leaving raw/ empty."""
    for page in golden.corpus.pages:
        target = layout.wiki / f"{page.slug}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(page.path, target)
    layout.brief.parent.mkdir(parents=True, exist_ok=True)
    layout.brief.write_text(
        golden.corpus.purpose.strip() + "\n", encoding="utf-8"
    )
    layout.legacy_schema.write_text(
        golden.corpus.schema.strip() + "\n", encoding="utf-8"
    )


def answer_text(
    entries: Sequence[Mapping[str, object]],
) -> str:
    """The text the judge grades: the final answer, plus a proposal if any.

    A task turn may end in a silent ``propose`` call, so the wait's job,
    interface, intent and reason are the answer's content there.
    """
    final = ""
    proposal: Mapping[str, object] | None = None
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("type") == "wait_proposed":
            proposal = entry
            continue
        if entry.get("role") != "assistant" or entry.get("toolCalls"):
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            final = content.strip()
    parts: list[str] = []
    if final:
        parts.append(final)
    if proposal is not None:
        lines = [
            "Proposal: job={}, interface={}".format(
                proposal.get("job") or "?",
                proposal.get("interface") or "?",
            )
        ]
        for key, label in (("intent", "Intent"), ("reason", "Reason")):
            value = proposal.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{label}: {value.strip()}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def judge_pages(
    golden: GoldenSet,
    case: GoldenQuery,
    evidence: Evidence,
) -> tuple[CorpusPage, ...]:
    """The case's pages plus any other page the agent actually read."""
    slugs = list(case.corpus_pages)
    for slug in evidence.read_pages:
        if slug not in slugs:
            slugs.append(slug)
    pages: list[CorpusPage] = []
    for slug in slugs:
        try:
            pages.append(golden.corpus.page(slug))
        except GoldenError:
            continue
    return tuple(pages)


def run_variant(
    golden: GoldenSet,
    config: LlmConfig,
    artifacts_root: Path,
    *,
    variant: str,
    model: ModelClient | None = None,
    judge_client: ScoreClient | None = None,
    progress: Callable[[str], None] | None = None,
) -> VariantRun:
    """Run every query once under *variant* and keep its artifacts."""
    if variant not in VARIANTS:
        raise ValueError(
            f"variant must be one of {', '.join(VARIANTS)}, got {variant!r}"
        )
    config = replace(config, temperature=0.0)
    run_dir = Path(artifacts_root)
    run_dir.mkdir(parents=True, exist_ok=True)
    moment = utc_now()
    started_at = iso_timestamp(moment)
    run_id = run_id_for(moment)
    started = time.monotonic()
    sections = system_sections(golden, variant)
    prompt_sha = sections_sha256(sections)
    judge_client = judge_client or LlmClient(judge_config(config))
    queries: list[QueryRun] = []

    # The harness (and the FastAPI test client behind it) is only needed once a
    # run actually starts, so the CLI can print help without importing it.
    from evals.harness import CaseRun

    with tempfile.TemporaryDirectory(
        prefix=f"noteagent-golden-{variant}-"
    ) as tmp:
        run: CaseRun | None = None
        try:
            run = CaseRun(Path(tmp), pages=[])
            seed_corpus(run.layout, golden)
            recording = RecordingModel(
                model or OpenAIModelClient(config)
            )
            api = run.start(recording)
            for case in golden.queries:
                query_run = _run_one(
                    case,
                    golden=golden,
                    sections=sections,
                    run=run,
                    api=api,
                    recording=recording,
                    judge_client=judge_client,
                    run_dir=run_dir,
                    secret=config.api_key,
                )
                queries.append(query_run)
                if progress is not None:
                    progress(_progress_line(query_run))
        finally:
            if run is not None:
                _keep_project(run, run_dir / "project")
                run.close()

    return VariantRun(
        variant=variant,
        run_id=run_id,
        started_at=started_at,
        duration_s=time.monotonic() - started,
        temperature=config.temperature,
        sections=sections,
        prompt_sha256=prompt_sha,
        judge_prompt_sha256=shared_prompt_sha256(),
        queries=tuple(queries),
        run_dir=run_dir,
    )


def record_for(
    run: VariantRun,
    golden: GoldenSet,
    config: LlmConfig,
    *,
    pair_id: str = "",
) -> RunRecord:
    """The manifest-shaped record of one variant run."""
    return RunRecord(
        run_id=run.run_id,
        started_at=run.started_at,
        variant=run.variant,
        pair_id=pair_id,
        agent_model=config.model,
        agent_base_url=config.base_url,
        agent_temperature=run.temperature,
        judge_model=config.model,
        judge_temperature=0.0,
        corpus_sha256=golden.corpus_sha256,
        queries_sha256=golden.queries_sha256,
        prompt_sha256=run.prompt_sha256,
        judge_prompt_sha256=run.judge_prompt_sha256,
        git_commit=git_commit(),
        duration_s=run.duration_s,
        queries=tuple(
            QueryRecord(
                id=query.case.id,
                type=query.case.type,
                difficulty=query.case.difficulty,
                passed=query.passed,
                trace_passed=query.trace_passed,
                judge_ok=query.judge_ok,
                scores=tuple(
                    ScoreRecord(item.dimension, item.score, item.soft)
                    for item in query.verdict.scores
                    if not item.error
                ),
                latency_s=query.latency_s,
                input_tokens=query.input_tokens,
                output_tokens=query.output_tokens,
                error=query.error,
            )
            for query in run.queries
        ),
    )


def pair_variants(
    base: VariantRun, strict: VariantRun
) -> PairedSummary:
    """Compare the two variants query by query, without a pairwise judge."""
    strict_by_id = {query.case.id: query for query in strict.queries}
    cases: list[PairedCase] = []
    for base_query in base.queries:
        strict_query = strict_by_id.get(base_query.case.id)
        if strict_query is None:
            continue
        base_scores = base_query.soft_scores
        strict_scores = strict_query.soft_scores
        deltas = {
            dimension: strict_scores[dimension] - base_scores[dimension]
            for dimension in DIMENSIONS
            if dimension in base_scores and dimension in strict_scores
        }
        mean_delta = (
            sum(deltas.values()) / len(deltas) if deltas else 0.0
        )
        winner = (
            "strict"
            if mean_delta > _EPSILON
            else "base"
            if mean_delta < -_EPSILON
            else "tie"
        )
        cases.append(
            PairedCase(
                id=base_query.case.id,
                type=base_query.case.type,
                difficulty=base_query.case.difficulty,
                base_scores=base_scores,
                strict_scores=strict_scores,
                deltas=deltas,
                mean_delta=mean_delta,
                winner=winner,
                base_passed=base_query.passed,
                strict_passed=strict_query.passed,
                base_trace_passed=base_query.trace_passed,
                strict_trace_passed=strict_query.trace_passed,
                base_quality_warning=base_query.quality_warning,
                strict_quality_warning=strict_query.quality_warning,
            )
        )

    dimension_deltas = {
        dimension: _mean(
            case.deltas[dimension]
            for case in cases
            if dimension in case.deltas
        )
        for dimension in DIMENSIONS
    }
    dimension_deltas = {
        dimension: value
        for dimension, value in dimension_deltas.items()
        if value is not None
    }
    return PairedSummary(
        cases=tuple(cases),
        strict_wins=sum(1 for case in cases if case.winner == "strict"),
        base_wins=sum(1 for case in cases if case.winner == "base"),
        ties=sum(1 for case in cases if case.winner == "tie"),
        mean_delta=_mean(case.mean_delta for case in cases) or 0.0,
        dimension_deltas=dimension_deltas,
        by_type=_group(cases, "type"),
        by_difficulty=_group(cases, "difficulty"),
    )


_EPSILON = 1e-9


def _run_one(
    case: GoldenQuery,
    *,
    golden: GoldenSet,
    sections: Mapping[str, str],
    run: CaseRun,
    api: Any,
    recording: RecordingModel,
    judge_client: ScoreClient,
    run_dir: Path,
    secret: str,
) -> QueryRun:
    """One question: a fresh session, the model loop, then the judge."""
    target = run_dir / "queries" / case.id
    target.mkdir(parents=True, exist_ok=True)
    session_id = api.create_session(title=case.id)
    run.store.append(
        session_id,
        {"type": "system_section", "sections": dict(sections)},
    )
    before_in = recording.input_tokens
    before_out = recording.output_tokens
    started = time.monotonic()
    error = ""
    entries: tuple[Mapping[str, object], ...] = ()
    trace: tuple[Mapping[str, object], ...] = ()
    session: Mapping[str, object] | None = None
    events: tuple[tuple[str, Mapping[str, object]], ...] = ()

    try:
        reply = api.query(session_id, case.question)
        events = tuple(
            (str(kind), data) for kind, data in reply.events
        )
    except Exception:  # noqa: BLE001 - the case records the failure
        error = traceback.format_exc()
    latency = time.monotonic() - started

    try:
        entries = tuple(api.entries(session_id))
        trace = tuple(api.trace(session_id))
        try:
            session = api.get_session(session_id)
        except Exception:  # noqa: BLE001 - the view is evidence, not a gate
            session = None
    except Exception:  # noqa: BLE001 - the case records the failure
        if not error:
            error = traceback.format_exc()

    evidence = evidence_from(
        entries=entries,
        trace=trace,
        session=session,
        events=events,
        tokens_in=recording.input_tokens - before_in,
        tokens_out=recording.output_tokens - before_out,
        latency_s=latency,
    )
    checks = evaluate(case, evidence)
    answer = _clean(answer_text(entries), secret)
    if error:
        verdict = GevalVerdict(
            error=(
                "geval_error: the run failed: "
                + _clean(_first_line(error), secret)
            )
        )
    else:
        verdict = judge(
            case,
            answer,
            judge_pages(golden, case, evidence),
            client=judge_client,
            secret=secret,
            wiki_index=tuple(
                f"{page.slug} — {page.title}"
                for page in golden.corpus.pages
            ),
            purpose=golden.corpus.purpose,
        )

    result = QueryRun(
        case=case,
        answer=answer,
        entries=entries,
        trace=trace,
        session=session,
        events=events,
        evidence=evidence,
        checks=checks,
        verdict=verdict,
        latency_s=latency,
        input_tokens=recording.input_tokens - before_in,
        output_tokens=recording.output_tokens - before_out,
        error=_clean(error, secret),
    )
    _keep_query_artifacts(run, session_id, target, result)
    return result


def _keep_query_artifacts(
    run: CaseRun,
    session_id: str,
    target: Path,
    result: QueryRun,
) -> None:
    session_path = run.layout.session_path(session_id)
    if session_path.is_file():
        shutil.copyfile(session_path, target / "session.jsonl")
    events_path = run.layout.session_events_path(session_id)
    if events_path.is_file():
        shutil.copyfile(events_path, target / "events.jsonl")
    _write_json(target / "case.json", _query_payload(result))
    _write_json(
        target / "judge.json",
        result.verdict.to_payload(include_raw=True),
    )


def _keep_project(run: CaseRun, target: Path) -> None:
    if not run.layout.root.is_dir():
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(run.layout.root, target)


def _query_payload(result: QueryRun) -> dict[str, object]:
    return {
        "id": result.case.id,
        "type": result.case.type,
        "difficulty": result.case.difficulty,
        "question": result.case.question,
        "answer": result.answer,
        "passed": result.passed,
        "tracePassed": result.trace_passed,
        "judgeOk": result.judge_ok,
        "error": result.error,
        "scores": [
            {
                "dimension": item.dimension,
                "score": item.score,
                "soft": round(item.soft, 4),
                "reason": _clip_reason(item.reason),
                "error": item.error,
            }
            for item in result.verdict.scores
        ],
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "expected": check.expected,
                "actual": check.actual,
                "detail": check.detail,
            }
            for check in result.checks
        ],
        "evidence": {
            "tools": list(result.evidence.tools),
            "readPages": list(result.evidence.read_pages),
            "toolRounds": result.evidence.tool_rounds,
            "wait": result.evidence.wait_status or "absent",
            "waitJob": result.evidence.wait_job or "",
            "waitInterface": result.evidence.wait_interface or "",
            "proposeCalls": [
                list(pair) for pair in result.evidence.propose_calls
            ],
            "errors": list(result.evidence.errors),
            "inputTokens": result.input_tokens,
            "outputTokens": result.output_tokens,
            "latencySeconds": round(result.latency_s, 3),
        },
        "entries": list(result.entries),
        "trace": list(result.trace),
        "session": result.session,
        "events": [
            {"event": kind, "data": data}
            for kind, data in result.events
        ],
    }


def _progress_line(result: QueryRun) -> str:
    passed_checks = sum(1 for check in result.checks if check.passed)
    scores = " ".join(
        f"{item.dimension[0]}={item.soft:.2f}"
        for item in result.verdict.scores
        if not item.error
    )
    if not result.verdict.ok:
        scores = (scores + " judge=ERROR").strip()
    verdict = "PASS" if result.passed else "FAIL"
    return (
        f"{verdict} {result.case.id} "
        f"trace={passed_checks}/{len(result.checks)} {scores}"
    ).rstrip()


def _group(
    cases: Sequence[PairedCase], field: str
) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    keys = sorted({str(getattr(case, field)) for case in cases})
    for key in keys:
        group = [case for case in cases if str(getattr(case, field)) == key]
        out[key] = {
            "count": len(group),
            "strictWins": sum(1 for case in group if case.winner == "strict"),
            "baseWins": sum(1 for case in group if case.winner == "base"),
            "ties": sum(1 for case in group if case.winner == "tie"),
            "meanDelta": round(
                _mean(case.mean_delta for case in group) or 0.0, 4
            ),
        }
    return out


def _mean(values) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _round_map(values: Mapping[str, float]) -> dict[str, float]:
    return {
        key: round(value, 4) for key, value in values.items()
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clip_reason(text: str, limit: int = 2_000) -> str:
    """The report keeps the shape of a reason; judge.json keeps all of it."""
    clipped = text.strip()
    if len(clipped) > limit:
        clipped = clipped[:limit] + "…"
    return clipped


def _first_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else "unknown error"


def _clean(text: str, secret: str) -> str:
    return redact(text, (secret,)) if secret else text


__all__ = [
    "IDENTITY",
    "PROMPT_DIR",
    "PairedCase",
    "PairedSummary",
    "QueryRun",
    "RecordingModel",
    "TOOLS",
    "VARIANTS",
    "VariantRun",
    "answer_text",
    "judge_pages",
    "pair_variants",
    "record_for",
    "run_variant",
    "sections_sha256",
    "seed_corpus",
    "system_sections",
]
