"""Assemble one LLM request: catalog files, skill names, and history.

System text is not stored in the session file. Tool results are ``role=tool``
messages appended by the query loop, not extra session turns.
"""

from __future__ import annotations

from collections.abc import Sequence

from noteagent.chat.limits import ChatLimits
from noteagent.chat.skills import list_skills
from noteagent.chat.store import ChatConversation, history_for_llm
from noteagent.chat.tools import tool_catalog
from noteagent.chat.tools.types import ChatToolSpec, ToolObservation
from noteagent.llm.client import ChatMessage
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import read_brief, read_optional

_BASE_LINES: tuple[str, ...] = (
    "You are NOTEAGENT Chat for one personal wiki project.",
    "Five action types exist: wiki_read (read), search, write, run (execute), skill.",
    "Only the enabled tools in this request may be called.",
    "Use wiki_read to open page bodies by [[slug]] from the index or search hits.",
    "Use search scope=wiki to find pages by slug or title when the index is not enough.",
    "Use search scope=web only if the user asks about the open web or no page covers it.",
    "Web hits are SERP cards, not sources — do not treat snippets as notes.",
    "Use run job=doctor to lint the current wiki; it does not change files.",
    "Use run job=fetch only when the user asks to open a public URL.",
    "A preview is neither raw nor wiki; do not fetch just because search returned a card.",
    "Use skill to load a SKILL.md when you need the procedure; skills do not run tools.",
    "Use write action=propose_topic_map to plan from clip collections already under",
    "raw/. Intent is optional; that does not write wiki.",
    "Preview-only clips are not sources.",
    "Use write action=ingest_confirmed only after the human has checked pages.",
    "It ignores any confirmed list you invent and writes only persisted write checkboxes.",
    "Collections without on-disk raw files cannot compile.",
    "Do not edit wiki files yourself. Do not write raw/.",
    "Do not read raw/ as notes.",
    "Write in the same language as the user. Do not output hidden reasoning.",
    "When you mention a page, use a [[slug]] wikilink (not a bare title);",
    "a source summary keeps its sources/ folder.",
    "If the index is enough, answer without tools.",
    "Start the final answer directly. Do not narrate these instructions.",
)
_BASE = "\n".join(_BASE_LINES)


def assemble_messages(
    layout: ProjectLayout,
    conversation: ChatConversation,
    limits: ChatLimits,
    *,
    tools: Sequence[ChatToolSpec] | None = None,
) -> list[ChatMessage]:
    specs = tuple(tools) if tools is not None else tool_catalog()
    parts = [_BASE, "", _tool_lines(specs), "", _skill_lines()]

    brief = read_brief(layout, limits.max_brief_chars) if limits.max_brief_chars > 0 else ""
    if brief:
        parts.extend(["", "## Project brief (config/brief.md)", brief])
    index = read_optional(layout.wiki_index, limits.max_index_chars)
    if index:
        parts.extend(["", "## Wiki index (wiki/index.md)", index])
    else:
        parts.extend(
            [
                "",
                "## Wiki index (wiki/index.md)",
                "(missing or empty — say that you cannot see any pages yet)",
            ]
        )

    messages = [ChatMessage(role="system", content="\n".join(parts))]
    for turn in history_for_llm(conversation, limits):
        messages.append(ChatMessage(role=turn["role"], content=turn["content"]))
    if len(messages) < 2:
        raise ValueError("chat session has no user message to answer")
    return messages


def render_observations(observations: Sequence[ToolObservation]) -> str:
    """Tool results rendered as the text the model reads in the next round."""
    blocks: list[str] = []
    for item in observations:
        header = f"### {item.tool} · {item.target} · {item.title}".strip()
        meta_bits: list[str] = []
        path = item.meta.get("path")
        if isinstance(path, str) and path:
            meta_bits.append(f"path: {path}")
        sources = item.meta.get("sources")
        if isinstance(sources, (list, tuple)) and sources:
            meta_bits.append("sources: " + ", ".join(str(src) for src in sources))
        if item.meta.get("missing"):
            meta_bits.append("missing: page not found on disk")
        if item.meta.get("omitted") == "budget":
            meta_bits.append("omitted: context budget")
        if item.meta.get("truncated"):
            meta_bits.append("truncated: yes")
        if item.meta.get("disabled"):
            meta_bits.append("disabled: yes")
        if item.meta.get("needs_human"):
            meta_bits.append("needs_human: yes")
        if item.meta.get("error"):
            meta_bits.append("error: yes")
        url = item.meta.get("url")
        if isinstance(url, str) and url:
            meta_bits.append(f"url: {url}")
        scope = item.meta.get("scope")
        if isinstance(scope, str) and scope:
            meta_bits.append(f"scope: {scope}")
        if item.meta.get("not_source"):
            meta_bits.append("not_source: yes")
        job = item.meta.get("job")
        if isinstance(job, str) and job:
            meta_bits.append(f"job: {job}")
        action = item.meta.get("action")
        if isinstance(action, str) and action:
            meta_bits.append(f"action: {action}")
        if item.meta.get("wrote_wiki"):
            meta_bits.append("wrote_wiki: yes")
        written = item.meta.get("written")
        if isinstance(written, (list, tuple)) and written:
            meta_bits.append("written: " + ", ".join(str(path) for path in written))
        lines = [header]
        if meta_bits:
            lines.append("; ".join(meta_bits))
        if item.content:
            lines.append(item.content)
        elif item.meta.get("missing"):
            lines.append("(no body)")
        elif item.meta.get("omitted"):
            lines.append("(body omitted)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _tool_lines(specs: Sequence[ChatToolSpec]) -> str:
    enabled = [spec for spec in specs if spec.enabled]
    if not enabled:
        return "Enabled tools: none."
    lines = ["Enabled tools:"]
    for spec in enabled:
        extra = f" ({spec.argument_hint})" if spec.argument_hint else ""
        lines.append(f"- {spec.name}: {spec.description}{extra}")
    disabled = [spec.name for spec in specs if not spec.enabled]
    if disabled:
        lines.append("Disabled this turn: " + ", ".join(disabled) + ".")
    return "\n".join(lines)


def _skill_lines() -> str:
    entries = list_skills()
    if not entries:
        return "Skills: none."
    lines = [
        "Skills (name + when to use; call skill to load the full text):",
    ]
    for item in entries:
        desc = item.description or "(no description)"
        lines.append(f"- {item.name}: {desc}")
    return "\n".join(lines)
