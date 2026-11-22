"""Run the frozen V3 quality set against a live model, locally only.

The 24 single-turn items are measured with Pass@k and Pass^k, by type and
overall; the 2 multi-turn items are reported one by one, never pooled. The
content verdict comes from the shared rubric alone; tool and trace assertions
are diagnostics beside it. Every item run keeps its project, transcript, trace
and judge verdict under the run directory.
"""

from __future__ import annotations

import json
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.golden.trace import (
    evaluate as trace_checks,
    evidence_from as trace_evidence,
)
from evals.harness import CaseRun
from evals.quality.assertions import evaluate, evidence_from
from evals.quality.artifacts import copy_case_artifacts
from evals.quality.judge_client import conversation_from, make_judge_client
from evals.quality.v3.judge import V3JudgeVerdict, judge_item
from evals.quality.v3.pricing import price_for
from evals.quality.v3.set import QualitySet
from evals.report import REPORT_VERSION, utc_now
from evals.support import remove_tree

from noteagent.agent.model import ModelClient, ModelUsageEvent
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient

K_DEFAULT = 3
TYPES = ("fact", "synthesis", "gap", "task")
SINGLE = "single"
MULTI = "multi"


@dataclass(frozen=True, slots=True)
class ItemSpec:
    """One item as the runner needs it; content plus diagnostics."""

    item_id: str
    kind: str
    type: str
    question: str
    turns: tuple[str, ...]
    pages: tuple[tuple[str, str, str], ...]
    expected_points: tuple[str, ...]
    pitfalls: tuple[str, ...]
    diagnostics: object


@dataclass(frozen=True, slots=True)
class ItemRun:
    """One run of one item, with its content verdict and its cost."""

    item_id: str
    kind: str
    type: str
    run: int
    passed: bool
    verdicts: Mapping[str, str]
    diagnostics: Mapping[str, object]
    tokens_in: int = 0
    tokens_out: int = 0
    latency_s: float = 0.0
    judge_latency_s: float = 0.0
    cost: float | None = None
    error: str = ""


@dataclass(frozen=True, slots=True)
class ItemScores:
    item_id: str
    kind: str
    type: str
    runs: tuple[bool, ...]

    @property
    def passed_at_k(self) -> bool:
        return any(self.runs)

    @property
    def passed_hat_k(self) -> bool:
        return bool(self.runs) and all(self.runs)


@dataclass(frozen=True, slots=True)
class Rate:
    passed: int
    total: int

    @property
    def rate(self) -> float | None:
        return round(self.passed / self.total, 4) if self.total else None

    def to_payload(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "total": self.total,
            "rate": self.rate,
        }


@dataclass(frozen=True, slots=True)
class TypeScores:
    type: str
    pass_at_k: Rate
    pass_hat_k: Rate


@dataclass(frozen=True, slots=True)
class QualityScores:
    k: int
    single: tuple[ItemScores, ...]
    multi: tuple[ItemScores, ...]

    @classmethod
    def from_runs(
        cls, runs: list[ItemRun], *, k: int
    ) -> "QualityScores":
        grouped: dict[tuple[str, str, str], list[ItemRun]] = {}
        for run in runs:
            grouped.setdefault(
                (run.item_id, run.kind, run.type), []
            ).append(run)
        single: list[ItemScores] = []
        multi: list[ItemScores] = []
        for (item_id, kind, kind_type), items in grouped.items():
            by_run = {item.run: item.passed for item in items}
            passes = tuple(by_run.get(index, False) for index in range(k))
            score = ItemScores(item_id, kind, kind_type, passes)
            (single if kind == SINGLE else multi).append(score)
        return cls(k, tuple(single), tuple(multi))

    @property
    def overall(self) -> TypeScores:
        return _type_scores("overall", self.single)

    @property
    def by_type(self) -> tuple[TypeScores, ...]:
        return tuple(
            _type_scores(
                kind_type,
                [item for item in self.single if item.type == kind_type],
            )
            for kind_type in TYPES
        )


@dataclass(frozen=True, slots=True)
class QualityReport:
    scores: QualityScores
    runs: tuple[ItemRun, ...]
    set_version: str
    content_hash: str
    model: str
    started_at: str
    duration_s: float

    def payload(self) -> dict[str, object]:
        return {
            "version": REPORT_VERSION,
            "setVersion": self.set_version,
            "contentHash": self.content_hash,
            "k": self.scores.k,
            "model": self.model,
            "startedAt": self.started_at,
            "durationSeconds": round(self.duration_s, 3),
            "singleTurn": {
                "overall": _type_payload(self.scores.overall),
                "byType": {
                    item.type: _type_payload(item)
                    for item in self.scores.by_type
                },
                "items": [
                    _single_item_payload(item, self.runs)
                    for item in self.scores.single
                ],
            },
            "multiTurn": [
                _multi_payload(item, self.runs)
                for item in self.scores.multi
            ],
        }

    def markdown(self) -> str:
        lines = [
            "# V3 quality eval",
            "",
            f"- Set: {self.set_version} ({self.content_hash[:12]})",
            f"- Model: {self.model}",
            f"- k: {self.scores.k}",
            f"- Started: {self.started_at}",
            f"- Duration: {self.duration_s:.1f}s",
            "",
            "## Single-turn",
            "",
            "| Type | Items | Pass@k | Pass^k |",
            "|---|---|---|---|",
        ]
        for item in (self.scores.overall, *self.scores.by_type):
            lines.append(
                f"| {item.type} | {item.pass_at_k.total} | "
                f"{_rate_text(item.pass_at_k)} | "
                f"{_rate_text(item.pass_hat_k)} |"
            )
        lines.extend(
            [
                "",
                "## Single-turn items",
                "",
                "| Item | Type | Pass@k | Pass^k | Tokens in/out | Cost | Latency | Judge |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for item in self.scores.single:
            items = [
                run
                for run in self.runs
                if run.item_id == item.item_id and run.kind == SINGLE
            ]
            tokens_in = sum(run.tokens_in for run in items)
            tokens_out = sum(run.tokens_out for run in items)
            cost = _sum_cost(items)
            lines.append(
                f"| `{item.item_id}` | {item.type} | "
                f"{'pass' if item.passed_at_k else 'fail'} | "
                f"{'pass' if item.passed_hat_k else 'fail'} | "
                f"{tokens_in}/{tokens_out} | "
                f"{'n/a' if cost is None else f'{cost:.4f}'} | "
                f"{sum(run.latency_s for run in items):.1f}s | "
                f"{sum(run.judge_latency_s for run in items):.1f}s |"
            )
        lines.extend(
            [
                "",
                "## Multi-turn (individual, never pooled)",
                "",
                "| Case | Run | Pass | Grounding | Completeness | Uncertainty | Hallucination | Tokens in/out | Cost | Latency | Judge |",
                "|---|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        if not self.scores.multi:
            lines.append("| none | | | | | | | | | | |")
        for item in self.scores.multi:
            for run in sorted(
                (
                    run
                    for run in self.runs
                    if run.item_id == item.item_id
                    and run.kind == MULTI
                ),
                key=lambda run: run.run,
            ):
                verdicts = [
                    run.verdicts.get(name, "-")
                    for name in (
                        "grounding",
                        "completeness",
                        "uncertainty",
                        "hallucination",
                    )
                ]
                lines.append(
                    f"| `{item.item_id}` | {run.run + 1} | "
                    f"{'PASS' if run.passed else 'FAIL'} | "
                    f"{' | '.join(verdicts)} | "
                    f"{run.tokens_in}/{run.tokens_out} | "
                    f"{'n/a' if run.cost is None else f'{run.cost:.4f}'} | "
                    f"{run.latency_s:.1f}s | "
                    f"{run.judge_latency_s:.1f}s |"
                )
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        return path


RunItem = Callable[[ItemSpec, int], ItemRun]


def run_quality_set(
    quality: QualitySet,
    config: LlmConfig,
    artifacts_root: Path,
    *,
    k: int = K_DEFAULT,
    model: ModelClient | None = None,
    judge_client: Any | None = None,
    run_item: RunItem | None = None,
    input_cost_per_mtok: float | None = None,
    output_cost_per_mtok: float | None = None,
) -> QualityReport:
    """Run every item k times; *run_item* is the test seam."""
    if k < 1:
        raise ValueError("k must be at least 1")
    specs = item_specs(quality)
    runner: RunItem = run_item or (
        lambda spec, index: run_item_once(
            quality,
            spec,
            index,
            config,
            artifacts_root,
            model=model,
            judge_client=judge_client,
            input_cost_per_mtok=input_cost_per_mtok,
            output_cost_per_mtok=output_cost_per_mtok,
        )
    )
    started_at = utc_now()
    started = time.monotonic()
    runs = [runner(spec, index) for spec in specs for index in range(k)]
    scores = QualityScores.from_runs(runs, k=k)
    return QualityReport(
        scores=scores,
        runs=tuple(runs),
        set_version=quality.version,
        content_hash=quality.content_hash,
        model=getattr(config, "model", "") or "",
        started_at=started_at,
        duration_s=time.monotonic() - started,
    )


def item_specs(quality: QualitySet) -> tuple[ItemSpec, ...]:
    """The 24 single-turn queries then the 2 multi-turn cases."""
    specs: list[ItemSpec] = []
    for query in quality.single_turn:
        pages = tuple(
            (
                page.slug,
                page.title,
                page.body,
            )
            for page in (
                quality.golden.corpus.page(slug)
                for slug in query.corpus_pages
            )
        )
        specs.append(
            ItemSpec(
                item_id=query.id,
                kind=SINGLE,
                type=query.type,
                question=query.question,
                turns=(query.question,),
                pages=pages,
                expected_points=query.key_points,
                pitfalls=query.forbidden,
                diagnostics=query,
            )
        )
    for case in quality.multi_turn:
        specs.append(
            ItemSpec(
                item_id=case.name,
                kind=MULTI,
                type=MULTI,
                question=" / ".join(case.turns),
                turns=case.turns,
                pages=tuple(
                    (page.slug, page.title, page.body)
                    for page in case.wiki
                ),
                expected_points=quality.expected_points(case),
                pitfalls=quality.pitfalls(case),
                diagnostics=case.assertions,
            )
        )
    return tuple(specs)


def run_item_once(
    quality: QualitySet,
    spec: ItemSpec,
    index: int,
    config: LlmConfig,
    artifacts_root: Path,
    *,
    model: ModelClient | None = None,
    judge_client: Any | None = None,
    input_cost_per_mtok: float | None = None,
    output_cost_per_mtok: float | None = None,
) -> ItemRun:
    """Run one item once through the real HTTP/SSE path."""
    started = time.monotonic()
    target = (
        Path(artifacts_root) / spec.item_id / f"run-{index + 1}"
    )
    target.mkdir(parents=True, exist_ok=True)
    raw_model = model or OpenAIModelClient(config)
    recording = _RecordingModel(raw_model)
    run: CaseRun | None = None
    session_id: str | None = None
    entries: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    verdict = V3JudgeVerdict(error="the run did not reach the judge")
    diagnostics: Mapping[str, object] = {}
    error = ""
    agent_latency = 0.0
    judge_latency = 0.0
    tmp = Path(tempfile.mkdtemp(prefix=f"noteagent-quality-v3-{spec.item_id}-"))
    try:
        run = CaseRun(tmp, pages=list(spec.pages))
        api = run.start(recording, model_name=config.model)
        session_id = api.create_session(title=spec.item_id)
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
        diagnostics = _diagnostics(spec, entries, trace, turns)
        agent_latency = time.monotonic() - started
        judge_started = time.monotonic()
        verdict = judge_item(
            quality.rubric,
            question=spec.question,
            wiki=tuple((title, body) for _slug, title, body in spec.pages),
            conversation=conversation_from(entries),
            expected_points=spec.expected_points,
            pitfalls=spec.pitfalls,
            client=judge_client or make_judge_client(config),
            secret=config.api_key,
        )
        judge_latency = time.monotonic() - judge_started
    except Exception:
        error = _last_line(traceback.format_exc())
    finally:
        if run is not None:
            copy_case_artifacts(run, session_id, target)
            run.close()
        remove_tree(tmp)

    input_price = input_cost_per_mtok
    output_price = output_cost_per_mtok
    if input_price is None or output_price is None:
        price = price_for(config.model)
        if price is not None:
            input_price, output_price = price
    passed = bool(verdict.passed(quality.rubric)) and not error
    item = ItemRun(
        item_id=spec.item_id,
        kind=spec.kind,
        type=spec.type,
        run=index,
        passed=passed,
        verdicts=verdict.verdicts(),
        diagnostics=diagnostics,
        tokens_in=recording.input_tokens,
        tokens_out=recording.output_tokens,
        latency_s=agent_latency,
        judge_latency_s=judge_latency,
        cost=_cost(
            recording.input_tokens,
            recording.output_tokens,
            input_price,
            output_price,
        ),
        error=error or verdict.error,
    )
    (target / "case.json").write_text(
        json.dumps(
            {
                "item": spec.item_id,
                "run": index,
                "passed": item.passed,
                "verdicts": dict(item.verdicts),
                "diagnostics": item.diagnostics,
                "tokensIn": item.tokens_in,
                "tokensOut": item.tokens_out,
                "latencySeconds": round(item.latency_s, 3),
                "judgeLatencySeconds": round(item.judge_latency_s, 3),
                "cost": item.cost,
                "error": item.error,
                "judge": verdict.to_payload(),
                "turns": turns,
                "entries": entries,
                "trace": trace,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return item


class _RecordingModel:
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


def _diagnostics(
    spec: ItemSpec,
    entries: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> Mapping[str, object]:
    if spec.kind == MULTI:
        evidence = evidence_from(
            entries=entries,
            trace=trace,
            events=[
                (str(item["event"]), item["data"])
                for turn in turns
                for item in turn["events"]
            ],
        )
        results = evaluate(spec.diagnostics, evidence)
        return {
            "kind": "tools",
            "passed": all(item.passed for item in results),
            "assertions": [item.to_payload() for item in results],
        }
    evidence = trace_evidence(
        entries=entries,
        trace=trace,
        events=[
            (str(item["event"]), item["data"])
            for turn in turns
            for item in turn["events"]
        ],
    )
    checks = trace_checks(spec.diagnostics, evidence)
    return {
        "kind": "trace",
        "passed": all(check.passed for check in checks),
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "expected": check.expected,
                "actual": check.actual,
                "detail": check.detail,
            }
            for check in checks
        ],
    }


def _type_scores(type_: str, items: list[ItemScores]) -> TypeScores:
    return TypeScores(
        type=type_,
        pass_at_k=Rate(
            sum(1 for item in items if item.passed_at_k), len(items)
        ),
        pass_hat_k=Rate(
            sum(1 for item in items if item.passed_hat_k), len(items)
        ),
    )


def _type_payload(scores: TypeScores) -> dict[str, object]:
    return {
        "passAtK": scores.pass_at_k.to_payload(),
        "passHatK": scores.pass_hat_k.to_payload(),
    }


def _single_item_payload(
    item: ItemScores, runs: tuple[ItemRun, ...]
) -> dict[str, object]:
    items = [
        run
        for run in runs
        if run.item_id == item.item_id and run.kind == SINGLE
    ]
    return {
        "id": item.item_id,
        "type": item.type,
        "runs": list(item.runs),
        "passAtK": item.passed_at_k,
        "passHatK": item.passed_hat_k,
        "tokensIn": sum(run.tokens_in for run in items),
        "tokensOut": sum(run.tokens_out for run in items),
        "cost": _sum_cost(items),
        "latencySeconds": round(
            sum(run.latency_s for run in items), 3
        ),
        "judgeLatencySeconds": round(
            sum(run.judge_latency_s for run in items), 3
        ),
    }


def _multi_payload(
    item: ItemScores, runs: tuple[ItemRun, ...]
) -> dict[str, object]:
    items = [
        run
        for run in runs
        if run.item_id == item.item_id and run.kind == MULTI
    ]
    return {
        "name": item.item_id,
        "tokensIn": sum(run.tokens_in for run in items),
        "tokensOut": sum(run.tokens_out for run in items),
        "cost": _sum_cost(items),
        "latencySeconds": round(
            sum(run.latency_s for run in items), 3
        ),
        "judgeLatencySeconds": round(
            sum(run.judge_latency_s for run in items), 3
        ),
        "runs": [
            {
                "run": run.run,
                "passed": run.passed,
                "verdicts": dict(run.verdicts),
                "diagnostics": dict(run.diagnostics),
                "tokensIn": run.tokens_in,
                "tokensOut": run.tokens_out,
                "latencySeconds": round(run.latency_s, 3),
                "judgeLatencySeconds": round(run.judge_latency_s, 3),
                "cost": run.cost,
                "error": run.error,
            }
            for run in sorted(
                (
                    run
                    for run in runs
                    if run.item_id == item.item_id and run.kind == MULTI
                ),
                key=lambda run: run.run,
            )
        ],
    }


def _sum_cost(items: list[ItemRun]) -> float | None:
    values = [run.cost for run in items]
    if any(value is None for value in values):
        return None
    return round(sum(value for value in values if value is not None), 6)


def _cost(
    tokens_in: int,
    tokens_out: int,
    input_cost_per_mtok: float | None,
    output_cost_per_mtok: float | None,
) -> float | None:
    if input_cost_per_mtok is None or output_cost_per_mtok is None:
        return None
    return round(
        tokens_in / 1_000_000 * input_cost_per_mtok
        + tokens_out / 1_000_000 * output_cost_per_mtok,
        6,
    )


def _rate_text(rate: Rate) -> str:
    return (
        f"{rate.passed}/{rate.total}"
        if rate.total
        else "n/a"
    )


def _last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


__all__ = [
    "ItemRun",
    "ItemScores",
    "ItemSpec",
    "K_DEFAULT",
    "MULTI",
    "QualityReport",
    "QualityScores",
    "Rate",
    "SINGLE",
    "TYPES",
    "TypeScores",
    "item_specs",
    "run_item_once",
    "run_quality_set",
]
