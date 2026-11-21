"""The paired writing quality report and its baseline.

The JSON is the machine record; the Markdown is the short read: per case, what
the skill changed, and where it did not. A run that shows no improvement says
so in the report rather than hiding it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.quality.writing.runner import CasePair

REPORT_VERSION = 1


_VERDICT_TEXT = {
    "improved": "the skill improved this case",
    "unchanged": "no measurable difference",
    "worse": "the skill did worse on this case",
}


def verdict_text(verdict: str) -> str:
    return _VERDICT_TEXT.get(verdict, verdict)


@dataclass(frozen=True, slots=True)
class WritingReport:
    pairs: tuple[CasePair, ...]
    started_at: str
    duration_s: float
    #: What a future delta can be attributed to: the model, endpoint and judge.
    model: str = ""
    base_url: str = ""
    judge_model: str = ""
    judge_prompt_sha256: str = ""

    @property
    def passed(self) -> bool:
        """Whether every arm actually ran and every trajectory check held."""
        return all(
            pair.skill.passed and pair.plain.passed
            for pair in self.pairs
        )

    @property
    def usable(self) -> bool:
        """Whether both judges answered; a failed verdict is still a result."""
        return all(
            pair.skill.judge_usable and pair.plain.judge_usable
            for pair in self.pairs
        )

    def improved(self) -> tuple[CasePair, ...]:
        return tuple(
            pair for pair in self.pairs if pair.verdict == "improved"
        )

    def unchanged(self) -> tuple[CasePair, ...]:
        return tuple(
            pair for pair in self.pairs if pair.verdict == "unchanged"
        )

    def worse(self) -> tuple[CasePair, ...]:
        return tuple(
            pair for pair in self.pairs if pair.verdict == "worse"
        )

    def payload(self) -> dict[str, Any]:
        return {
            "version": REPORT_VERSION,
            "startedAt": self.started_at,
            "durationSeconds": round(self.duration_s, 3),
            "passed": self.passed,
            "usable": self.usable,
            "model": self.model,
            "baseUrl": self.base_url,
            "judgeModel": self.judge_model,
            "judgePromptSha256": self.judge_prompt_sha256,
            "cases": [
                {
                    "name": pair.case.name,
                    "title": pair.case.title,
                    "verdict": pair.verdict,
                    "skill": pair.skill.to_payload(),
                    "plain": pair.plain.to_payload(),
                    "delta": dict(pair.deltas),
                }
                for pair in self.pairs
            ],
        }

    def markdown(self) -> str:
        lines = [
            "# Writing quality A/B",
            "",
            f"- Started: {self.started_at}",
            f"- Duration: {self.duration_s:.1f}s",
            f"- Passed: {self.passed}",
            f"- Usable judge: {self.usable}",
            "",
            "| Case | Arm | Checks | Completion | Sections | Citation | Judge |",
            "|---|---|---|---|---|---|---|",
        ]
        for pair in self.pairs:
            for arm in (pair.skill, pair.plain):
                checks = (
                    f"{sum(1 for c in arm.checks if c.passed)}"
                    f"/{len(arm.checks)}"
                )
                judge = (
                    f"{arm.judge.score()}/4"
                    if arm.judge_usable
                    else "error"
                )
                lines.append(
                    f"| {pair.case.name} | "
                    f"{'skill' if arm.skill else 'plain'} | {checks} | "
                    f"{arm.metrics.get('completion', 0)} | "
                    f"{arm.metrics.get('sections', 0)} | "
                    f"{arm.metrics.get('citation_coverage', 0)} | "
                    f"{judge} |"
                )
        lines.extend(["", "## Deltas (skill - plain)"])
        for pair in self.pairs:
            deltas = ", ".join(
                f"{name}={value}"
                for name, value in sorted(pair.deltas.items())
            )
            lines.append(
                f"- `{pair.case.name}`: {verdict_text(pair.verdict)}. "
                f"{deltas}"
            )
        if not self.improved():
            lines.extend(
                [
                    "",
                    "## Next step",
                    "",
                    "The skill did not improve these cases. Before changing "
                    "the skill again, look at the judge's reasons in each "
                    "arm's report and at the pages under the run directory: "
                    "either the recipe needs one more teaching sentence "
                    "(frames, section pacing, citation rules), or the case "
                    "already passes without it and does not measure the "
                    "skill.",
                ]
            )
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.payload(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        path.with_suffix(".md").write_text(
            self.markdown(), encoding="utf-8"
        )
        path.with_suffix(".html").write_text(
            self.html(), encoding="utf-8"
        )
        return path

    def html(self) -> str:
        rows = []
        for pair in self.pairs:
            for arm in (pair.skill, pair.plain):
                judge = (
                    f"{arm.judge.score()}/4"
                    if arm.judge_usable
                    else "error"
                )
                rows.append(
                    "<tr>"
                    f"<td>{_escape(pair.case.name)}</td>"
                    f"<td>{'skill' if arm.skill else 'plain'}</td>"
                    f"<td>{arm.metrics.get('completion', 0)}</td>"
                    f"<td>{arm.metrics.get('sections', 0)}</td>"
                    f"<td>{arm.metrics.get('citation_coverage', 0)}</td>"
                    f"<td>{judge}</td>"
                    f"<td>{_escape(verdict_text(pair.verdict))}</td>"
                    "</tr>"
                )
        meta = (
            f"model: {_escape(self.model)} · "
            f"judge: {_escape(self.judge_model)} · "
            f"prompt: {_escape(self.judge_prompt_sha256[:12])}"
        )
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Writing quality A/B</title></head><body>"
            "<h1>Writing quality A/B</h1>"
            f"<p>{_escape(self.started_at)} · {meta}</p>"
            "<table border='1' cellpadding='4'>"
            "<tr><th>Case</th><th>Arm</th><th>Completion</th>"
            "<th>Sections</th><th>Citation</th><th>Judge</th>"
            "<th>Verdict</th></tr>"
            + "".join(rows)
            + "</table></body></html>\n"
        )


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def baseline_payload(report: WritingReport) -> dict[str, Any]:
    """A small baseline: per case and arm, the metrics and judge score."""
    return {
        "version": REPORT_VERSION,
        "createdAt": report.started_at,
        "model": report.model,
        "baseUrl": report.base_url,
        "judgeModel": report.judge_model,
        "judgePromptSha256": report.judge_prompt_sha256,
        "cases": {
            pair.case.name: {
                "skill": dict(pair.skill.metrics),
                "plain": dict(pair.plain.metrics),
            }
            for pair in report.pairs
        },
    }


def read_baseline(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_baseline(path: Path, report: WritingReport) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline_payload(report), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def compare(report: WritingReport, baseline: dict[str, Any]) -> list[str]:
    """The sentences a reader needs when today's run moved off the baseline."""
    notes: list[str] = []
    cases = baseline.get("cases") or {}
    for pair in report.pairs:
        old = cases.get(pair.case.name) or {}
        for arm_name, arm in (
            ("skill", pair.skill),
            ("plain", pair.plain),
        ):
            previous = old.get(arm_name) or {}
            for metric in ("completion", "judge_score", "write_success"):
                before = previous.get(metric)
                after = arm.metrics.get(metric)
                if before is None or after is None:
                    continue
                if after < before:
                    notes.append(
                        f"{pair.case.name}/{arm_name}: {metric} fell "
                        f"from {before} to {after}"
                    )
    return notes


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "WritingReport",
    "baseline_payload",
    "compare",
    "read_baseline",
    "utc_now",
    "verdict_text",
    "write_baseline",
]
