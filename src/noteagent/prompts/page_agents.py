"""Hierarchical agents that write one confirmed wiki page."""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.llm.client import ChatMessage
from noteagent.prompts.generation import current_wiki_date
from noteagent.prompts.wiki_style import PAGE_GUIDE


@dataclass(frozen=True, slots=True)
class AgentPrompt:
    """The system and user prompts for one page-writing agent."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_blueprint_prompt(
    *,
    intent: str,
    page_name: str,
    wiki_path: str,
    section_plan: str,
    mention_names: list[str],
    skip_names: list[str],
    collection_catalog: str,
    clips_text: str,
    logic_links_text: str = "",
    brief: str = "",
) -> AgentPrompt:
    """Page architect: JSON contributions, one collection per unit."""
    mention = ", ".join(mention_names) if mention_names else "(none)"
    skip = ", ".join(skip_names) if skip_names else "(none)"
    system = "\n".join(
        [
            "You are the page architect for one wiki page.",
            "Do not write the page. Output one JSON object only. No FILE blocks.",
            "",
            PAGE_GUIDE,
            "",
            "Plan contributions as whole clip collections. Each contribution is one",
            "contiguous stretch of the final page, fed by exactly one collectionId.",
            "Order contributions so the wiki spine works (definition before derivation).",
            "If two collections cover the same formula, keep both units; do not merge them",
            "into one contribution.",
            "",
            "JSON:",
            "{",
            '  "title": "page title",',
            '  "wikiPath": "wiki/example-page.md",',
            '  "contributions": [',
            "    {",
            '      "collectionId": "id from catalog",',
            '      "role": "definition|principle|derivation|application|related|other",',
            '      "sectionTitle": "short wiki heading",',
            '      "blockIds": ["b1"],',
            '      "instruction": "what this unit must keep from the clips (one or two sentences)"',
            "    }",
            "  ]",
            "}",
            "",
            f"May mention in the close, no FILE: {mention}",
            f"Do not treat as this page's subject: {skip}",
            "Leave room in the close for the neighbouring pages this round links to",
            "(a prerequisite near the principles, used_by / contrast at the end).",
            "Do not invent a numeric relatedness score.",
        ]
    )
    user_parts = [
        f"Session goal: **{intent}**",
        f"This page: **{page_name}** at `{wiki_path}`",
        "",
        "## Human-ticked outline (titles only; refine, do not ignore)",
        "",
        section_plan or "(none — infer from clips, stay on this topic)",
    ]
    if logic_links_text.strip():
        user_parts.extend(["", logic_links_text.strip(), ""])
    user_parts.extend(
        [
            "",
            "## Collections",
            "",
            collection_catalog,
            "",
            "## Clips",
            "",
            clips_text,
            "",
            "Return JSON. First character `{` or a ```json fence.",
        ]
    )
    user = "\n".join(user_parts)
    if brief:
        system = system + "\n\n## The project brief\n" + brief
    return AgentPrompt(system=system, user=user)


def build_draft_prompt(
    *,
    page_name: str,
    headline: str,
    role: str,
    section_title: str,
    instruction: str,
    collection_text: str,
    collection_id: str,
    brief: str = "",
) -> AgentPrompt:
    """Write one collection's contribution. No other collections in context."""
    marker = f"<!-- na-clip:{collection_id} -->"
    system = "\n".join(
        [
            "You draft ONE contribution to a wiki page from ONE clip collection.",
            "Output Markdown only: a heading plus body. No YAML, no FILE blocks,",
            "no preamble.",
            "",
            PAGE_GUIDE,
            "",
            "Stay on the page topic. Follow the collection's own order and examples.",
            "Keep formulas and caveats that appear in the clips. Do not import facts",
            "from other articles. Do not start a different knowledge point.",
            "If the collection is thin, write a short accurate stub, not filler.",
            "First line must be this HTML comment (reader uses it to open the source set):",
            marker,
            "If you emit several headings for this unit, put the same comment before each.",
        ]
    )
    user = "\n".join(
        [
            f"Wiki page topic: **{page_name}**",
            f"Collection title: **{headline}**",
            f"Role on the page: {role}",
            f"Suggested heading: {section_title}",
            f"Architect note: {instruction}",
            "",
            "## This collection only",
            "",
            collection_text,
            "",
            "Write the Markdown contribution now.",
        ]
    )
    if brief:
        system = system + "\n\n## The project brief\n" + brief
    return AgentPrompt(system=system, user=user)


def build_compose_prompt(
    *,
    intent: str,
    page_name: str,
    wiki_path: str,
    wiki_date: str,
    mention_names: list[str],
    locators: list[str],
    blueprint_text: str,
    drafts_text: str,
    logic_links_text: str = "",
    brief: str = "",
) -> AgentPrompt:
    """Assemble collection drafts into one FILE wiki page."""
    locator_joined = ", ".join(f'"{item}"' for item in locators) or "(none)"
    mention = ", ".join(mention_names) if mention_names else "(none)"
    system = "\n".join(
        [
            "You compose one wiki page from already-written collection drafts.",
            "Output only FILE blocks. No chain-of-thought.",
            "",
            PAGE_GUIDE,
            "",
            "Use each draft as an atomic unit. Reorder units to match the spine.",
            "Do not splice sentences from two drafts into one paragraph.",
            "Add a short opening definition if none of the drafts opens that way.",
            "Add a short close (application / significance / related) if the drafts",
            "lack it; related names may be mentioned only:",
            f"  {mention}",
            "Cross-reference this round's neighbors with body [[wikilinks]].",
            "Prefer bare [[slug]]. Do not invent a numeric relatedness score.",
            "Light connective tissue is allowed. Rewriting a draft's argument is not.",
            "",
            "Each draft starts with `<!-- na-clip:COLLECTION_ID -->`. Keep that comment",
            "immediately before every heading that belongs to that unit. If you split one",
            "unit into several headings, repeat the same comment before each. Do not",
            "invent ids. Do not delete these comments.",
            "",
            f"Write exactly one content FILE at **{wiki_path}**.",
            "Do not emit wiki/index.md or extra note pages.",
            "",
            "Frontmatter: first line `---`, inline YAML lists, no fences.",
            "  title",
            f"  created / updated — {wiki_date}",
            f"  sources — block locators from: {locator_joined}",
            "",
            "Opener must be one line: ---FILE: wiki/path.md---",
        ]
    )
    user_parts = [
        f"Session goal: **{intent}**",
        f"Page: **{page_name}** → `{wiki_path}`",
        "",
        "## Blueprint",
        "",
        blueprint_text,
    ]
    if logic_links_text.strip():
        user_parts.extend(["", logic_links_text.strip()])
    user_parts.extend(
        [
            "",
            "## Collection drafts (keep as units)",
            "",
            drafts_text,
            "",
            f"Emit FILE blocks. First characters MUST be `---FILE:`. Path `{wiki_path}`.",
        ]
    )
    user = "\n".join(user_parts)
    if brief:
        system = system + "\n\n## The project brief\n" + brief
    return AgentPrompt(system=system, user=user)


def compose_wiki_date() -> str:
    """Today's date, as compose prompts write it into frontmatter."""
    return current_wiki_date()


__all__ = [
    "AgentPrompt",
    "build_blueprint_prompt",
    "build_compose_prompt",
    "build_draft_prompt",
    "compose_wiki_date",
]
