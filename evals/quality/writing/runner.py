"""Run one writing quality case with and without the writing skill.

Each arm gets its own temporary project and real model; the state is seeded
with the case's selected pages, so the model does the writing work (frame,
compose, publish) rather than the search. The trajectory checks and the judge
are independent: a page can be written and still be graded poorly.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.harness import CaseRun
from evals.quality.judge import make_judge_client
from evals.quality.writing.cases import WritingCase
from evals.quality.writing.judge import WritingJudge, judge_page

from noteagent.agent.model import ModelClient
from noteagent.agent.projection import project
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient
from noteagent.prompts.writing_skill import writing_skill_text
from noteagent.writing.state import WritingState, WritingStateStore

REQUIRED_TOOLS = ("get_state", "select_frame", "compose_wiki", "write_wiki")


@dataclass(frozen=True, slots=True)
class ArmCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArmResult:
    """One arm's run: what it did, what the checks said, and the judge's read."""

    skill: bool
    status: int
    error: str
    tool_names: tuple[str, ...]
    checks: tuple[ArmCheck, ...]
    metrics: Mapping[str, float | int]
    judge: WritingJudge
    page: str
    entries_path: str = ""

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and bool(self.checks)
            and all(item.passed for item in self.checks)
        )

    @property
    def judge_usable(self) -> bool:
        return not self.judge.error

    def to_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "status": self.status,
            "passed": self.passed,
            "error": self.error,
            "toolNames": list(self.tool_names),
            "checks": [
                {
                    "name": item.name,
                    "passed": item.passed,
                    "detail": item.detail,
                }
                for item in self.checks
            ],
            "metrics": dict(self.metrics),
            "judge": self.judge.to_payload(),
            "page": self.page,
            "entries": self.entries_path,
        }


@dataclass(frozen=True, slots=True)
class CasePair:
    """One case run twice; the deltas are skill minus plain."""

    case: WritingCase
    skill: ArmResult
    plain: ArmResult

    @property
    def deltas(self) -> dict[str, float | int]:
        return _deltas(self.skill, self.plain)

    @property
    def verdict(self) -> str:
        """``improved`` / ``unchanged`` / ``worse``, from the paired deltas."""
        judge = int(self.deltas.get("judge_score", 0))
        completion = int(self.deltas.get("completion", 0))
        if judge > 0 or (judge == 0 and completion > 0):
            return "improved"
        if judge == 0 and completion == 0:
            return "unchanged"
        return "worse"


def _tool_names(entries: list[dict[str, Any]]) -> tuple[str, ...]:
    names: list[str] = []
    for entry in entries:
        if entry.get("type") != "message":
            continue
        for call in entry.get("toolCalls") or []:
            names.append(str(call.get("name") or ""))
    return tuple(names)


def _seed_state(run: CaseRun, case: WritingCase) -> None:
    store = WritingStateStore(run.layout, clock=run.clock)
    rows: list[dict[str, object]] = []
    for index, source in enumerate(case.sources, 1):
        page_id = f"page-{index}"
        blocks = [
            {"id": f"b{position}", "tag": "p", "text": text}
            for position, text in enumerate(source.blocks, 1)
        ]
        store.save_page_blocks(page_id, blocks)
        material = (
            f"raw/sources/web/eval/{case.name}-{index}.html"
        )
        store.save_page_material(
            page_id, source.html().encode("utf-8"), material
        )
        rows.append(
            {
                "id": page_id,
                "hitId": f"hit-{index}",
                "url": source.url,
                "title": source.title,
                "status": "ready",
                "blockCount": len(blocks),
                "version": 1,
                "material": material,
            }
        )
    store.save(
        WritingState(
            selected_pages=tuple(rows),
            frames=(),
            draft={"pages": []},
        )
    )


def _draft_pages(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    draft = state.get("draft") or {}
    pages = draft.get("pages") if isinstance(draft, Mapping) else None
    return tuple(pages or ())


def _all_sections(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        section
        for page in _draft_pages(state)
        for section in page.get("sections") or ()
    )


def _tokens(entries: list[dict[str, Any]]) -> int:
    total = 0
    for entry in entries:
        usage = entry.get("usage")
        if not isinstance(usage, Mapping):
            continue
        value = usage.get("totalTokens", usage.get("total_tokens", 0))
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def _metrics(
    status: int,
    entries: list[dict[str, Any]],
    state: Mapping[str, Any],
    latency_s: float,
) -> dict[str, float | int]:
    pages = _draft_pages(state)
    sections = _all_sections(state)
    cited = sum(1 for section in sections if section.get("citations"))
    stale = sum(1 for section in sections if section.get("stale"))
    written = [page for page in pages if page.get("status") == "written"]
    calls = sum(
        len(entry.get("toolCalls") or [])
        for entry in entries
        if entry.get("type") == "message"
    )
    rounds = sum(
        1
        for entry in entries
        if entry.get("type") == "message"
        and entry.get("toolCalls")
    )
    return {
        "completion": 1 if status == 200 and written else 0,
        "tool_calls": calls,
        "tool_rounds": rounds,
        "sections": len(sections),
        "citation_coverage": (
            round(cited / len(sections), 2) if sections else 0.0
        ),
        "stale_sections": stale,
        "unresolved_sections": stale
        + (len(sections) - cited),
        "write_success": 1 if written else 0,
        "latency_s": round(latency_s, 2),
        "tokens": _tokens(entries),
    }


def _page_text(run: CaseRun, state: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    for page in _draft_pages(state):
        path = page.get("wikiFile") or ""
        file = run.layout.root / path if path else None
        if file is not None and file.is_file():
            chunks.append(file.read_text(encoding="utf-8"))
            continue
        chunks.append(
            "\n\n".join(
                f"## {section.get('heading')}\n\n{section.get('body')}"
                for section in page.get("sections") or ()
            )
        )
    return "\n\n".join(chunks)


def _checks(
    status: int,
    tool_names: tuple[str, ...],
    state: Mapping[str, Any],
    sections: Mapping[str, str],
    skill: bool,
) -> tuple[ArmCheck, ...]:
    pages = _draft_pages(state)
    draft_sections = _all_sections(state)
    cited = sum(1 for section in draft_sections if section.get("citations"))
    compose_calls = tool_names.count("compose_wiki")
    has_skill = str(sections.get("skills") or "").strip() == writing_skill_text()
    return (
        ArmCheck("the query completes", status == 200, f"status {status}"),
        ArmCheck(
            "the recipe's tools are used",
            all(name in tool_names for name in REQUIRED_TOOLS),
            f"tools {list(tool_names)}",
        ),
        ArmCheck("a page is published", any(
            page.get("status") == "written" for page in pages
        )),
        ArmCheck("the page has sections", bool(draft_sections)),
        ArmCheck(
            "every section cites its sources",
            bool(draft_sections) and cited == len(draft_sections),
            f"{cited}/{len(draft_sections)} cited",
        ),
        ArmCheck(
            "the compose is chunked as the skill says",
            compose_calls >= 1
            and len(draft_sections) <= 4 * compose_calls,
            f"{len(draft_sections)} sections in {compose_calls} compose calls",
        ),
        ArmCheck(
            "the skill section matches the arm",
            has_skill if skill else not has_skill,
        ),
    )


def run_arm(
    case: WritingCase,
    *,
    skill: bool,
    artifacts: Path,
    config: LlmConfig | None = None,
    model: ModelClient | None = None,
    judge_client=None,
) -> ArmResult:
    """Run *case* once, with or without the writing skill."""
    arm = "skill" if skill else "plain"
    target = Path(artifacts) / case.name / arm
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    run: CaseRun | None = None
    status = 0
    error = ""
    tool_names: tuple[str, ...] = ()
    state: Mapping[str, Any] = {}
    entries: list[dict[str, Any]] = []
    page = ""
    judge = WritingJudge(error="not run")
    checks: tuple[ArmCheck, ...] = ()
    metrics: dict[str, float | int] = {}
    with tempfile.TemporaryDirectory(
        prefix=f"noteagent-writing-quality-{case.name}-{arm}-"
    ) as tmp:
        try:
            run = CaseRun(Path(tmp))
            api = run.start(
                model or OpenAIModelClient(config),
                writing_skill=skill,
            )
            _seed_state(run, case)
            session = api.create_session(case.title)
            reply = api.query(session, case.request)
            status = reply.status_code
            entries = api.entries(session)
            state = api.request(
                "GET", "/api/projects/demo/writing/state"
            ).json()
            tool_names = _tool_names(entries)
            prompt_sections = project(entries).sections
            page = _page_text(run, state)
            metrics = _metrics(
                status, entries, state, time.monotonic() - started
            )
            judge = judge_page(
                case,
                page,
                client=judge_client or make_judge_client(config),
                secret=config.api_key if config is not None else "",
            )
            metrics["judge_score"] = judge.score()
            metrics["judge_pass"] = 1 if judge.passed else 0
            checks = _checks(
                status,
                tool_names,
                state,
                prompt_sections,
                skill,
            )
        except Exception:
            error = traceback.format_exc()
        finally:
            if run is not None:
                _copy(run, target)
                run.close()
    entries_path = ""
    if run is not None:
        entries_path = str(
            target / "project" / ".noteagent" / "sessions"
        )
    return ArmResult(
        skill=skill,
        status=status,
        error=error,
        tool_names=tool_names,
        checks=checks,
        metrics=metrics,
        judge=judge,
        page=page,
        entries_path=entries_path,
    )


def _copy(run: CaseRun, target: Path) -> None:
    project = target / "project"
    if project.exists():
        shutil.rmtree(project)
    # A run that just ended can still have an atomic-write temp file here.
    shutil.copytree(
        run.layout.root,
        project,
        ignore=shutil.ignore_patterns("*.tmp"),
    )
    (target / "state.json").write_text(
        json.dumps(
            WritingStateStore(run.layout, clock=run.clock).load().to_dict(),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _deltas(
    skill: ArmResult, plain: ArmResult
) -> dict[str, float | int]:
    keys = sorted(
        set(skill.metrics) | set(plain.metrics)
    )
    return {
        key: skill.metrics.get(key, 0) - plain.metrics.get(key, 0)
        for key in keys
    }


def run_pair(
    case: WritingCase,
    *,
    artifacts: Path,
    config: LlmConfig | None = None,
    model: ModelClient | None = None,
    judge_client=None,
) -> CasePair:
    """The paired A/B: the same case with the skill and without it."""
    skill = run_arm(
        case,
        skill=True,
        artifacts=artifacts,
        config=config,
        model=model,
        judge_client=judge_client,
    )
    plain = run_arm(
        case,
        skill=False,
        artifacts=artifacts,
        config=config,
        model=model,
        judge_client=judge_client,
    )
    return CasePair(case=case, skill=skill, plain=plain)


__all__ = [
    "ArmCheck",
    "ArmResult",
    "CasePair",
    "REQUIRED_TOOLS",
    "run_arm",
    "run_pair",
]
