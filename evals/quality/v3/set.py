"""The frozen V3 quality set: 24 single-turn and 2 multi-turn items.

The set is a fixed yardstick: ``manifest.yaml`` pins the content hashes, and the
loader refuses a question, case or rubric that changed under the same version.
The shared rubric decides pass/fail from content only; tool and trace
assertions are diagnostics, never part of the verdict.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evals.golden.dataset import (
    DEFAULT_GOLDEN,
    ExpectedTrace,
    GoldenError,
    GoldenQuery,
    GoldenSet,
    load_golden,
)
from evals.quality.cases import AssertionSpec, CaseSpec, load_cases

SET_DIR = Path(__file__).resolve().parent
RUBRIC_PATH = SET_DIR / "rubric.yaml"
MANIFEST_PATH = SET_DIR / "manifest.yaml"
CASES_ROOT = SET_DIR.parent / "cases"

ESSENTIAL = "essential"
IMPORTANT = "important"
VETO = "veto"
_REQUIRED_WEIGHTS = (ESSENTIAL, IMPORTANT)
_EXPECTED_DIMENSIONS = {
    "grounding": ESSENTIAL,
    "completeness": ESSENTIAL,
    "uncertainty": IMPORTANT,
}
_VETO_NAME = "hallucination"


class QualitySetError(ValueError):
    """The frozen quality set cannot be loaded or changed under its version."""


@dataclass(frozen=True, slots=True)
class RubricDimension:
    name: str
    weight: str
    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Rubric:
    version: str
    dimensions: tuple[RubricDimension, ...]
    veto: RubricDimension

    def passes(self, verdicts: Mapping[str, str]) -> bool:
        """Every dimension passes and the hallucination veto passes."""
        for dimension in self.dimensions:
            if verdicts.get(dimension.name) != "pass":
                return False
        return verdicts.get(self.veto.name) == "pass"


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """Which assertions are reported but never decide the content verdict."""

    tools: bool
    trace: bool


@dataclass(frozen=True, slots=True)
class QualitySet:
    """The frozen set: the shared rubric is the verdict, the rest is data."""

    version: str
    golden: GoldenSet
    cases: tuple[CaseSpec, ...]
    rubric: Rubric
    diagnostics: Diagnostics
    content_hash: str

    @property
    def single_turn(self) -> tuple[GoldenQuery, ...]:
        return self.golden.queries

    @property
    def multi_turn(self) -> tuple[CaseSpec, ...]:
        return self.cases

    def expected_points(self, case: CaseSpec) -> tuple[str, ...]:
        """The question's expected points; the shared rubric judges them."""
        return case.judge.completeness.must

    def pitfalls(self, case: CaseSpec) -> tuple[str, ...]:
        """Question-specific traps; the shared veto remains global."""
        return case.judge.completeness.must_not

    def diagnostic_assertions(self, case: CaseSpec) -> AssertionSpec:
        """Tool assertions are diagnostics, never part of the verdict."""
        return case.assertions

    def diagnostic_trace(self, query: GoldenQuery) -> ExpectedTrace:
        """The expected trace is a diagnostic, never part of the verdict."""
        return query.expected_trace


def load_rubric(path: Path = RUBRIC_PATH) -> Rubric:
    """Load and validate the shared rubric; a bad weight is refused."""
    path = Path(path)
    raw = _yaml(path)
    if not isinstance(raw, Mapping):
        raise QualitySetError(f"{path}: the rubric must be a mapping")
    version = _text(raw.get("version"), f"{path}: version")
    dimensions = _dimensions(raw.get("dimensions"), path)
    veto = _veto(raw.get("veto"), path)
    names = [item.name for item in dimensions] + [veto.name]
    if len(set(names)) != len(names):
        raise QualitySetError(f"{path}: dimension names must be unique")
    actual = {item.name: item.weight for item in dimensions}
    if actual != _EXPECTED_DIMENSIONS:
        raise QualitySetError(
            f"{path}: the shared rubric must define grounding/completeness "
            "as essential and uncertainty as important"
        )
    if veto.name != _VETO_NAME:
        raise QualitySetError(
            f"{path}: the veto must be {_VETO_NAME!r}"
        )
    return Rubric(version, dimensions, veto)


def load_quality_set(
    manifest_path: Path = MANIFEST_PATH,
    *,
    golden_root: Path | None = None,
    cases_root: Path | None = None,
    rubric_path: Path | None = None,
    check_hashes: bool = True,
    require_counts: bool = True,
) -> QualitySet:
    """Load the frozen set; a changed hash is refused unless the flags say not."""
    manifest_path = Path(manifest_path)
    manifest = _yaml(manifest_path)
    if not isinstance(manifest, Mapping):
        raise QualitySetError(
            f"{manifest_path}: the manifest must be a mapping"
        )
    version = _text(manifest.get("version"), f"{manifest_path}: version")
    golden_conf = _mapping(
        manifest.get("golden"), f"{manifest_path}: golden"
    )
    root = (
        Path(golden_root)
        if golden_root is not None
        else Path(str(golden_conf.get("root") or DEFAULT_GOLDEN))
    )
    try:
        golden = load_golden(
            root,
            check_hashes=check_hashes,
            require_counts=require_counts,
        )
    except GoldenError as err:
        raise QualitySetError(f"the golden content changed: {err}") from err
    cases_dir = Path(cases_root) if cases_root is not None else CASES_ROOT
    cases = load_cases(cases_dir)
    for case in cases:
        if not case.judge.completeness.must:
            raise QualitySetError(
                f"{case.name}: the case has no completeness expected points"
            )
    rubric_file = (
        Path(rubric_path)
        if rubric_path is not None
        else manifest_path.parent / "rubric.yaml"
    )
    rubric = load_rubric(rubric_file)
    diagnostics = _diagnostics(
        manifest.get("diagnostics"), manifest_path
    )
    case_hashes = {
        case.name: _case_hash(cases_dir / case.name) for case in cases
    }
    rubric_hash = _sha(rubric_file.read_bytes())
    content_hash = _content_hash(
        version,
        golden.queries_sha256,
        golden.corpus_sha256,
        case_hashes,
        rubric_hash,
    )
    if check_hashes:
        _require_hash(
            manifest_path,
            "golden.queries_sha256",
            golden_conf.get("queries_sha256"),
            golden.queries_sha256,
        )
        _require_hash(
            manifest_path,
            "golden.corpus_sha256",
            golden_conf.get("corpus_sha256"),
            golden.corpus_sha256,
        )
        manifest_cases = _mapping(
            manifest.get("cases"), f"{manifest_path}: cases"
        )
        if set(manifest_cases) != set(case_hashes):
            raise QualitySetError(
                f"{manifest_path}: the cases no longer match the manifest; "
                "bump the version and regenerate it"
            )
        for name, digest in case_hashes.items():
            _require_hash(
                manifest_path,
                f"cases.{name}",
                manifest_cases.get(name),
                digest,
            )
        _require_hash(
            manifest_path, "rubric", manifest.get("rubric"), rubric_hash
        )
        _require_hash(
            manifest_path,
            "content_hash",
            manifest.get("content_hash"),
            content_hash,
        )
    return QualitySet(
        version=version,
        golden=golden,
        cases=cases,
        rubric=rubric,
        diagnostics=diagnostics,
        content_hash=content_hash,
    )


def write_manifest(
    manifest_path: Path = MANIFEST_PATH,
    *,
    version: str,
    golden_root: Path = DEFAULT_GOLDEN,
    cases_root: Path = CASES_ROOT,
    rubric_path: Path = RUBRIC_PATH,
    require_counts: bool = True,
) -> Path:
    """Write the frozen set's hashes after an explicit version bump."""
    golden = load_golden(
        golden_root,
        check_hashes=False,
        require_counts=require_counts,
    )
    cases = load_cases(cases_root)
    load_rubric(rubric_path)
    case_hashes = {
        case.name: _case_hash(Path(cases_root) / case.name) for case in cases
    }
    rubric_hash = _sha(Path(rubric_path).read_bytes())
    content_hash = _content_hash(
        version,
        golden.queries_sha256,
        golden.corpus_sha256,
        case_hashes,
        rubric_hash,
    )
    payload = {
        "version": version,
        "golden": {
            "root": Path(golden_root).as_posix(),
            "queries_sha256": golden.queries_sha256,
            "corpus_sha256": golden.corpus_sha256,
        },
        "cases": dict(sorted(case_hashes.items())),
        "rubric": rubric_hash,
        "diagnostics": {"tools": True, "trace": True},
        "content_hash": content_hash,
    }
    path = Path(manifest_path)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _dimensions(
    value: Any, path: Path
) -> tuple[RubricDimension, ...]:
    if not isinstance(value, list) or not value:
        raise QualitySetError(
            f"{path}: dimensions must be a non-empty list"
        )
    dimensions: list[RubricDimension] = []
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, Mapping):
            raise QualitySetError(
                f"{path}: dimension {index} must be a mapping"
            )
        name = _text(raw.get("name"), f"{path}: dimension {index} name")
        weight = _text(raw.get("weight"), f"{path}: {name} weight")
        if weight not in _REQUIRED_WEIGHTS:
            raise QualitySetError(
                f"{path}: {name} weight must be essential or important"
            )
        dimensions.append(
            RubricDimension(
                name,
                weight,
                _strings(raw.get("must"), f"{path}: {name} must"),
                _strings(
                    raw.get("must_not"), f"{path}: {name} must_not"
                ),
            )
        )
    return tuple(dimensions)


def _veto(value: Any, path: Path) -> RubricDimension:
    if not isinstance(value, Mapping):
        raise QualitySetError(f"{path}: veto must be a mapping")
    name = _text(value.get("name"), f"{path}: veto name")
    return RubricDimension(
        name,
        VETO,
        _strings(value.get("must"), f"{path}: veto must"),
        _strings(value.get("must_not"), f"{path}: veto must_not"),
    )


def _diagnostics(value: Any, path: Path) -> Diagnostics:
    if not isinstance(value, Mapping):
        raise QualitySetError(f"{path}: diagnostics must be a mapping")
    tools = value.get("tools")
    trace = value.get("trace")
    if not isinstance(tools, bool) or not isinstance(trace, bool):
        raise QualitySetError(
            f"{path}: diagnostics.tools and diagnostics.trace must be true"
        )
    if not tools or not trace:
        raise QualitySetError(
            f"{path}: tools and trace must stay diagnostics, not verdicts"
        )
    return Diagnostics(tools=tools, trace=trace)


def _yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise QualitySetError(f"{path}: invalid YAML: {err}") from err
    except OSError as err:
        raise QualitySetError(f"{path}: cannot be read: {err}") from err


def _mapping(value: Any, what: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise QualitySetError(f"{what} must be a mapping")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualitySetError(f"{what} must be a non-empty string")
    return value.strip()


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise QualitySetError(f"{what} must be a non-empty list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise QualitySetError(
                f"{what} must hold non-empty strings"
            )
        items.append(item.strip())
    return tuple(items)


def _require_hash(
    path: Path, label: str, expected: Any, actual: str
) -> None:
    if str(expected or "") != actual:
        raise QualitySetError(
            f"{path}: {label} changed; bump the version and regenerate "
            "the manifest"
        )


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*")):
        if not file.is_file():
            continue
        digest.update(file.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _content_hash(
    version: str,
    queries_sha256: str,
    corpus_sha256: str,
    case_hashes: Mapping[str, str],
    rubric_sha256: str,
) -> str:
    body = json.dumps(
        {
            "version": version,
            "golden": {
                "queries": queries_sha256,
                "corpus": corpus_sha256,
            },
            "cases": dict(sorted(case_hashes.items())),
            "rubric": rubric_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return _sha(body.encode("utf-8"))


__all__ = [
    "CASES_ROOT",
    "Diagnostics",
    "ESSENTIAL",
    "IMPORTANT",
    "MANIFEST_PATH",
    "QualitySet",
    "QualitySetError",
    "RUBRIC_PATH",
    "Rubric",
    "RubricDimension",
    "VETO",
    "load_quality_set",
    "load_rubric",
    "write_manifest",
]
