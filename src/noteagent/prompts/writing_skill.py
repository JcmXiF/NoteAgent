"""The writing skill as a context source: when to select it, and what it says.

The skill is not a tool: it is the ``skills`` system section the model reads, so
a request that asks for a page selects it and the next request that does not
leaves it out again. An experiment can force it on or off. The deterministic
evals and the node's short instruction do not need it.
"""

from __future__ import annotations

from noteagent.agent.projection import project
from noteagent.agent.store import SessionStore
from noteagent.ingest.frontmatter import parse_page
from noteagent.skills import BUNDLED_ROOT

SKILL_PATH = BUNDLED_ROOT / "writing" / "SKILL.md"
WRITING_SECTION = "skills"

#: A person naming the skill outright always gets it.
_EXPLICIT = (
    "/writing",
    "writing skill",
    "写作 skill",
    "写作技能",
    "用写作",
)
#: The intent phrases that ask for a page, not a question about one. A bare
#: "整理成" is not enough: "把结果整理成表格" is not a wiki request.
_INTENT = (
    "写一篇",
    "写一页",
    "写个 wiki",
    "写 wiki",
    "写wiki",
    "整理成一页",
    "整理成一篇",
    "整理成 wiki",
    "整理成wiki",
    "更新这一页",
    "更新这个页面",
    "更新 wiki",
    "更新wiki",
    "补一节",
    "补一段",
    "改写这一页",
    "改成 wiki",
    "write a wiki page",
    "write the wiki page",
    "update this page",
    "add a section",
)


def writing_skill_text() -> str:
    """The bundled skill's instructions, without the frontmatter."""
    return parse_page(
        SKILL_PATH.read_text(encoding="utf-8")
    ).body.strip()


def select_writing_skill(message: str) -> bool:
    """Whether *message* asks to write (or names the skill)."""
    text = message.casefold()
    return any(item in text for item in _EXPLICIT) or any(
        item in text for item in _INTENT
    )


def sync_writing_skill(
    store: SessionStore,
    session_id: str,
    message: str,
    *,
    enabled: bool | None = None,
) -> bool:
    """Select the skill for this request; drop it when the request moves on.

    ``enabled`` is the experiment's override: ``None`` follows the message,
    ``True`` forces the skill, ``False`` keeps the session plain. The section is
    only removed when it is the one this module wrote, so a session's own
    skills are left alone.
    """
    current = str(
        project(store.load(session_id).entries).sections.get(
            WRITING_SECTION
        )
        or ""
    ).strip()
    text = writing_skill_text()
    selected = (
        enabled if enabled is not None else select_writing_skill(message)
    )
    if selected:
        if current == text:
            return False
        store.append(
            session_id,
            {
                "type": "system_section",
                "sections": {WRITING_SECTION: text},
            },
        )
        return True
    if current == text:
        store.append(
            session_id,
            {
                "type": "system_section",
                "sections": {WRITING_SECTION: None},
            },
        )
        return True
    return False


__all__ = [
    "SKILL_PATH",
    "WRITING_SECTION",
    "select_writing_skill",
    "sync_writing_skill",
    "writing_skill_text",
]
