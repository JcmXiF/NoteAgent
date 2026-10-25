"""The read-only reading pass: one source document in, its substance out.

The reading is not shown to the reader. It is the hand-off to the pass that
writes pages, so it is organised around what a writer needs: what the source
establishes, what carries it, and which subjects deserve a page of their own.
That is also why it has no page types: which folder a subject belongs in is a
decision the code makes, not one the model is asked to repeat.
"""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.io.read_source import SourceDocument
from noteagent.llm.client import ChatMessage
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import (
    MAX_INDEX_CHARS,
    MAX_SOURCE_CHARS,
    folder_context_from_identity,
    read_brief,
    read_optional,
    truncate,
)

#: The headings the reading keeps. The generation pass reads this text whole, so
#: the order also says what matters most: the claims before the suggestions.
_READING_SECTIONS = """\
## What this source is
One short paragraph: what it is, what it argues, and who it is for.

## What it establishes
The definitions, results and methods a reader would come for. For each, say what
it holds and where the source carries it — a section name, or the locator such as
`raw/web/intro-1a2b3c.html#b4` when one was given. Keep what the source argues
apart from what it only asserts.

## What it rests on
The evidence, data and worked examples behind those claims, and the limits the
source sets for itself.

## Subjects worth a page
For each: the name a reader would search for, one sentence on what the page would
say, and which part of the source would carry it. Then a line
"Not enough for a page:" naming what the source only touches, so the next pass
leaves it alone instead of padding it.

## How it meets the wiki
The pages in the index it confirms, extends or contradicts — by title, with the
substance of the agreement or the conflict.

## What is left open
Questions a reader would have next, and anything the source promises and does not
deliver."""


@dataclass(frozen=True, slots=True)
class AnalysisPrompt:
    """The system and user prompts for the analysis pass."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_analysis_prompt(
    doc: SourceDocument,
    layout: ProjectLayout,
) -> AnalysisPrompt:
    """Build the reading prompt (read-only; no wiki writes)."""
    brief = read_brief(layout)
    index = read_optional(layout.wiki_index, MAX_INDEX_CHARS)
    source_context = truncate(doc.content, MAX_SOURCE_CHARS)

    system_parts = [
        "You read one source for a personal wiki. What you write here is handed to",
        "the pass that writes the pages, so its job is to hand over the substance of",
        "the source in the order a writer can use — not to review the source or the",
        "task.",
        "",
        "Write the reading, not the reasoning behind it, and write it in the source's",
        "own language.",
        "",
        _READING_SECTIONS,
    ]
    if brief:
        system_parts.extend(["", "## The project brief", brief])
    if index:
        system_parts.extend(["", "## The wiki as it stands", index])

    folder_context = folder_context_from_identity(doc.identity)

    user_parts = [
        "Read this source:",
        "",
        f"**File:** {doc.identity}",
    ]
    if folder_context:
        user_parts.append(f"**Filed under:** {folder_context}")
    user_parts.extend(["", "---", "", source_context])

    return AnalysisPrompt(
        system="\n".join(system_parts),
        user="\n".join(user_parts),
    )
