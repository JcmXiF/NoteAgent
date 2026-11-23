"""The fixture shape the page-writing V4 quality cases share.

A case owns a task, one or more raw originals, its expected points, and the
calibration samples with their human labels. The loader checks the shapes once,
so a typo in a label file fails at load, not as a mystery in a judge verdict.
A case with no raw originals (a searched answer) can reuse the shape; the
shape checks then simply have no source to cite.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from evals.quality.v4.judge import (
    POINT_IDS,
    JudgeMaterial,
    TaskCompliance,
)

from noteagent.acquire.derive import blocks_for_capture
from noteagent.acquire.tools.web_fetch import capture_rendered_html
from noteagent.acquire.types import Capture
from noteagent.acquire.write_raw import raw_relative_for_capture


@dataclass(frozen=True, slots=True)
class CaseSource:
    """One allowed original: its URL, its title and its captured text."""

    url: str
    title: str
    capture: Capture
    material_id: str


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One artifact with the human labels it must be judged against."""

    name: str
    artifact: str
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class QualityCase:
    """A task, its allowed originals, expected points and samples."""

    id: str
    wiki_file: str
    page_title: str
    task: str
    sources: tuple[CaseSource, ...]
    expected_points: tuple[str, ...]
    samples: tuple[CalibrationSample, ...]

    @property
    def sample_names(self) -> tuple[str, ...]:
        return tuple(sample.name for sample in self.samples)

    @property
    def material_ids(self) -> tuple[str, ...]:
        return tuple(source.material_id for source in self.sources)

    def judge_materials(self) -> tuple[JudgeMaterial, ...]:
        return tuple(
            JudgeMaterial(
                source.material_id,
                source.title,
                source.capture.markdown.strip(),
            )
            for source in self.sources
        )


def load_case(
    *,
    case_id: str,
    directory: Path,
    wiki_file: str,
    page_title: str,
    samples: tuple[str, ...],
    sources: tuple[tuple[str, str, str], ...],
) -> QualityCase:
    """Load one case's task, materials, points, samples and labels."""
    task = (directory / "task.md").read_text(encoding="utf-8").strip()
    if not task:
        raise ValueError(f"case {case_id}: the task is empty")
    expected = yaml.safe_load(
        (directory / "expected_points.yaml").read_text(encoding="utf-8")
    )
    points = tuple(str(item) for item in expected["points"])
    if not points:
        raise ValueError(f"case {case_id}: no expected points")
    labels = yaml.safe_load(
        (directory / "labels.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(labels, dict) or not labels:
        raise ValueError(f"case {case_id}: no calibration labels")
    if set(labels) != set(samples):
        raise ValueError(
            f"case {case_id}: labels must name exactly "
            + ", ".join(samples)
        )
    loaded_samples: list[CalibrationSample] = []
    for name in samples:
        row = labels[name]
        if not isinstance(row, dict):
            raise ValueError(
                f"case {case_id}: {name} labels must be a mapping"
            )
        missing = [point for point in POINT_IDS if point not in row]
        bad = [
            point
            for point, verdict in row.items()
            if verdict not in ("pass", "fail")
        ]
        if missing or bad:
            raise ValueError(
                f"case {case_id}: {name} labels are incomplete: "
                f"missing {missing}, invalid {bad}"
            )
        artifact = (
            directory / "artifacts" / f"{name}.md"
        ).read_text(encoding="utf-8")
        if not artifact.strip():
            raise ValueError(
                f"case {case_id}: {name} has an empty artifact"
            )
        loaded_samples.append(
            CalibrationSample(
                name=str(name),
                artifact=artifact,
                labels={
                    str(point): str(verdict)
                    for point, verdict in row.items()
                },
            )
        )
    loaded: list[CaseSource] = []
    for url, title, filename in sources:
        html = (directory / "material" / filename).read_text(
            encoding="utf-8"
        )
        capture = capture_rendered_html(url, html)
        if capture.fidelity != "ok" or not blocks_for_capture(capture):
            raise ValueError(
                f"case {case_id}: {filename} did not capture cleanly: "
                f"{capture.fidelity} {capture.reasons}"
            )
        loaded.append(
            CaseSource(
                url=url,
                title=title,
                capture=capture,
                material_id=raw_relative_for_capture(capture),
            )
        )
    return QualityCase(
        id=case_id,
        wiki_file=wiki_file,
        page_title=page_title,
        task=task,
        sources=tuple(loaded),
        expected_points=points,
        samples=tuple(loaded_samples),
    )


def fixture_task_compliance() -> TaskCompliance:
    """Calibration samples are the requested page, by construction."""
    return TaskCompliance(
        passed=True,
        checks=("the fixture is at the requested path and shape",),
    )


__all__ = [
    "CalibrationSample",
    "CaseSource",
    "QualityCase",
    "fixture_task_compliance",
    "load_case",
]
