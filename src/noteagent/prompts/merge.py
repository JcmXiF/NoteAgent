"""The prompt that asks for one page when a second source lands on it.

This is R6 of ``ingest/page_merge.py``: the page already exists, the write brought
material the page does not claim, so neither text can simply replace the other and
a model is asked for the page both of them describe.

The prompt is written around the three things only the model can decide:

- what the subject is, when two accounts of it overlap but do not coincide;
- which differences are a disagreement between sources and which are just two
  halves of one account;
- how to say all of it in the page's own voice, with no trace of the two texts.

Everything else is the code's, and saying so in the prompt is cheaper than
trusting the model to infer it. The checks that run on the answer are listed here
because a refused answer costs the whole merge: the page keeps its old body and
the call is wasted. See ``structure_breaks`` and ``merge_page_content`` for the
checks themselves; they are the authority, not this text.

The annotations (``na-clip`` / ``na-ecg``) are the one part of the page that
carries its own provenance, which is why they are described by what they mean
rather than as lines to copy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

from noteagent.llm.client import ChatMessage, LlmClient
from noteagent.util.cancel import check_cancelled

# This module stays free of ingest imports, or the cycle
# prompts.merge → ingest.__init__ → auto_ingest → prompts.merge closes.
MergeFn = Callable[[str, str, str], str]


#: ``repair(existing, merged_incoming, source_identity, reason) -> whole page``.
RepairFn = Callable[[str, str, str, str], str]


@dataclass(frozen=True, slots=True)
class MergePrompt:
    """System and user messages for one page-merge LLM call."""

    system: str
    user: str

    def as_messages(self) -> list[ChatMessage]:
        """The prompt as chat messages, ready for the client."""
        return [
            ChatMessage(role="system", content=self.system),
            ChatMessage(role="user", content=self.user),
        ]


def build_page_merge_system_prompt() -> str:
    """System message: one page about one subject, written as if it were the only text."""
    return "\n".join(
        [
            "A page of a personal wiki is being written again, because a second source",
            "was read for the subject the page already covers. You write the page.",
            "",
            "## What you are given",
            "",
            "- The page as it stands. This is what the reader has now.",
            "- The material this write brought, already shaped as a page.",
            "- The passages the page says it was built from, when they can still be",
            "  read. They are not a third account to weave in: they are what the",
            "  page's own account was written from, so they are how you tell a",
            "  fact the page holds from a drift the page picked up later.",
            "",
            "They are about the same subject -- the page's path names it -- and they",
            "were written at different times from different sources. Neither is a",
            "version of the other, and neither is wrong by being older or newer.",
            "",
            "## What the reply is",
            "",
            "The body of the page. Whatever you write replaces the body that is there,",
            "so a fact that was on the page and is not in your reply is gone from it.",
            "That is the one rule this cannot recover from, and it decides most of the",
            "others: keep the substance of both texts, and write it as one account.",
            "",
            "Where the two agree, say it once. Where they cover different ground, cover",
            "both -- a subject the other text happened not to mention is not thereby",
            "unimportant. Where they disagree, do not average the disagreement away:",
            "say what each account holds, and what it holds under, and let the reader",
            "see that the sources differ. A page that resolves a conflict the sources",
            "left open is a page that made something up.",
            "",
            "## When a passage is there",
            "",
            "A passage settles a question about what the page was built from, not",
            "about the subject. Where the page and its passage disagree, the passage",
            "is what the source said; keep what the passage supports and drop what it",
            "does not, rather than carrying a drift forward. Where a passage is silent",
            "on something the page says, that is not a contradiction: the page may",
            "hold it from material that is no longer cited.",
            "",
            "Never copy a passage into the page, and never quote it: the page states",
            "the subject in its own voice, and a pasted passage is not a page. Never",
            "cite a passage the page did not list either -- attribution stays exactly",
            "as the frontmatter has it.",
            "",
            "## What stays as it is",
            "",
            "A line such as `<!-- na-clip:ID -->` or `<!-- na-ecg -->` records which",
            "passages the section beneath it was written from. Every one either text",
            "carries stays, unchanged, on the line directly above the heading it",
            "precedes -- move a section and its line moves with it. Do not write a new",
            "one: it would attribute prose to a passage nobody read, and the program",
            "cannot tell that from a real citation.",
            "",
            "A `[[link]]` either text carries stays a link to the same page. Adding one",
            "is fine when the subject needs it and the page it names exists --",
            "`wiki/index.md` lists what exists, and a link to anything else is a dead",
            "end in the graph.",
            "",
            "Keep the page's opening, the text above its first heading: it introduces",
            "the subject to a reader arriving from a link. Extend it only if the new",
            "material changes what the subject is.",
            "",
            "## Voice",
            "",
            "Write the subject, not the work. The page never says that two texts were",
            "combined, never names a source or a file in its prose, and never contrasts",
            "an old and a new account. A reader who never saw either text should find",
            "nothing in the page that refers to them. Attribution belongs in the",
            "frontmatter and in the annotations, where the reader can act on it.",
            "",
            "Write in the language the page is already written in, and keep the",
            "material's own terms and notation rather than translating them into",
            "vocabulary the page happens to use.",
            "",
            "## What the program checks",
            "",
            "The reply is checked before it is written, and a reply that fails leaves",
            "the page exactly as it was:",
            "",
            "- it opens with frontmatter, first line `---`;",
            "- no annotation is missing, invented, or left without a heading below it;",
            "- no link target either text carried has disappeared;",
            "- the body is not far shorter than the longer of the two texts.",
            "",
            "The frontmatter is the program's to settle: it keeps the page's title and",
            "creation date, unions the `sources` list and stamps `updated`. Leave those",
            "fields as they are. A field the page lacks and your body needs, you may",
            "add.",
            "",
            "One of these may have refused an earlier answer at this page; the message",
            "names which, and that is the one to fix first.",
            "",
            "Reply with the page itself and nothing around it. No preamble, no note",
            "about what changed, no `---FILE:` wrapper, no reasoning.",
        ]
    )


def build_page_merge_user_message(
    existing_content: str,
    incoming_content: str,
    source_identity: str,
    evidence: str = "",
    refused: str = "",
) -> str:
    """User message: the two texts, labelled by what they are rather than by age.

    *evidence* is the resolved passages block, already formatted by the caller;
    an empty string leaves it out, which is what a page with no resolvable
    sources gets.

    *refused* turns the message into a second attempt: it is the reason the last
    answer was not written, which R7b hands to the model verbatim.
    """
    material = "## The material this write brought"
    if source_identity.strip():
        # Worth naming so the model can weigh the two accounts; not worth naming
        # in the page, which the system message says.
        material = f"{material} (from `{source_identity}`; not to be named in the page)"
    parts = [
        "## The page as it stands",
        "",
        existing_content,
        "",
        "---",
        "",
    ]
    if evidence.strip():
        parts.extend([evidence.strip(), "", "---", ""])
    parts.extend(
        [
            material,
            "",
            incoming_content,
            "",
            "---",
            "",
        ]
    )
    if refused.strip():
        parts.extend(
            [
                "## Why the last answer was not written",
                "",
                refused.strip(),
                "",
                "Keep everything the page holds and everything the material adds,",
                "without whatever that check names. A shorter page is not the fix.",
                "",
                "Write the page again.",
            ]
        )
    else:
        parts.append("Write the page.")
    return "\n".join(parts)


def build_page_merge_prompt(
    existing_content: str,
    incoming_content: str,
    source_identity: str,
    *,
    evidence: str = "",
    refused: str = "",
) -> MergePrompt:
    """The two messages that ask for one merged page, or for a second attempt."""
    return MergePrompt(
        system=build_page_merge_system_prompt(),
        user=build_page_merge_user_message(
            existing_content, incoming_content, source_identity, evidence, refused
        ),
    )


def build_page_merger(
    client: LlmClient,
    *,
    passages: Callable[[str], str] | None = None,
    on_token: Callable[[str], None] | None = None,
    cancel: Event | None = None,
) -> MergeFn:
    """Wire an ``LlmClient`` into the ``MergeFn`` expected by ``merge_page_content``.

    *passages* resolves a page's own cited passages to their text; the ingest
    layer supplies it because this module must not import ingest. Without it the
    merge is the two-text prompt it has always been.
    """

    def merger(
        existing_content: str,
        incoming_content: str,
        source_identity: str,
    ) -> str:
        check_cancelled(cancel)
        evidence = ""
        if passages is not None:
            # A page whose sources cannot be resolved is merged without
            # evidence, which is the old prompt, not a failed call.
            evidence = passages(existing_content)
        prompt = build_page_merge_prompt(
            existing_content, incoming_content, source_identity, evidence=evidence
        )
        return client.chat(prompt.as_messages(), on_token=on_token, cancel=cancel)

    return merger


def build_page_repairer(
    client: LlmClient,
    *,
    passages: Callable[[str], str] | None = None,
    on_token: Callable[[str], None] | None = None,
    cancel: Event | None = None,
) -> RepairFn:
    """Wire an ``LlmClient`` into the ``RepairFn`` of rule R7b.

    A repair is the same question asked again with the refusal in hand, so it
    shares this module's prompt and its evidence: the page, its own passages, the
    material, and now the reason the last answer was not written.
    """

    def repair(
        existing_content: str,
        incoming_content: str,
        source_identity: str,
        reason: str,
    ) -> str:
        check_cancelled(cancel)
        evidence = ""
        if passages is not None:
            evidence = passages(existing_content)
        prompt = build_page_merge_prompt(
            existing_content,
            incoming_content,
            source_identity,
            evidence=evidence,
            refused=reason,
        )
        return client.chat(prompt.as_messages(), on_token=on_token, cancel=cancel)

    return repair

