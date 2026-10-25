"""Turn one source and its reading into FILE blocks for the wiki.

The reply format is the parser's contract, not a preference: each block names one
page and carries its whole content. Everything else here is about the pages
themselves — which ones this source earns, what their paths are, and the four
frontmatter fields the writer and the reader both rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from noteagent.io.read_source import SourceDocument
from noteagent.llm.client import ChatMessage
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import (
    MAX_ANALYSIS_CHARS,
    MAX_INDEX_CHARS,
    MAX_SOURCE_CHARS,
    folder_context_from_identity,
    read_brief,
    read_optional,
    truncate,
)
from noteagent.prompts.wiki_style import PAGE_GUIDE


def source_summary_path_for_identity(identity: str) -> str:
    """The source page a whole-file ingest writes, from the file's name.

    The extension goes: the page is named for the source, not for its format.
      raw/web/Bellman equation.html  ->  wiki/sources/Bellman equation.md
      raw/pdf/theory.pdf             ->  wiki/sources/theory.md
    """
    stem = Path(identity).stem.strip() or "source"
    return f"wiki/sources/{stem}.md"


def current_wiki_date(today: date | None = None) -> str:
    """YYYY-MM-DD, as frontmatter and log entries write it."""
    return (today or date.today()).isoformat()


@dataclass(frozen=True, slots=True)
class GenerationPrompt:
    """The system and user prompts for the generation pass."""

    system: str
    user: str
    #: The source page this pass is expected to produce.
    source_summary_path: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_generation_prompt(
    doc: SourceDocument,
    layout: ProjectLayout,
    analysis: str,
    *,
    today: date | None = None,
) -> GenerationPrompt:
    """Build the generation prompt from the reading and the source text."""
    brief = read_brief(layout)
    index = read_optional(layout.wiki_index, MAX_INDEX_CHARS)
    source_context = truncate(doc.content, MAX_SOURCE_CHARS)
    analysis_context = truncate(analysis, MAX_ANALYSIS_CHARS)
    summary_path = source_summary_path_for_identity(doc.identity)
    wiki_date = current_wiki_date(today)

    system_parts = [
        "You write the wiki pages one source supports. The reading you are given",
        "says what the source holds; the pages say the same thing in the wiki's own",
        "voice, for a reader who was not there. Do not show your reasoning.",
        "",
        PAGE_GUIDE,
        "",
        "## What to reply with",
        "",
        "Your whole reply is FILE blocks. Each opens on one line,",
        "",
        "    ---FILE: wiki/<path>.md---",
        "",
        "and ends with `---END FILE---`. Nothing before the first block and nothing",
        "between blocks: no preamble, no account of what you wrote.",
        "",
        "A page that already exists is merged with what you write, so write the",
        "complete page rather than a change to it.",
        "",
        "## The pages",
        "",
        f"1. The source page, at exactly `{summary_path}`. It records what this",
        "   source contributed: what it is, the claims it supports, and the passages",
        "   they come from. Short — a record of the source, not a second set of notes.",
        "2. One note at `wiki/<slug>.md` for each subject under \"Subjects worth a",
        "   page\" that the source gives enough for. The slug is the name a reader",
        "   would type; one flat file per subject, and the same slug for the same",
        "   subject every time.",
        f"3. One `wiki/log.md` entry: the line `## [{wiki_date}] ingest | <title>`,",
        "   then a bullet naming each page written. A log entry has no frontmatter.",
        "",
        "The application writes `wiki/index.md` from the pages on disk: do not write",
        "it, and write nothing else.",
        "",
        "## Frontmatter",
        "",
        "The first line of a page is `---`, the frontmatter closes with `---`, and no",
        "```yaml fence goes around it. Four fields, and no others:",
        "",
        "    ---",
        "    title: ...",
        f"    created: {wiki_date}",
        f"    updated: {wiki_date}",
        '    sources: ["raw/web/intro-1a2b3c.html#b4"]',
        "    ---",
        "",
        "- `sources` lists the passages the page was built from, exactly as given.",
        "  When a passage has a block id, cite the block rather than the whole file.",
        "- Quote an entry that contains a space or a colon.",
        "- Body links are `[[slug]]`: no folder, no `.md`, and only to pages the wiki",
        "  already has or this reply writes.",
        "",
    ]
    if brief:
        system_parts.extend(["## The project brief", brief, ""])
    if index:
        system_parts.extend(
            [
                "## The wiki as it stands (for links; the application regenerates it)",
                "",
                "Treat this as a hint for linking. A page listed here already exists;",
                "write it again only if what this source adds has to go on it.",
                "",
                index,
                "",
            ]
        )

    folder_context = folder_context_from_identity(doc.identity)
    user_parts = [f"Source: **{doc.identity}**"]
    if folder_context:
        user_parts.append(f"**Filed under:** {folder_context}")
    user_parts.extend(
        [
            "",
            "## The reading (context for you; do not copy it into the pages)",
            "",
            analysis_context,
            "",
            "## The source",
            "",
            source_context,
            "",
            "---",
            "",
            "Write the FILE blocks for this source. Your reply starts with",
            "`---FILE:` as its first characters.",
        ]
    )

    return GenerationPrompt(
        system="\n".join(system_parts),
        user="\n".join(user_parts),
        source_summary_path=summary_path,
    )
