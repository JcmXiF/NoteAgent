"""Load acquire skills with progressive disclosure.

The catalog reads frontmatter only; a skill's body is loaded on demand. Both
tiers read the same folder, so a skill is one ``*.md`` file whose frontmatter
names it and whose body is the instruction.
"""

from __future__ import annotations

from pathlib import Path

from noteagent.acquire.types import Skill, SkillCatalogEntry
from noteagent.ingest.frontmatter import parse_page, unquote_scalar

_SKILLS_DIR = Path(__file__).resolve().parent


class SkillNotFoundError(KeyError):
    """Requested skill name is not in the bundled catalog."""


def skills_dir() -> Path:
    """The folder acquire skills are bundled in."""
    return _SKILLS_DIR


def _name_of(path: Path, fields: dict[str, str]) -> str:
    """The skill's name: its frontmatter's, or the file's own stem."""
    return unquote_scalar(fields.get("name", "")).strip() or path.stem


def _description_of(fields: dict[str, str]) -> str:
    """The skill's one-line when-to-use text."""
    return unquote_scalar(fields.get("description", "")).strip()


def catalog_in(folder: Path) -> tuple[SkillCatalogEntry, ...]:
    """Tier 1 for one folder: name and description per file, no bodies."""
    if not folder.is_dir():
        return ()
    entries: list[SkillCatalogEntry] = []
    for path in sorted(folder.glob("*.md")):
        parsed = parse_page(path.read_text(encoding="utf-8"))
        entries.append(
            SkillCatalogEntry(
                name=_name_of(path, parsed.fields),
                description=_description_of(parsed.fields),
                filename=path.name,
            )
        )
    return tuple(entries)


def skill_in(folder: Path, needle: str) -> Skill | None:
    """Tier 2 for one folder: the skill *needle* names, or ``None``."""
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.md")):
        parsed = parse_page(path.read_text(encoding="utf-8"))
        name = _name_of(path, parsed.fields)
        if name != needle and path.stem != needle:
            continue
        return Skill(
            name=name,
            description=_description_of(parsed.fields),
            body=parsed.body.strip(),
            path=path,
        )
    return None


def list_skills() -> tuple[SkillCatalogEntry, ...]:
    """Tier 1: name + description for every bundled skill, no bodies."""
    return catalog_in(_SKILLS_DIR)


def load_skill(name: str) -> Skill:
    """Tier 2: full instructions for one skill."""
    needle = name.strip()
    if not needle:
        raise SkillNotFoundError("skill name is empty")
    found = skill_in(_SKILLS_DIR, needle)
    if found is None:
        raise SkillNotFoundError(f"unknown acquire skill: {needle}")
    return found
