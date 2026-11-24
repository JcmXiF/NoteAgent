"""The fixture shape the page-writing and answer V4 quality cases share.

A case owns a task, its allowed material, its expected points, and the
calibration samples with their human labels. The loader checks the shapes once,
so a typo in a label file fails at load, not as a mystery in a judge verdict.
A page-writing case's material is raw originals; an answer case's material is
the wiki pages the answer may cite.
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

PAGE_KIND = "page"
ANSWER_KIND = "answer"


@dataclass(frozen=True, slots=True)
class CaseMaterial:
    """One allowed original: a raw capture or a wiki page the answer may cite."""

    id: str
    title: str
    text: str
    url: str = ""
    capture: Capture | None = None


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One artifact with the human labels it must be judged against."""

    name: str
    artifact: str
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class QualityCase:
    """A task, its allowed material, expected points and samples."""

    id: str
    kind: str
    task: str
    materials: tuple[CaseMaterial, ...]
    expected_points: tuple[str, ...]
    samples: tuple[CalibrationSample, ...]
    #: Existing wiki pages seeded for the agent and shown to the judge.
    wiki_materials: tuple[CaseMaterial, ...] = ()
    wiki_file: str = ""
    page_title: str = ""
    #: The pages the task asks the agent to read.
    required_reads: tuple[str, ...] = ()
    #: Tells the judge what the allowed set covers, for an absence claim.
    scope_note: str = ""

    @property
    def sample_names(self) -> tuple[str, ...]:
        return tuple(sample.name for sample in self.samples)

    @property
    def material_ids(self) -> tuple[str, ...]:
        return tuple(material.id for material in self.materials)

    @property
    def source_ids(self) -> tuple[str, ...]:
        """The raw originals a written page's ``sources`` must cite."""
        return tuple(
            material.id
            for material in self.materials
            if material.capture is not None
        )

    def judge_materials(self) -> tuple[JudgeMaterial, ...]:
        return tuple(
            JudgeMaterial(material.id, material.title, material.text)
            for material in (*self.materials, *self.wiki_materials)
        )


def load_page_case(
    *,
    case_id: str,
    directory: Path,
    wiki_file: str,
    page_title: str,
    samples: tuple[str, ...],
    sources: tuple[tuple[str, str, str], ...],
    wiki_pages: tuple[tuple[str, str, str], ...] = (),
    required_reads: tuple[str, ...] = (),
    scope_note: str = "",
) -> QualityCase:
    """Load a page-writing case: its task, raw originals, points and labels."""
    task, points, loaded_samples = _load_meta(
        case_id, directory, samples
    )
    materials: list[CaseMaterial] = []
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
        materials.append(
            CaseMaterial(
                id=raw_relative_for_capture(capture),
                title=title,
                text=capture.markdown.strip(),
                url=url,
                capture=capture,
            )
        )
    wiki_materials = _load_pages(directory, wiki_pages)
    _check_required_reads(case_id, required_reads, wiki_materials)
    return QualityCase(
        id=case_id,
        kind=PAGE_KIND,
        task=task,
        materials=tuple(materials),
        expected_points=points,
        samples=loaded_samples,
        wiki_materials=tuple(wiki_materials),
        wiki_file=wiki_file,
        page_title=page_title,
        required_reads=required_reads,
        scope_note=scope_note,
    )


def load_answer_case(
    *,
    case_id: str,
    directory: Path,
    samples: tuple[str, ...],
    pages: tuple[tuple[str, str, str], ...],
    required_reads: tuple[str, ...] = (),
    scope_note: str = "",
) -> QualityCase:
    """Load an answer case: its task, the wiki pages it may cite, and labels."""
    task, points, loaded_samples = _load_meta(
        case_id, directory, samples
    )
    materials = _load_pages(directory, pages)
    _check_required_reads(case_id, required_reads, materials)
    return QualityCase(
        id=case_id,
        kind=ANSWER_KIND,
        task=task,
        materials=tuple(materials),
        expected_points=points,
        samples=loaded_samples,
        required_reads=required_reads,
        scope_note=scope_note,
    )


def _load_pages(
    directory: Path,
    pages: tuple[tuple[str, str, str], ...],
) -> list[CaseMaterial]:
    materials: list[CaseMaterial] = []
    for slug, title, filename in pages:
        text = (
            directory / "pages" / filename
        ).read_text(encoding="utf-8")
        materials.append(
            CaseMaterial(id=slug, title=title, text=text.strip())
        )
    return materials


def _check_required_reads(
    case_id: str,
    required_reads: tuple[str, ...],
    materials: list[CaseMaterial],
) -> None:
    missing = [
        slug
        for slug in required_reads
        if slug not in {material.id for material in materials}
    ]
    if missing:
        raise ValueError(
            f"case {case_id}: required reads not in the material: "
            + ", ".join(missing)
        )


def _load_meta(
    case_id: str, directory: Path, samples: tuple[str, ...]
) -> tuple[str, tuple[str, ...], tuple[CalibrationSample, ...]]:
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
    loaded: list[CalibrationSample] = []
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
        loaded.append(
            CalibrationSample(
                name=str(name),
                artifact=artifact,
                labels={
                    str(point): str(verdict)
                    for point, verdict in row.items()
                },
            )
        )
    return task, points, tuple(loaded)


def fixture_task_compliance() -> TaskCompliance:
    """Calibration samples are the requested artifact, by construction."""
    return TaskCompliance(
        passed=True,
        checks=("the fixture is at the requested path and shape",),
    )


__all__ = [
    "ANSWER_KIND",
    "PAGE_KIND",
    "CalibrationSample",
    "CaseMaterial",
    "QualityCase",
    "fixture_task_compliance",
    "load_answer_case",
    "load_page_case",
]
