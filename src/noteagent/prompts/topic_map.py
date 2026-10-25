"""Planning pass: what a round of clips could become, as JSON.

This pass names pages; it never writes them. The reply is parsed, so the shape is
fixed, and every claim of support has to point at a passage that was on offer —
a plan the writer cannot open is worse than no plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noteagent.io.read_source import SourceDocument
from noteagent.llm.client import ChatMessage
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import (
    MAX_INDEX_CHARS,
    MAX_SOURCE_CHARS,
    read_brief,
    read_optional,
    truncate,
)
from noteagent.prompts.wiki_style import PAGE_GUIDE

if TYPE_CHECKING:
    from noteagent.ingest.topic_map import PlanTarget


@dataclass(frozen=True, slots=True)
class TopicMapPrompt:
    """The system and user prompts for one planning pass."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_topic_map_prompt(
    doc: SourceDocument,
    layout: ProjectLayout,
    *,
    intent: str,
    collection_catalog: str,
    targets: Sequence["PlanTarget"] = (),
    language: str = "",
) -> TopicMapPrompt:
    """Ask the model for a parseable plan. No FILE blocks.

    With ``targets`` the pages are already named: the pass is about those pages,
    and the model's job is to say what goes on each one. Without them the model
    names them, which is what a pass starting from nothing has to do.
    """
    brief = read_brief(layout)
    index = read_optional(layout.wiki_index, MAX_INDEX_CHARS)
    source_context = truncate(doc.content, MAX_SOURCE_CHARS)
    note = (intent or "").strip() or "(none — induce titles from the clips)"

    system = "\n".join(
        [
            "You plan what a round of clipped passages adds to a wiki. You name",
            "candidate pages, say which passages support each one, and propose the",
            "links between them. You do not write pages: no Markdown, no FILE blocks,",
            "one JSON object and nothing else. Do not show your reasoning.",
            "",
            "The clips are the human's choice of what counts as material; the page",
            "names are usually yours to infer. A collection title in the catalog is a",
            "label the human already sees — reuse it when you say what that",
            "collection gives. A session note is a bias, not a page title.",
            "",
            PAGE_GUIDE,
            "",
            "Discovery is not a decision to write. List every distinct knowledge",
            "point the clips contain, including the ones that should only be",
            "mentioned or skipped. A subject that cannot meet the paragraph above is",
            "a mention, not a page.",
            "",
            "Evidence rules:",
            "- Every statement about support points at a [bN] in a named collection.",
            "- Never invent a relatedness score, and never treat two collections",
            "  sharing a source file as a reason to put them on one page.",
            "- A collection is atomic here: it backs at most one candidate with",
            "  coverage=write. One candidate may rest on several collections when",
            "  they are about the same subject.",
            "",
            "collectionRoles.role — what this collection does for the candidate:",
            "  defines   states the definition or the core claim",
            "  derives   derives, proves, or works the mathematics",
            "  applies   applies or instantiates the idea",
            "  alias     the same idea under another name",
            "  mentions  named in passing, not enough to write a page from",
            "",
            "coverage — should this become a page of its own?",
            "  write     a collection defines, derives or applies it, or the source's",
            "            title names it, or several substantial blocks argue it",
            "  mention   the clips only pass through it; a link, not a page",
            "  skip      no [bN] supports it",
            "",
            "relationToPrimary — from a candidate toward the round's main page:",
            "  prerequisite  needed to define or understand the other",
            "  used_by       applies or follows the other",
            "  contrast      defined against the other",
            "  cooccurs      appears beside it with no clear before/after",
            "",
            "edges — typed relations between candidates, only those the clips show:",
            "  from/to are candidate slugs, and relation uses the four names above.",
            "  from is related to to in that way (entropy -prerequisite-> KL says",
            "  entropy is needed to understand KL). Each edge needs a collection",
            "  title and a [bN] as evidence. Omit a pair the clips do not support; a",
            "  shared source file is not an edge.",
            "",
            "outline — the sections of one candidate:",
            "  role is definition | principle | derivation | application | related | other.",
            "  Each item belongs to exactly one collectionId; do not mix two",
            "  collections in one item, and prefer several short titled items to one",
            "  item that dumps every block.",
            "",
            "JSON shape:",
            "{",
            '  "collectionSummaries": [',
            '    {"id": "collection-id", "title": "reuse catalog title", '
            '"gist": "one short clause"}',
            "  ],",
            '  "candidates": [',
            "    {",
            '      "name": "induced wiki title",',
            '      "slug": "kebab-or-unicode-slug",',
            '      "wikiPath": "wiki/existing-or-proposed.md",',
            '      "coverage": "write",',
            '      "relationToPrimary": "cooccurs",',
            '      "collectionIds": ["collection-id"],',
            '      "collectionRoles": [{"collectionId": "collection-id", "role": "defines"}],',
            '      "blockIds": ["b1"],',
            '      "evidence": "one sentence with collection title + [bN]",',
            '      "outline": [{',
            '        "id": "s1", "title": "定义", "role": "definition",',
            '        "collectionId": "collection-id", "blockIds": ["b4"]',
            "      }]",
            "    }",
            "  ],",
            '  "edges": [',
            "    {",
            '      "from": "information-entropy",',
            '      "to": "kl-divergence",',
            '      "relation": "prerequisite",',
            '      "collectionId": "collection-id",',
            '      "blockIds": ["b2"],',
            '      "evidence": "one sentence with collection title + [bN]"',
            "    }",
            "  ],",
            '  "notes": "optional short remark, not a wiki page"',
            "}",
            "",
            "Rules:",
            "- Induce titles from the clips, not from file names.",
            "- If a page in the index matches a candidate, use that page's path.",
            "- The same normalized title is one candidate; different ideas inside one",
            "  file stay different candidates.",
            "- collectionIds and blockIds must come from the catalog and the [bN]",
            "  markers.",
            "- Outline titles are section names as the reader would write them.",
            "- A note lives at `wiki/<slug>.md`; a source page at",
            "  `wiki/sources/<slug>.md`.",
            # Names and section titles are content: they follow what the material
            # calls things. The explanation is written for a reader, so it follows the
            # reader's language — the same split as anywhere else in the core.
            "- The language of names and outline titles follows the clips.",
            *(
                [
                    f"- The reader is using {language}. Write every evidence sentence in",
                    f"  {language} — even when the clips, the intent and the page names are",
                    "  in another language. Evidence in the wrong language is a defect.",
                ]
                if language
                else []
            ),
            "- Put evidenced relations between candidates in edges[]. Do not leave",
            "  two write candidates unrelated when the clips show a before/after.",
        ]
    )
    if brief:
        system = (
            system
            + "\n\n## The project brief (long-term; not a page title)\n"
            + brief
        )
    if targets:
        system = (
            system
            + "\n\n## Pages already chosen for this pass\n"
            + "The human has already decided which pages this pass is about; they are\n"
            + "listed in the user message. Every candidate's slug MUST be one of those\n"
            + "slugs, spelled exactly, and each candidate must be for the page it names.\n"
            + "Passages the list does not cover belong in the outline of the page they\n"
            + "are closest to, or in notes — do not invent a page that is not listed.\n"
            + "candidates[].coverage says whether that page should be written now."
        )
    if index:
        system = (
            system
            + "\n\n## The wiki as it stands (match existing paths where relevant)\n"
            + index
        )

    user_parts = [
        f"Session note (optional bias, NOT a page title): **{note}**",
        "",
    ]
    if targets:
        user_parts += [
            "## Pages this pass may write (use these exact slugs)",
            "",
            *[
                f"- {item.slug} — {item.title or item.slug}"
                + (f" ({item.action})" if item.action else "")
                for item in targets
            ],
            "",
        ]
    user_parts += [
        "## Clip catalog (ids and titles the JSON must reuse)",
        "",
        collection_catalog,
        "",
        "## Clipped passages (derived text with [bN] markers)",
        "",
        source_context,
        "",
        "Return the JSON object now. First character must be `{` or a ```json fence.",
        "Do not copy source formulas or set-braces `{ ... }` into the reply; cite [bN]",
        "instead.",
    ]
    return TopicMapPrompt(system=system, user="\n".join(user_parts))


__all__ = ["TopicMapPrompt", "build_topic_map_prompt"]
