"""What a pass proposes to do with a page, and how much of it to write.

One type for one idea. A page gets proposed before anything is read (a search names
the pages it wants to touch) and gets refined once material is in hand (the plan says
what goes on each one). Those are two stages of the same proposal, so they are one
type with one identity — not two types that both claim to say which pages exist.

Two questions, kept apart because they are genuinely different:

- `action` — is the page already there? (`add` / `revise`) This is what the round's
  overlay draws and what the gating logic keys on.
- `coverage` — how much of it should be written? (`write` / `mention` / `skip`) This
  is what a plan decides once it has read the material.

They are separate questions: one says whether the page exists, the other says how
much of it this round writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Whether the page exists already. Empty means nobody said.
PAGE_ACTIONS: tuple[str, ...] = ("add", "revise")
#: How much of the page to write.
COVERAGES: tuple[str, ...] = ("write", "mention", "skip")


@dataclass(frozen=True, slots=True)
class PageSection:
    """One titled part of a page, and the passages it is written from."""

    id: str
    title: str
    block_ids: tuple[str, ...]
    collection_id: str = ""
    role: str = ""


@dataclass(frozen=True, slots=True)
class PageProposal:
    """One page a pass has proposed, from first mention to planned content.

    Which fields are filled depends on how far along the pass is: a proposal made
    before reading has an identity, a question to search, and whether the page is
    new; one made after reading also has the coverage, the sections, and which
    passages each section comes from.
    """

    slug: str
    #: What the page is called. One name for it, everywhere in the core.
    title: str = ""
    #: Where it lives, once somebody has decided (`wiki/x.md`).
    wiki_path: str = ""
    #: `add` / `revise`, or empty when nobody has said.
    action: str = ""
    #: `write` / `mention` / `skip`: how much of it to write.
    coverage: str = "skip"
    #: Whether the reader still wants this page. Only a round's own list has this.
    accepted: bool = True
    #: The question to search for, when a round goes looking for material.
    query: str = ""
    #: A named part of the page the material is expected to land in.
    section: str = ""
    #: Why the round wants this page (written before anything was read).
    plan: str = ""
    #: Why the plan wants it, pointing at the passages (written after reading).
    evidence: str = ""
    #: Why the core itself wants it, as a code the interface renders.
    #:
    #: A reason the core worked out is not a sentence: the core does not know what
    #: language the reader is reading in, and a sentence written here would arrive in
    #: whichever one happened to be hard-coded. A code plus its figures travels
    #: instead, and the interface says it in the reader's own words — the same road
    #: `error.*` messages travel.
    reason: str = ""
    #: The figures that code needs, as text (a count, a page name).
    reason_args: tuple[tuple[str, str], ...] = ()
    relation_to_primary: str = ""
    collection_ids: tuple[str, ...] = ()
    collection_roles: tuple[tuple[str, str], ...] = ()
    block_ids: tuple[str, ...] = ()
    outline: tuple[PageSection, ...] = ()
    #: True when the pass's list of pages does not have this one: material that
    #: turned up while reading, kept and marked rather than dropped.
    unlisted: bool = False


__all__ = [
    "COVERAGES",
    "PAGE_ACTIONS",
    "PageProposal",
    "PageSection",
    "page_section_from_wire",
    "page_section_to_wire",
    "proposal_from_wire",
    "proposal_to_wire",
]


def page_section_to_wire(section: PageSection) -> dict[str, object]:
    return {
        "id": section.id,
        "title": section.title,
        "blockIds": list(section.block_ids),
        "collectionId": section.collection_id,
        "role": section.role,
    }


def _wire_list(raw: dict[str, Any], camel: str, snake: str) -> list[object]:
    value = raw.get(camel) or raw.get(snake) or []
    return value if isinstance(value, list) else []


def _str_tuple(raw: dict[str, Any], camel: str, snake: str) -> tuple[str, ...]:
    out: list[str] = []
    for item in _wire_list(raw, camel, snake):
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


def page_section_from_wire(raw: object) -> PageSection | None:
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or "").strip()
    if not ident:
        return None
    block_ids = tuple(
        str(item).strip()
        for item in _wire_list(raw, "blockIds", "block_ids")
        if isinstance(item, str) and item.strip()
    )
    return PageSection(
        id=ident,
        title=str(raw.get("title") or "").strip(),
        block_ids=block_ids,
        collection_id=str(raw.get("collectionId") or raw.get("collection_id") or "").strip(),
        role=str(raw.get("role") or "").strip(),
    )


def proposal_to_wire(item: PageProposal) -> dict[str, object]:
    """One page proposal as JSON, whichever stage of a pass wrote it.

    Both the round's list and the plan drawn from it are proposals, so they go out in
    one shape. Which fields are empty is what says how far along the pass is: an
    identity and a query before anything is read, plus coverage and sections after.
    """
    return {
        "title": item.title,
        "slug": item.slug,
        "wikiPath": item.wiki_path,
        "action": item.action,
        "coverage": item.coverage,
        "accepted": item.accepted,
        "query": item.query,
        "section": item.section,
        "plan": item.plan,
        "evidence": item.evidence,
        "reason": item.reason,
        "reasonArgs": dict(item.reason_args),
        "relationToPrimary": item.relation_to_primary,
        "collectionIds": list(item.collection_ids),
        "collectionRoles": [
            {"collectionId": cid, "role": role} for cid, role in item.collection_roles
        ],
        "blockIds": list(item.block_ids),
        "outline": [page_section_to_wire(section) for section in item.outline],
        "unlisted": item.unlisted,
    }


def _reason_args_from_wire(raw: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """The figures a reason code needs, however they were written."""
    args = raw.get("reasonArgs") or raw.get("reason_args") or {}
    if not isinstance(args, dict):
        return ()
    out: list[tuple[str, str]] = []
    for key, value in args.items():
        name = str(key or "").strip()
        if not name or value is None or isinstance(value, (dict, list)):
            continue
        out.append((name, str(value)))
    return tuple(out)


def proposal_from_wire(raw: dict[str, Any]) -> PageProposal:
    """Read what ``proposal_to_wire`` wrote.

    Both camelCase and snake_case keys are accepted, and ``name`` is read as
    ``title`` when the writer used that spelling.
    """
    outline = raw.get("outline")
    sections = (
        tuple(
            section
            for section in (page_section_from_wire(item) for item in outline)
            if section is not None
        )
        if isinstance(outline, list)
        else ()
    )
    roles = raw.get("collectionRoles") or raw.get("collection_roles") or []
    parsed_roles: list[tuple[str, str]] = []
    if isinstance(roles, list):
        for item in roles:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("collectionId") or item.get("collection_id") or "").strip()
            role = str(item.get("role") or "").strip()
            if cid:
                parsed_roles.append((cid, role))
    return PageProposal(
        slug=str(raw.get("slug") or "").strip(),
        title=str(raw.get("title") or raw.get("name") or "").strip(),
        wiki_path=str(raw.get("wikiPath") or raw.get("wiki_path") or "").strip(),
        action=str(raw.get("action") or "").strip(),
        coverage=str(raw.get("coverage") or "skip").strip(),
        accepted=bool(raw.get("accepted", True)),
        query=str(raw.get("query") or "").strip(),
        section=str(raw.get("section") or "").strip(),
        plan=str(raw.get("plan") or "").strip(),
        evidence=str(raw.get("evidence") or "").strip(),
        reason=str(raw.get("reason") or "").strip(),
        reason_args=_reason_args_from_wire(raw),
        relation_to_primary=str(
            raw.get("relationToPrimary") or raw.get("relation_to_primary") or ""
        ).strip(),
        collection_ids=_str_tuple(raw, "collectionIds", "collection_ids"),
        collection_roles=tuple(parsed_roles),
        block_ids=_str_tuple(raw, "blockIds", "block_ids"),
        outline=sections,
        unlisted=bool(raw.get("unlisted")),
    )
