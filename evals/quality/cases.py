"""Load a quality case from its directory.

A case is a directory with ``case.yaml`` and one or more Markdown pages under
``wiki/``. The YAML is the case's contract: turns, machine-checked assertions,
and the per-dimension rules the judge reads. Loading validates the whole file,
so a typo in an assertion key fails before a provider is called.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from noteagent.ingest.frontmatter import parse_page, unquote_scalar

JUDGE_DIMENSIONS = ("grounding", "completeness", "uncertainty")
_ASSERTION_KEYS = (
    "tools_any",
    "tools_all",
    "tools_none",
    "max_tool_rounds",
    "no_error",
)
_CASE_KEYS = ("name", "title", "turns", "assertions", "judge")


class CaseError(ValueError):
    """A case directory does not match the dataset's contract."""


@dataclass(frozen=True, slots=True)
class WikiPage:
    """One page seeded into the case's temporary project."""

    slug: str
    title: str
    body: str
    path: Path


@dataclass(frozen=True, slots=True)
class AssertionSpec:
    """The machine-checked part of a case; ``None`` means the key is absent."""

    tools_any: tuple[str, ...] | None = None
    tools_all: tuple[str, ...] | None = None
    tools_none: tuple[str, ...] | None = None
    max_tool_rounds: int | None = None
    no_error: bool | None = None


@dataclass(frozen=True, slots=True)
class JudgeRule:
    """What the judge must find, and must not find, in one dimension."""

    must: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JudgeSpec:
    """The three text-quality dimensions the judge grades."""

    grounding: JudgeRule
    completeness: JudgeRule
    uncertainty: JudgeRule


@dataclass(frozen=True, slots=True)
class CaseSpec:
    """One validated quality case."""

    name: str
    title: str
    turns: tuple[str, ...]
    assertions: AssertionSpec
    judge: JudgeSpec
    wiki: tuple[WikiPage, ...]


def load_case(path: Path) -> CaseSpec:
    """Load and validate one case directory."""
    path = Path(path)
    manifest = path / "case.yaml"
    if not manifest.is_file():
        raise CaseError(f"{path}: case.yaml is missing")
    raw = _load_yaml(manifest)
    if not isinstance(raw, Mapping):
        raise CaseError(f"{manifest}: the case must be a mapping")
    _reject_unknown(raw, _CASE_KEYS, manifest, "key")

    name = _text(raw.get("name"), "name", manifest)
    if name != path.name:
        raise CaseError(
            f"{manifest}: name {name!r} does not match the directory "
            f"{path.name!r}"
        )
    title = _text(raw.get("title", name), "title", manifest)
    turns = _turns(raw.get("turns"), manifest)
    assertions = _assertions(raw.get("assertions"), manifest)
    judge = _judge(raw.get("judge"), manifest)
    wiki = _wiki(path / "wiki")
    return CaseSpec(name, title, turns, assertions, judge, wiki)


def load_cases(root: Path) -> tuple[CaseSpec, ...]:
    """Load every case under *root*, in directory order."""
    root = Path(root)
    if not root.is_dir():
        raise CaseError(f"{root}: the cases directory is missing")
    cases = [
        load_case(child)
        for child in sorted(root.iterdir())
        if child.is_dir() and (child / "case.yaml").is_file()
    ]
    if not cases:
        raise CaseError(f"{root}: no case directory with a case.yaml")
    return tuple(cases)


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise CaseError(f"{path}: invalid YAML: {err}") from err
    except OSError as err:
        raise CaseError(f"{path}: cannot be read: {err}") from err


def _reject_unknown(
    raw: Mapping[Any, Any],
    allowed: tuple[str, ...],
    path: Path,
    what: str,
    *,
    where: str = "",
) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        prefix = f"{where} " if where else ""
        raise CaseError(
            f"{path}: {prefix}has unknown {what}(s): {', '.join(unknown)}"
        )


def _text(value: Any, key: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaseError(f"{path}: {key} must be a non-empty string")
    return value.strip()


def _turns(value: Any, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CaseError(f"{path}: turns must be a non-empty list")
    turns: list[str] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, str) or not item.strip():
            raise CaseError(
                f"{path}: turns[{index}] must be a non-empty string"
            )
        turns.append(item)
    return tuple(turns)


def _names(value: Any, key: str, path: Path) -> tuple[str, ...]:
    names = _strings(value, key, path)
    if not names:
        raise CaseError(f"{path}: assertions.{key} must be a non-empty list")
    return names


def _strings(value: Any, key: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CaseError(f"{path}: {key} must be a list")
    out: list[str] = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, str) or not item.strip():
            raise CaseError(
                f"{path}: {key}[{index}] must be a non-empty string"
            )
        out.append(item.strip())
    return tuple(out)


def _assertions(value: Any, path: Path) -> AssertionSpec:
    if not isinstance(value, Mapping) or not value:
        raise CaseError(f"{path}: assertions must be a non-empty mapping")
    _reject_unknown(value, _ASSERTION_KEYS, path, "assertion")

    rounds: int | None = None
    if "max_tool_rounds" in value:
        candidate = value["max_tool_rounds"]
        if isinstance(candidate, bool) or not isinstance(candidate, int):
            raise CaseError(
                f"{path}: assertions.max_tool_rounds must be a whole number"
            )
        if candidate < 0:
            raise CaseError(
                f"{path}: assertions.max_tool_rounds must not be negative"
            )
        rounds = candidate

    no_error: bool | None = None
    if "no_error" in value:
        candidate = value["no_error"]
        if not isinstance(candidate, bool):
            raise CaseError(f"{path}: assertions.no_error must be true or false")
        no_error = candidate

    return AssertionSpec(
        tools_any=(
            _names(value["tools_any"], "tools_any", path)
            if "tools_any" in value
            else None
        ),
        tools_all=(
            _names(value["tools_all"], "tools_all", path)
            if "tools_all" in value
            else None
        ),
        tools_none=(
            _names(value["tools_none"], "tools_none", path)
            if "tools_none" in value
            else None
        ),
        max_tool_rounds=rounds,
        no_error=no_error,
    )


def _judge(value: Any, path: Path) -> JudgeSpec:
    if not isinstance(value, Mapping):
        raise CaseError(f"{path}: judge must be a mapping")
    _reject_unknown(value, JUDGE_DIMENSIONS, path, "judge dimension")
    missing = [name for name in JUDGE_DIMENSIONS if name not in value]
    if missing:
        raise CaseError(
            f"{path}: judge is missing dimension(s): {', '.join(missing)}"
        )
    return JudgeSpec(
        grounding=_rule(value["grounding"], "grounding", path),
        completeness=_rule(value["completeness"], "completeness", path),
        uncertainty=_rule(value["uncertainty"], "uncertainty", path),
    )


def _rule(value: Any, dimension: str, path: Path) -> JudgeRule:
    if not isinstance(value, Mapping):
        raise CaseError(f"{path}: judge.{dimension} must be a mapping")
    _reject_unknown(
        value,
        ("must", "must_not"),
        path,
        "key",
        where=f"judge.{dimension}",
    )
    return JudgeRule(
        must=_strings(value.get("must", []), f"judge.{dimension}.must", path),
        must_not=_strings(
            value.get("must_not", []), f"judge.{dimension}.must_not", path
        ),
    )


def _wiki(directory: Path) -> tuple[WikiPage, ...]:
    if not directory.is_dir():
        raise CaseError(f"{directory}: the wiki directory is missing")
    pages: list[WikiPage] = []
    for path in sorted(directory.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            raise CaseError(f"{path}: cannot be read: {err}") from err
        parsed = parse_page(text)
        title = unquote_scalar(parsed.fields.get("title", "")).strip()
        slug = path.relative_to(directory).with_suffix("").as_posix()
        pages.append(
            WikiPage(
                slug=slug,
                title=title or slug.rsplit("/", 1)[-1],
                body=parsed.body.strip(),
                path=path,
            )
        )
    if not pages:
        raise CaseError(f"{directory}: at least one Markdown page is required")
    return tuple(pages)


__all__ = [
    "AssertionSpec",
    "CaseError",
    "CaseSpec",
    "JUDGE_DIMENSIONS",
    "JudgeRule",
    "JudgeSpec",
    "WikiPage",
    "load_case",
    "load_cases",
]
