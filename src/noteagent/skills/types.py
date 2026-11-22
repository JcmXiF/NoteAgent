"""The two views of a skill: the tier-1 catalog row and the tier-2 body."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Tier-1 skill disclosure: name + when-to-use only."""

    name: str
    description: str
    filename: str


@dataclass(frozen=True, slots=True)
class Skill:
    """Tier-2 skill disclosure: full SKILL.md body."""

    name: str
    description: str
    body: str
    path: Path
