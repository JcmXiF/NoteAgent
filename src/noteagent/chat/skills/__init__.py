"""Load Chat skills, falling back to the acquire skills of the same name.

A chat skill is a chat-local ``*.md``; the acquire skills are read through the
loader that owns them, so the two tiers cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

from noteagent.acquire.skills import (
    SkillNotFoundError,
    catalog_in,
    list_skills as list_acquire_skills,
    load_skill as load_acquire_skill,
    skill_in,
)
from noteagent.acquire.types import Skill, SkillCatalogEntry

_CHAT_SKILLS_DIR = Path(__file__).resolve().parent


def skills_dir() -> Path:
    """The folder chat-only skills are bundled in."""
    return _CHAT_SKILLS_DIR


def list_skills() -> tuple[SkillCatalogEntry, ...]:
    """Chat-only skills first, then the acquire skills not named here already."""
    local = catalog_in(_CHAT_SKILLS_DIR)
    seen = {item.name for item in local}
    merged = list(local)
    for item in list_acquire_skills():
        if item.name in seen:
            continue
        merged.append(item)
    return tuple(merged)


def load_skill(name: str) -> Skill:
    """One chat skill by name, or the acquire skill that carries it."""
    needle = name.strip()
    if not needle:
        raise SkillNotFoundError("skill name is empty")
    found = skill_in(_CHAT_SKILLS_DIR, needle)
    if found is not None:
        return found
    return load_acquire_skill(needle)
