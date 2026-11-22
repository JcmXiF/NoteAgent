"""The system sections every product session starts with.

The agent prompt is a projection of its log: a session with no
``system_section`` entry has no role, no tool policy, and no grounding rule.
These defaults give a new session that baseline, plus the project's brief when
it has one and the skill index when any skill exists. The eval harness disables
them because its cases bring their own sections.
"""

from __future__ import annotations

from noteagent.agent.projection import project as read_projection
from noteagent.agent.store import SessionStore
from noteagent.project.layout import ProjectLayout
from noteagent.skills import default_registry
from noteagent.skills.index import render_index

SKILLS_SECTION = "skills"

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
    "- `get_state(...)`: read the writing state: search hits, selected "
    "pages and the draft.\n"
    "- `web_search(query)`, `select_page({hitId})`, "
    "`select_frame({pageId, blockIds})`: gather material into the state.\n"
    "- `compose_wiki({wikiFile, baseDraftVersion, sections})`: compose or "
    "revise a draft page.\n"
    "- `write_wiki({wikiFile, draftVersion})`: publish the draft into the "
    "wiki.\n"
    "- `skill(name)` / `skill_read_file(name, path)`: load a skill's "
    "instructions and a file it references.\n"
    "Rules: answer questions that ask you to explain, compare, analyze, or "
    "state what is missing directly in prose. When the reader asks you to "
    "write, update, plan or compile a page, load the `writing` skill and "
    "follow its recipe. Never claim work you did not do."
)
DEFAULT_GROUNDING = (
    "Answer only from this project's wiki. Do not add general knowledge, "
    "even when you mark it as an aside. When the wiki does not contain what "
    "the question asks, say exactly what is missing. Never invent formulas, "
    "definitions, pages or sources."
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
    """The role, tools, project, grounding and skill index to start with.

    The project section appears when the wiki has a brief; the skills section
    appears when a skill is discoverable.
    """
    sections = {
        "identity": DEFAULT_IDENTITY,
        "tools": DEFAULT_TOOLS,
    }
    project = project_text(layout)
    if project:
        sections["project"] = project
    sections["grounding"] = DEFAULT_GROUNDING
    index = render_index(default_registry(layout.root))
    if index:
        sections[SKILLS_SECTION] = index
    return sections


def ensure_default_sections(
    store: SessionStore,
    session_id: str,
    layout: ProjectLayout | None,
) -> bool:
    """Add the sections an old or new session is missing; keep custom ones.

    A session that already carries a section keeps it: the eval runner writes
    its own identity/tools/project/grounding/skills and must not be
    overwritten.
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
