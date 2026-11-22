"""Agent skills: folders with ``SKILL.md``, discovered across three roots.

A skill is one folder holding ``SKILL.md`` (frontmatter ``name`` and
``description``, Markdown body), or one flat ``*.md`` file for the older
acquisition skills. Tier 1 reads name and description; tier 2 reads the body
on demand.

Roots are ordered from most to least specific: the project's
``.noteagent/skills``, the user's ``<config home>/skills``, and the skills
bundled with the package. A skill in a higher root shadows the same name in a
lower one. A skill that breaks the contract is refused on its own and reported
as a problem, so one bad file cannot hide the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.skills.types import Skill, SkillCatalogEntry

#: Bundled skills ship beside the prompt builders, as data under the package.
BUNDLED_ROOT = (
    Path(__file__).resolve().parent.parent / "prompts" / "skills"
)
#: One skill file may not exceed this; the index must stay cheap to read.
MAX_SKILL_BYTES = 256 * 1024
MAX_NAME_LENGTH = 64
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

PROJECT_ROOT = ".noteagent/skills"
CONFIG_SKILLS = "skills"


class SkillError(ValueError):
    """A skill folder or file does not match the skill contract."""


class SkillNotFoundError(KeyError):
    """No root holds a skill with the requested name."""


class SkillRootKind(StrEnum):
    """Which of the three roots a skill came from."""

    PROJECT = "project"
    USER = "user"
    BUNDLED = "bundled"


@dataclass(frozen=True, slots=True)
class SkillRoot:
    """One place skills are read from, and what kind of place it is."""

    kind: SkillRootKind
    folder: Path


@dataclass(frozen=True, slots=True)
class SkillProblem:
    """One skill the registry refused, and why."""

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class SkillRegistry:
    """The roots, most specific first; the first match for a name wins."""

    roots: tuple[SkillRoot, ...]

    def catalog(self) -> tuple[SkillCatalogEntry, ...]:
        """Tier 1 across the roots: one name and description per skill."""
        seen: dict[str, SkillCatalogEntry] = {}
        for root in self.roots:
            for entry in catalog_in(root.folder):
                seen.setdefault(entry.name, entry)
        return tuple(seen.values())

    def load(self, name: str) -> Skill:
        """Tier 2: the body of the skill *name*, from the first root."""
        needle = name.strip()
        if not needle:
            raise SkillNotFoundError("skill name is empty")
        for root in self.roots:
            found = skill_in(root.folder, needle)
            if found is not None:
                return found
        raise SkillNotFoundError(f"unknown skill: {needle}")

    def problems(self) -> tuple[SkillProblem, ...]:
        """Every skill refused across the roots, with its file and reason."""
        return tuple(
            problem
            for root in self.roots
            for problem in problems_in(root.folder)
        )


def default_roots(
    project_root: Path | None,
    *,
    config_home: Path | None = None,
    bundled: Path | None = None,
) -> tuple[SkillRoot, ...]:
    """The project, user and bundled roots, most specific first."""
    roots: list[SkillRoot] = []
    if project_root is not None:
        roots.append(
            SkillRoot(
                SkillRootKind.PROJECT,
                Path(project_root) / PROJECT_ROOT,
            )
        )
    home = Path(config_home) if config_home is not None else _config_home()
    roots.append(SkillRoot(SkillRootKind.USER, home / CONFIG_SKILLS))
    bundled_root = Path(bundled) if bundled is not None else BUNDLED_ROOT
    roots.append(SkillRoot(SkillRootKind.BUNDLED, bundled_root))
    return tuple(roots)


def catalog_in(folder: Path) -> tuple[SkillCatalogEntry, ...]:
    """Tier 1 for one root: name and description per valid skill."""
    found, _problems = _scan(folder)
    return tuple(
        SkillCatalogEntry(
            item.skill.name,
            item.skill.description,
            item.skill.path.name,
        )
        for item in found
    )


def skill_in(folder: Path, needle: str) -> Skill | None:
    """Tier 2 for one root: the skill *needle* names, or ``None``."""
    found, _problems = _scan(folder)
    for item in found:
        if item.skill.name == needle:
            return item.skill
        if not item.folder_skill and item.skill.path.stem == needle:
            return item.skill
    return None


def problems_in(folder: Path) -> tuple[SkillProblem, ...]:
    """Every skill refused in one root, with its file and reason."""
    _found, problems = _scan(folder)
    return problems


def _config_home() -> Path:
    """The per-user configuration folder, read lazily to avoid keyring."""
    from noteagent.settings.store import default_config_path

    return default_config_path().parent


@dataclass(frozen=True, slots=True)
class _Found:
    """One valid skill, and whether it came from a folder or a file."""

    skill: Skill
    folder_skill: bool


def _scan(
    folder: Path,
) -> tuple[tuple[_Found, ...], tuple[SkillProblem, ...]]:
    """Every valid skill in one root, and every refused one."""
    if not folder.is_dir():
        return (), ()
    skills: list[_Found] = []
    problems: list[SkillProblem] = []
    seen: dict[str, Path] = {}
    for path, folder_skill in _skill_files(folder):
        try:
            skill = _read(path, folder, folder_skill)
        except SkillError as err:
            problems.append(SkillProblem(path, str(err)))
            continue
        if skill.name in seen:
            problems.append(
                SkillProblem(
                    path,
                    f"duplicate skill {skill.name!r}: "
                    f"{seen[skill.name]} and {skill.path}",
                )
            )
            continue
        seen[skill.name] = skill.path
        skills.append(_Found(skill, folder_skill))
    return tuple(skills), tuple(problems)


def _skill_files(folder: Path) -> Iterable[tuple[Path, bool]]:
    """The skill files under *folder*: ``*/SKILL.md`` and flat ``*.md``."""
    for path in sorted(folder.iterdir()):
        if path.name.startswith("."):
            continue
        if _is_link(path):
            # Yield it so _read refuses it with the skill's own error.
            yield path, False
        elif path.is_dir():
            manifest = path / "SKILL.md"
            if manifest.is_file():
                yield manifest, True
        elif path.is_file() and path.suffix.lower() == ".md":
            yield path, False


def _read(path: Path, root: Path, folder_skill: bool) -> Skill:
    """Read and validate one skill file under *root*."""
    _refuse_unsafe(path, root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise SkillError(f"{path}: cannot be read: {err}") from err
    parsed = parse_page(text)
    fallback = path.parent.name if folder_skill else path.stem
    name = unquote_scalar(parsed.fields.get("name", "")).strip() or fallback
    _validate_name(name, path)
    return Skill(
        name=name,
        description=unquote_scalar(
            parsed.fields.get("description", "")
        ).strip(),
        body=parsed.body.strip(),
        path=path,
    )


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows the junction that stands in for one."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _refuse_unsafe(path: Path, root: Path) -> None:
    """Refuse a link, a path outside *root*, or an oversized file."""
    current = path
    while current != root:
        if _is_link(current):
            raise SkillError(f"{path}: a skill must not be a symlink")
        current = current.parent
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise SkillError(
            f"{path}: a skill must stay inside {root}"
        ) from None
    size = path.stat().st_size
    if size > MAX_SKILL_BYTES:
        raise SkillError(
            f"{path}: {size} bytes exceeds the {MAX_SKILL_BYTES}-byte limit"
        )


def _validate_name(name: str, path: Path) -> None:
    """Refuse a name the session could not address unambiguously."""
    if len(name) > MAX_NAME_LENGTH or _NAME.fullmatch(name) is None:
        raise SkillError(
            f"{path}: invalid skill name {name!r}; use lowercase letters, "
            "digits and single hyphens, at most "
            f"{MAX_NAME_LENGTH} characters"
        )
