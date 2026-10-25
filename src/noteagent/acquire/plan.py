"""Build the offline search plan for acquire step 1 (what/how to search)."""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.acquire.search_config import SearchConfig
from noteagent.acquire.skills import list_skills, load_skill
from noteagent.acquire.tools import tool_catalog
from noteagent.acquire.types import SearchBudget, Skill, SkillCatalogEntry, ToolSpec


@dataclass(frozen=True, slots=True)
class SearchPlan:
    topic: str
    intent: str | None
    first_query: str
    budget: SearchBudget
    tools: tuple[ToolSpec, ...]
    skills: tuple[SkillCatalogEntry, ...]
    loaded_skills: tuple[Skill, ...]


def open_search_plan(
    topic: str,
    *,
    intent: str | None = None,
    load_skill_names: tuple[str, ...] = (),
    search_config: SearchConfig | None = None,
) -> SearchPlan:
    """
    Open a search session without touching the network.

    The first query is the topic itself — same as typing the keyword
    into a search box.
    """
    cleaned = " ".join(topic.split())
    if not cleaned:
        raise ValueError("topic must not be empty")

    cleaned_intent = " ".join(intent.split()) if intent and intent.strip() else None
    loaded: list[Skill] = []
    for name in load_skill_names:
        loaded.append(load_skill(name))

    return SearchPlan(
        topic=cleaned,
        intent=cleaned_intent,
        first_query=cleaned,
        budget=SearchBudget(),
        tools=tool_catalog(search_config=search_config),
        skills=list_skills(),
        loaded_skills=tuple(loaded),
    )


def format_search_plan(plan: SearchPlan) -> str:
    """Human-readable plan for CLI / dry-run."""
    lines = [
        f"topic: {plan.topic}",
        f"intent: {plan.intent or '(none)'}",
        f"first_query: {plan.first_query}",
        (
            "budget: "
            f"web_search≤{plan.budget.max_web_searches} "
            f"wikipedia_lookup≤{plan.budget.max_wikipedia_lookups} "
            f"candidates≤{plan.budget.max_candidates} "
            f"fetches≤{plan.budget.max_fetches}"
        ),
        "",
        "tools:",
    ]
    for spec in plan.tools:
        state = "configured" if spec.configured else "not configured"
        lines.append(f"  - {spec.name} [{state}]")
        lines.append(f"    {spec.description}")

    lines.append("")
    lines.append("skills (catalog only):")
    for entry in plan.skills:
        lines.append(f"  - {entry.name}: {entry.description}")

    if plan.loaded_skills:
        lines.append("")
        for skill in plan.loaded_skills:
            lines.append(f"--- skill: {skill.name} ---")
            lines.append(skill.body)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "SearchPlan",
    "format_search_plan",
    "open_search_plan",
]
