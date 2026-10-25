"""Per-step Deep Search prompts. Working sets only; never accumulate prior drafts."""

from __future__ import annotations

from dataclasses import dataclass

from noteagent.ingest.deep_search_store import STEP_CHARS
from noteagent.llm.client import ChatMessage
from noteagent.prompts.context import truncate


@dataclass(frozen=True, slots=True)
class DeepSearchPrompt:
    """The system and user prompts for one Deep Search step."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_whitelist_prompt(
    *,
    mode: str,
    intent: str,
    seed_line: str,
    graph_summary: str,
    index_text: str,
    remainder_skim: str,
    language: str = "",
) -> DeepSearchPrompt:
    """Ask for one round's whitelist: pages to add or revise, plus overlay edges."""
    mode_name = "chew" if mode == "chew" else "explore"
    system = "\n".join(
        [
            "You propose a short wiki whitelist for one Deep Search round.",
            "Output one JSON object only. No chain-of-thought.",
            "Do not write wiki pages, FILE blocks, or clip collections.",
            "Discovery is not permission to create pages. The human will accept or drop rows.",
            "At most 8 pages (add + revise). At most 16 overlay edges.",
            "Pages you invent must have a slug and a one-sentence plan.",
            "Do not pretend you read full wiki bodies or raw HTML.",
            "A page's name follows the material it is about.",
            *(
                [
                    f"The reader is using {language}. Write every plan in {language} —",
                    "even when the material, the intent and the page names are in",
                    "another language. A plan in the wrong language is a defect.",
                ]
                if language
                else []
            ),
        ]
    )
    parts = [
        f"mode: {mode_name}",
        f"intent: {intent.strip() or '(none)'}",
        f"seed: {seed_line or '(none)'}",
        "",
        "## Graph summary (no page bodies)",
        graph_summary or "(empty graph)",
        "",
        "## Wiki titles (truncated index)",
        index_text or "(no index)",
    ]
    if mode == "chew":
        parts.extend(
            [
                "",
                "## Unclipped derived skim (not sources, not for writing pages)",
                remainder_skim or "(no unclipped blocks)",
            ]
        )
    else:
        parts.extend(
            [
                "",
                "Explore mode: ignore raw remainder. Only reason over the subgraph table.",
            ]
        )
    parts.extend(
        [
            "",
            "JSON shape:",
            '{ "pages": [ { "slug", "title", "action": "add"|"revise",',
            # The language sits next to the field it applies to: an instruction about
            # output language is followed far more often when it is written where the
            # model is deciding what to put there.
            '  "plan" (' + language + '), "query", "section" } ],'
            if language
            else '  "plan", "query", "section" } ],',
            '  "edges": [ { "source", "target", "relation" } ],',
            '  "omitted": 0 }',
            "revise = existing wiki page. add = not yet a page. query is for later web search.",
        ]
    )
    if language:
        # Said again at the end: what a model reads last is what it is most likely to
        # follow, and this is the instruction it got wrong when it was said once.
        parts.append(f"REMINDER: every plan field must be written in {language}.")
    user = truncate("\n".join(parts), STEP_CHARS)
    return DeepSearchPrompt(system=system, user=user)


def build_blocks_prompt(
    *,
    intent: str,
    wiki_titles: str,
    remainder_previews: str,
) -> DeepSearchPrompt:
    """Ask for leftover derived blocks the round could still chew on."""
    system = "\n".join(
        [
            "You propose leftover derived blocks from raw sources for one Chew round.",
            "Output one JSON object only. No chain-of-thought.",
            "Do not create clips or write wiki pages. The human will accept blocks, "
            "then plan pages.",
            "At most 8 leftover blocks per source file.",
            "pageSlug is an optional short topic hint, not a wiki page to write.",
            "Do not invent a page you cannot back with a leftover locator. "
            "Copy locator strings exactly.",
            "Do not assign a block to two files. Skip already-clipped blocks.",
        ]
    )
    user = truncate(
        "\n".join(
            [
                f"intent: {intent.strip() or '(none)'}",
                "",
                "## Existing wiki titles (optional hint only; do not pick pages to write)",
                wiki_titles or "(no index)",
                "",
                "## Unclipped block previews",
                remainder_previews or "(none)",
                "",
                "JSON shape:",
                '{ "blocks": [ { "pageSlug", "locator", "preview" } ] }',
            ]
        ),
        STEP_CHARS,
    )
    return DeepSearchPrompt(system=system, user=user)
