"""The system sections every product session starts with.

The agent prompt is a projection of its log: a session with no
``system_section`` entry has no role, no tool policy, and no grounding rule.
These defaults give a new session that baseline. The eval harness disables
them because its cases bring their own sections.
"""

from __future__ import annotations

from noteagent.agent.projection import project as read_projection
from noteagent.agent.store import SessionStore
from noteagent.project.layout import ProjectLayout

DEFAULT_IDENTITY = (
    "You are NoteAgent, a notes assistant working on one project wiki. "
    "Answer the reader's question from that wiki: give the definitions, "
    "formulas and relationships, not a pointer to them. Write in the "
    "language of the question."
)
DEFAULT_TOOLS = (
    "Available tools:\n"
    "- `search(query)`: find wiki pages by slug, title, or body text; "
    "results are locators, not quotes.\n"
    "- `wiki_read(slug)`: open one wiki page by slug; read a page before "
    "quoting or summarizing it.\n"
    "Rules: you cannot write or edit pages. Answer questions that ask you "
    "to explain, compare, analyze, or state what is missing directly in "
    "prose. If the wiki lacks a page, say exactly what is missing. When the "
    "reader asks you to write, use the writing tools offered to you; the "
    "writing state is where the work is handed over. Never claim a job ran "
    "or a page was written."
)
DEFAULT_GROUNDING = (
    "Prefer the wiki. You may add general knowledge only when it is "
    "necessary, and only as a clearly marked aside such as "
    "`（wiki 未覆盖，以下为一般知识：…）`. Never present outside knowledge as "
    "if a wiki page said it. When the wiki does not contain what the question "
    "asks, say what is missing rather than inventing it. Never invent "
    "formulas, definitions, pages or sources."
)
_PROJECT_LIMIT = 4_000


def project_text(layout: ProjectLayout) -> str:
    """The project's own brief, or an old project's purpose."""
    for path in (layout.brief, layout.legacy_purpose):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text[:_PROJECT_LIMIT]
    return ""


def default_sections(layout: ProjectLayout) -> dict[str, str]:
    """The role, tools and grounding a product session starts with."""
    sections = {
        "identity": DEFAULT_IDENTITY,
        "tools": DEFAULT_TOOLS,
    }
    project = project_text(layout)
    if project:
        sections["project"] = project
    sections["grounding"] = DEFAULT_GROUNDING
    return sections


def ensure_default_sections(
    store: SessionStore,
    session_id: str,
    layout: ProjectLayout | None,
) -> bool:
    """Add the sections an old or new session is missing; keep custom ones.

    A session that already carries a section keeps it: the eval runner writes
    its own identity/tools/project/grounding and must not be overwritten.
    """
    if layout is None:
        return False
    snapshot = store.load(session_id)
    current = read_projection(snapshot.entries).sections
    desired = default_sections(layout)
    patch = {
        key: value
        for key, value in desired.items()
        if not str(current.get(key) or "").strip()
    }
    if not patch:
        return False
    store.append(
        session_id,
        {"type": "system_section", "sections": patch},
    )
    return True


__all__ = [
    "DEFAULT_GROUNDING",
    "DEFAULT_IDENTITY",
    "DEFAULT_TOOLS",
    "default_sections",
    "ensure_default_sections",
    "project_text",
]
