"""One page, written again with the page in front of the model.

This is the pre-launch repair for the one blind spot in the compile pipeline: the
generation call writes every page from the source and the index, and a page that
already exists is reconciled afterwards, by a second model that sees two finished
texts. That reconciliation can only be as good as the two texts, and it is asked
to restore what the first call never knew.

So for a page above the size where this matters, the page is written once more —
this time with itself as part of what is being written from. The reply replaces
that page and is not merged again; R4b in ``ingest/page_merge.py`` says why.

The draft is not a second page. It is what this source would say about a subject
the page already covers, and the difference matters: the model is asked for one
account, told plainly that the draft is not to be appended to the page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from noteagent.llm.client import ChatMessage
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import (
    MAX_ANALYSIS_CHARS,
    MAX_INDEX_CHARS,
    MAX_SOURCE_CHARS,
    read_brief,
    read_optional,
    truncate,
)
from noteagent.prompts.generation import current_wiki_date
from noteagent.prompts.wiki_style import PAGE_GUIDE


@dataclass(frozen=True, slots=True)
class PageRewritePrompt:
    """The system and user prompts for rewriting one page."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_page_rewrite_prompt(
    *,
    page_path: str,
    existing_content: str,
    draft_content: str,
    analysis: str,
    layout: ProjectLayout,
    today: date | None = None,
) -> PageRewritePrompt:
    """Build the prompt for one page this source landed on."""
    brief = read_brief(layout)
    index = read_optional(layout.wiki_index, MAX_INDEX_CHARS)
    wiki_date = current_wiki_date(today)

    system_parts = [
        "You are writing one page of a personal wiki again. A source that was just",
        "read covers the subject this page already has, so the page is being",
        "written once more with both accounts available to you.",
        "",
        PAGE_GUIDE,
        "",
        "## What you are given",
        "",
        "- The page as it stands. This is what the reader has now.",
        "- A draft: what the new source would say under this page's name, written",
        "  without having seen the page. It is one source's account of the subject,",
        "  not a second page to append.",
        "- The reading of that source, for context only.",
        "",
        "## What to write",
        "",
        "One page a reader can use, as if it had always been written from both. Keep",
        "the substance of the page and the substance the draft adds: a subject only",
        "one of them mentions is not thereby unimportant. Where they disagree, say",
        "what each account holds and what it holds under; do not average the",
        "disagreement away, and do not resolve a conflict the sources left open.",
        "",
        "Write the subject, not the work: no sentence may say that two texts were",
        "combined, name a source or file, or contrast an older and a newer account.",
        "",
        "Keep the page's opening, the text above its first heading. It introduces the",
        "subject to a reader arriving from a link; extend it only when the new",
        "material changes what the subject is.",
        "",
        "## What the program settles",
        "",
        "The frontmatter is reconciled after you: the page keeps its `title` and",
        "`created`, `sources` is unioned with what this write brought, and `updated`",
        "is today's date. Write the four fields, keep `title` as it is, and give",
        "`sources` exactly what the page should cite.",
        "",
        "`wiki/index.md` is written by the application from the pages on disk: do not",
        "write it. Link only to pages the index lists or to ones you know exist;",
        "`[[slug]]`, no folder and no extension.",
        "",
        "Any `<!-- na-clip:ID -->` or `<!-- na-ecg -->` line the page carries stays,",
        "on the line directly above the heading it precedes. Do not invent one.",
        "",
        "## What to reply with",
        "",
        "The page itself and nothing around it: frontmatter first, then the body. No",
        "`---FILE:` wrapper, no preamble, no account of what you changed, no",
        "reasoning.",
        "",
        f"The first line of the frontmatter is `---`, the block closes with `---`, and",
        f"`updated` is `{wiki_date}`.",
    ]
    if brief:
        system_parts.extend(["", "## The project brief", brief])
    if index:
        system_parts.extend(
            [
                "",
                "## The wiki as it stands (for links; the application regenerates it)",
                "",
                index,
            ]
        )

    user_parts = [
        f"Page: `{page_path}`",
        "",
        "## The page as it stands",
        "",
        truncate(existing_content, MAX_SOURCE_CHARS),
        "",
        "---",
        "",
        "## The draft this source produced",
        "",
        truncate(draft_content, MAX_SOURCE_CHARS),
        "",
        "---",
        "",
        "## The reading (context only; do not copy it into the page)",
        "",
        truncate(analysis, MAX_ANALYSIS_CHARS),
        "",
        "---",
        "",
        "Write the page. Your reply starts with `---`.",
    ]

    return PageRewritePrompt(system="\n".join(system_parts), user="\n".join(user_parts))
