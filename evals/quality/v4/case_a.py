"""Case A: a 17k raw source becomes a source-summary page.

The agent runs the real writing tools over a local fake web: the search returns
one hit, the fetch serves the fixture's HTML, and the rest of the chain
(select, frame, compose, publish) is the product's own. The judge sees the raw
source's readable text, the expected points and the page, and returns the five
point verdicts. This module also owns the fixture the calibration reads.
"""

from __future__ import annotations

import tempfile
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from evals.harness import CaseRun
from evals.quality.judge_client import (
    JudgeClient,
    conversation_from,
    make_judge_client,
)
from evals.quality.v4.judge import (
    POINT_IDS,
    JudgeMaterial,
    TaskCompliance,
    V4JudgeVerdict,
    judge_artifact,
)
from evals.quality.v4.runner import UsageCounter, preserve_case
from evals.report import PUBLISH, Check
from evals.support import remove_tree

from noteagent.acquire.derive import blocks_for_capture
from noteagent.acquire.tools.web_fetch import capture_rendered_html
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.acquire.write_raw import raw_relative_for_capture
from noteagent.agent.model import ModelClient
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.llm.config import LlmConfig
from noteagent.llm.model_gateway import OpenAIModelClient

CASE_DIR = Path(__file__).resolve().parent / "cases" / "A"
WIKI_FILE = "wiki/sources/什么是信息熵-交叉熵-相对熵.md"
SOURCE_URL = (
    "https://charlesliuyx.github.io/2018/09/24/"
    "什么是信息熵、交叉熵和相对熵/"
)
SOURCE_TITLE = "什么是信息熵、交叉熵和相对熵"
AGENT_TEMPERATURE = 0.0
SAMPLES = ("A-good", "A-defective")


@dataclass(frozen=True, slots=True)
class CaseAFixture:
    """The task, its allowed original, the expected points and the samples."""

    task: str
    capture: Capture
    material_id: str
    expected_points: tuple[str, ...]
    labels: dict[str, dict[str, str]]
    artifacts: dict[str, str]

    @property
    def material_text(self) -> str:
        return self.capture.markdown.strip()

    def judge_materials(self) -> tuple[JudgeMaterial, ...]:
        return (
            JudgeMaterial(
                self.material_id,
                SOURCE_TITLE,
                self.material_text,
            ),
        )


@dataclass(frozen=True, slots=True)
class CaseAResult:
    """Case A's deterministic checks and the judge's five points."""

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
                f"{check.name}: {check.detail}" if check.detail else check.name
                for check in self.checks
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "case": "case-A",
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


def load_fixture() -> CaseAFixture:
    """Load case A: the task, the raw HTML, expected points and the samples."""
    task = (CASE_DIR / "task.md").read_text(encoding="utf-8")
    html = (CASE_DIR / "material" / "source.html").read_text(
        encoding="utf-8"
    )
    capture = capture_rendered_html(SOURCE_URL, html)
    if capture.fidelity != "ok" or not blocks_for_capture(capture):
        raise ValueError(
            "case A's material did not capture cleanly: "
            f"{capture.fidelity} {capture.reasons}"
        )
    expected = yaml.safe_load(
        (CASE_DIR / "expected_points.yaml").read_text(encoding="utf-8")
    )
    points = tuple(str(item) for item in expected["points"])
    labels = yaml.safe_load(
        (CASE_DIR / "labels.yaml").read_text(encoding="utf-8")
    )
    if set(labels) != set(SAMPLES):
        raise ValueError(
            "case A's labels must name exactly " + ", ".join(SAMPLES)
        )
    for sample in SAMPLES:
        row = labels[sample]
        missing = [point for point in POINT_IDS if point not in row]
        bad = [
            point
            for point, verdict in row.items()
            if verdict not in ("pass", "fail")
        ]
        if missing or bad:
            raise ValueError(
                f"case A's {sample} labels are incomplete: "
                f"missing {missing}, invalid {bad}"
            )
    artifacts = {
        name: (CASE_DIR / "artifacts" / f"{name}.md").read_text(
            encoding="utf-8"
        )
        for name in SAMPLES
    }
    return CaseAFixture(
        task=task.strip(),
        capture=capture,
        material_id=raw_relative_for_capture(capture),
        expected_points=points,
        labels={
            str(sample): {
                str(point): str(verdict)
                for point, verdict in row.items()
            }
            for sample, row in labels.items()
        },
        artifacts=artifacts,
    )


def fixture_task_compliance() -> TaskCompliance:
    """Calibration samples are the requested page, by construction."""
    return TaskCompliance(
        passed=True,
        checks=("the fixture is at the requested path and shape",),
    )


def _search(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title=SOURCE_TITLE,
            url=SOURCE_URL,
            site="charlesliuyx.github.io",
            snippet="信息熵、交叉熵与相对熵的定义和关系。",
            why="",
            via="eval",
        )
    ]


def _final_text(entries: list[dict[str, Any]]) -> str:
    for role, text in reversed(conversation_from(entries)):
        if role == "assistant":
            return text
    return ""


def shape_checks(
    page_text: str, material_id: str, final_text: str
) -> tuple[Check, ...]:
    parsed = parse_page(page_text) if page_text else None
    sources = (
        parse_inline_list(parsed.fields.get("sources", ""))
        if parsed is not None
        else []
    )
    body = parsed.body if parsed is not None else ""
    lowered = final_text.casefold()
    stem = Path(WIKI_FILE).stem

    def check(name: str, passed: bool, detail: str = "") -> Check:
        return Check(name, passed, detail, PUBLISH)

    return (
        check(
            "the page exists at the requested path",
            bool(page_text),
            "" if page_text else WIKI_FILE,
        ),
        check(
            "the page cites the allowed raw source",
            any(source.startswith(f"{material_id}#") for source in sources),
            f"sources: {sources}",
        ),
        check(
            "the page has a title and sections",
            body.lstrip().startswith("# ") and "\n## " in body,
            body[:80],
        ),
        check(
            "the page is in the task's language",
            any("一" <= char <= "鿿" for char in body),
            "no CJK text",
        ),
        check(
            "the final answer names the page written",
            bool(lowered)
            and (
                stem.casefold() in lowered
                or "wiki/sources" in lowered
                or SOURCE_TITLE.casefold() in lowered
            ),
            final_text[:120],
        ),
    )


def run_case_a(
    config: LlmConfig,
    *,
    failures_root: Path,
    run_id: str,
    model: ModelClient | None = None,
    judge_client: JudgeClient | None = None,
    fixture: CaseAFixture | None = None,
) -> CaseAResult:
    """Run case A once through the real tools, then judge the page."""
    fixture = fixture or load_fixture()
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="noteagent-v4-case-a-"))
    run: CaseRun | None = None
    counter = UsageCounter()
    error = ""
    artifact = ""
    final_text = ""
    verdict = V4JudgeVerdict(error="the run did not reach the judge")
    checks: tuple[Check, ...] = (
        Check(
            "the case started",
            False,
            "it did not start",
            PUBLISH,
        ),
    )
    try:
        run = CaseRun(tmp, pages=())
        agent_config = replace(config, temperature=AGENT_TEMPERATURE)
        raw_model = model or OpenAIModelClient(agent_config)

        def fetch(_url: str) -> Capture:
            return fixture.capture

        api = run.start(
            counter.wrap(raw_model),
            model_name=agent_config.model,
            seed_system_sections=True,
            web_search=_search,
            web_fetch=fetch,
        )
        session = api.create_session(title="case-A")
        api.query(session, fixture.task)
        entries = api.entries(session)
        final_text = _final_text(entries)
        page = run.layout.root / WIKI_FILE
        artifact = (
            page.read_text(encoding="utf-8") if page.is_file() else ""
        )
        checks = shape_checks(
            artifact, fixture.material_id, final_text
        )
        result = CaseAResult(
            checks=checks,
            artifact=artifact,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            error=error,
        )
        verdict = judge_artifact(
            task=fixture.task,
            materials=fixture.judge_materials(),
            expected_points=fixture.expected_points,
            artifact_id=WIKI_FILE,
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
        result = CaseAResult(
            checks=checks,
            artifact=artifact,
            final_text=final_text,
            verdict=verdict,
            duration_s=time.monotonic() - started,
            tokens_in=counter.tokens_in,
            tokens_out=counter.tokens_out,
            error=error,
        )
    if not result.passed:
        try:
            target = failures_root / run_id / "case-A"
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


__all__ = [
    "CASE_DIR",
    "CaseAFixture",
    "CaseAResult",
    "SAMPLES",
    "SOURCE_TITLE",
    "SOURCE_URL",
    "WIKI_FILE",
    "fixture_task_compliance",
    "load_fixture",
    "run_case_a",
    "shape_checks",
]
