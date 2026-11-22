"""The product prompt bundle: its sections, versions and migration.

The agent prompt is a projection of its log: a session with no
``system_section`` entry has no role, no tool policy, and no grounding rule.
These defaults give a new session that baseline, plus the project's brief when
it has one and the skill index when any skill exists. A session records the
``PROMPT_VERSION`` it read; an older one has its product sections replaced on
the next query, while its custom sections stay. The eval harness disables the
defaults because its cases bring their own sections.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from noteagent.agent.projection import (
    context_start,
    project as read_projection,
)
from noteagent.agent.store import SessionStore
from noteagent.project.layout import ProjectLayout
from noteagent.skills import SkillRegistry, default_registry
from noteagent.skills.index import render_index

SKILLS_SECTION = "skills"
#: Bump this whenever a DEFAULT_* text or the product section set changes, so
#: the next query replaces what older sessions hold.
PROMPT_VERSION = 1
#: The sections the product owns, and may therefore replace on migration.
PRODUCT_SECTIONS = (
    "identity",
    "tools",
    "project",
    "grounding",
    SKILLS_SECTION,
)

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


def default_sections(
    layout: ProjectLayout, *, registry: SkillRegistry | None = None
) -> dict[str, str]:
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
    active = registry or default_registry(layout.root)
    index = render_index(active)
    if index:
        sections[SKILLS_SECTION] = index
    return sections


def prompt_state(
    entries: Sequence[Mapping[str, object]],
) -> tuple[int | None, str]:
    """The prompt version and skill digest the session recorded last.

    Only entries after the latest reset count, because the projection starts
    the section replay there too.
    """
    version: int | None = None
    digest = ""
    start = context_start(entries)
    for entry in entries[start:]:
        if entry.get("type") != "system_section":
            continue
        raw = entry.get("promptVersion")
        if not isinstance(raw, int) or isinstance(raw, bool):
            continue
        version = raw
        value = entry.get("skillHash")
        digest = value if isinstance(value, str) else ""
    return version, digest


def sync_default_sections(
    store: SessionStore,
    session_id: str,
    layout: ProjectLayout | None,
    *,
    registry: SkillRegistry | None = None,
) -> bool:
    """Bring a session's product-owned sections to the current prompt.

    An old session has its five product sections replaced, keeping every other
    key. A current session is left alone unless the skill registry changed;
    then only the ``skills`` index is rewritten, or removed when no skill is
    left. One entry records the version and the digest, so a repeat call
    appends nothing.
    """
    if layout is None:
        return False
    active = registry or default_registry(layout.root)
    desired = default_sections(layout, registry=active)
    digest = hashlib.sha256(
        desired.get(SKILLS_SECTION, "").encode("utf-8")
    ).hexdigest()
    snapshot = store.load(session_id)
    current = read_projection(snapshot.entries).sections
    version, stored_digest = prompt_state(snapshot.entries)
    if version == PROMPT_VERSION and stored_digest == digest:
        return False
    patch: dict[str, object] = {}
    if version != PROMPT_VERSION:
        patch = _product_patch(current, desired)
    elif current.get(SKILLS_SECTION) != desired.get(SKILLS_SECTION):
        patch[SKILLS_SECTION] = desired.get(SKILLS_SECTION)
    store.append(
        session_id,
        {
            "type": "system_section",
            "sections": patch,
            "promptVersion": PROMPT_VERSION,
            "skillHash": digest,
        },
    )
    return True


def _product_patch(
    current: Mapping[str, str], desired: Mapping[str, str]
) -> dict[str, object]:
    """Replace each product section; drop it when the prompt has none."""
    patch: dict[str, object] = {}
    for key in PRODUCT_SECTIONS:
        wanted = desired.get(key)
        if wanted is None:
            if key in current:
                patch[key] = None
        elif current.get(key) != wanted:
            patch[key] = wanted
    return patch


__all__ = [
    "DEFAULT_GROUNDING",
    "DEFAULT_IDENTITY",
    "DEFAULT_TOOLS",
    "PROMPT_VERSION",
    "SKILLS_SECTION",
    "default_sections",
    "project_text",
    "sync_default_sections",
]
