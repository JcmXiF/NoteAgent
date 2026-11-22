"""Load acquire skills with progressive disclosure.

The catalog reads frontmatter only; a skill's body is loaded on demand. The
parsing and validation live in :mod:`noteagent.skills`, so the acquisition
plan and the agent read one skill format. This module keeps the acquire API
and its own bundled folder.
"""

from __future__ import annotations

from pathlib import Path

from noteagent.skills import (
    SkillNotFoundError,
    catalog_in,
    skill_in,
)
from noteagent.skills.types import Skill, SkillCatalogEntry

_SKILLS_DIR = Path(__file__).resolve().parent


def skills_dir() -> Path:
    """The folder acquire skills are bundled in."""
    return _SKILLS_DIR


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
