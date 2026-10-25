"""Blueprint → per-collection draft → compose for one confirmed wiki page."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from noteagent.ingest.clips import (
    ClipCollection,
    collection_headline,
    collections_as_source_text,
    format_collection_catalog,
    format_collection_text,
    locators_for_refs,
)
from noteagent.ingest.ecg import (
    LogicLink,
    format_logic_links_for_prompt,
    stamp_logic_wikilinks,
)
from noteagent.ingest.frontmatter import format_inline_list
from noteagent.ingest.parse_file_blocks import FileBlock, parse_file_blocks
from noteagent.ingest.source_citations import scan_clip_marks
from noteagent.ingest.topic_map import TopicNode, extract_json_object
from noteagent.llm.client import LlmClient
from noteagent.prompts.generation import current_wiki_date
from noteagent.prompts.page_agents import (
    build_blueprint_prompt,
    build_compose_prompt,
    build_draft_prompt,
)
from noteagent.util.cancel import check_cancelled

SECTION_ROLES = frozenset(
    {"definition", "principle", "derivation", "application", "related", "other"}
)
_ROLE_ORDER = {
    "definition": 0,
    "principle": 1,
    "derivation": 2,
    "application": 3,
    "related": 4,
    "other": 5,
}
_CLIP_MARK = re.compile(
    r"<!--\s*na-clip:([^\s>]+)(?:\s+[^>]*?)?\s*-->",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Contribution:
    collection_id: str
    role: str
    section_title: str
    block_ids: tuple[str, ...]
    instruction: str


@dataclass(frozen=True, slots=True)
class PageBlueprint:
    title: str
    wiki_path: str
    contributions: tuple[Contribution, ...]

    def to_json_text(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "wikiPath": self.wiki_path,
                "contributions": [
                    {
                        "collectionId": item.collection_id,
                        "role": item.role,
                        "sectionTitle": item.section_title,
                        "blockIds": list(item.block_ids),
                        "instruction": item.instruction,
                    }
                    for item in self.contributions
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


def clip_marker(collection_id: str) -> str:
    return f"<!-- na-clip:{collection_id} -->"


def ensure_clip_marker(collection_id: str, body: str) -> str:
    text = body.strip()
    token = clip_marker(collection_id)
    if not text:
        return token
    first = text.split("\n", 1)[0]
    if _CLIP_MARK.search(first) and collection_id in first:
        return text
    if token in text[:80]:
        return text
    return f"{token}\n{text}"


def stamp_clip_markers(
    content: str,
    contributions: tuple[Contribution, ...] | list[Contribution],
) -> str:
    """Insert ``na-clip`` comments before matching headings if compose dropped them.

    Does not invent mappings: a contribution without a heading that contains
    its ``section_title`` stays unmarked, and the reader reports it as unbound.
    """
    text = content.replace("\r\n", "\n")
    items = list(contributions)
    for item in reversed(items):
        token = clip_marker(item.collection_id)
        if token in text:
            continue
        title = (item.section_title or "").strip()
        if len(title) < 2:
            continue
        pattern = re.compile(
            rf"^(#{{2,6}}[ \t]+.*{re.escape(title)}.*)$",
            re.MULTILINE,
        )
        match = None
        for found in pattern.finditer(text):
            preceding = text[: found.start()]
            if token in preceding[-80:]:
                continue
            match = found
            break
        if match is None:
            continue
        text = f"{text[: match.start()]}{token}\n{text[match.start() :]}"
    return text


class PageStructureError(ValueError):
    """A composed page would leave agent content with no source marker."""


def agent_marker_breaks(
    content: str,
    contributions: tuple[Contribution, ...] | list[Contribution],
) -> list[str]:
    """
    Why *content* may not be written, given the contributions it was composed from.

    Every contribution has to own a heading, and no marker may sit anywhere other
    than directly above one. A contribution that was dropped, or a marker stacked
    above another, would leave a section that reads as if no agent wrote it, so the
    page is refused rather than written with a silent gap. The reading rule is the
    reader's own, so what this accepts is exactly what the reader can bind.
    """
    marks = scan_clip_marks(content)
    owned = {mark.collection_id for mark in marks if mark.heading is not None}
    reasons = [
        f"no heading carries the marker for {item.collection_id}"
        for item in contributions
        if item.collection_id not in owned
    ]
    stranded = sum(1 for mark in marks if mark.heading is None)
    if stranded:
        reasons.append(f"{stranded} marker(s) do not sit directly above a heading")
    return reasons


def parse_blueprint(
    payload: str | dict[str, Any],
    *,
    page_name: str,
    wiki_path: str,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> PageBlueprint:
    data = payload if isinstance(payload, dict) else extract_json_object(payload)
    if not isinstance(data, dict):
        raise ValueError("page blueprint must be a JSON object")
    known = {item.id: item for item in collections}
    title = str(data.get("title") or page_name).strip() or page_name
    path = str(data.get("wikiPath") or data.get("wiki_path") or wiki_path).replace("\\", "/")
    raw_items = data.get("contributions") or []
    if not isinstance(raw_items, list):
        raise ValueError("contributions must be a list")
    contributions: list[Contribution] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("collectionId") or item.get("collection_id") or "").strip()
        collection = known.get(cid)
        if collection is None:
            continue
        role = str(item.get("role") or "other").strip()
        if role not in SECTION_ROLES:
            role = "other"
        section_title = str(
            item.get("sectionTitle") or item.get("section_title") or ""
        ).strip()
        if not section_title:
            section_title = collection_headline(collection)
        allowed = set(collection.block_ids)
        block_ids = tuple(
            str(block).strip()
            for block in (item.get("blockIds") or item.get("block_ids") or [])
            if isinstance(block, str) and block.strip() in allowed
        )
        if not block_ids:
            block_ids = collection.block_ids
        instruction = str(item.get("instruction") or "").strip()[:400]
        contributions.append(
            Contribution(
                collection_id=cid,
                role=role,
                section_title=section_title,
                block_ids=block_ids,
                instruction=instruction or "Keep this collection's order and examples.",
            )
        )
    if not contributions:
        contributions = _fallback_contributions(collections)
    return PageBlueprint(
        title=title,
        wiki_path=path or wiki_path,
        contributions=tuple(contributions),
    )


def write_confirmed_page(
    *,
    intent: str,
    page_name: str,
    wiki_path: str,
    node: TopicNode | None,
    collections: tuple[ClipCollection, ...],
    mention_names: list[str],
    skip_names: list[str],
    client: LlmClient | None,
    on_status: Callable[[str], None] | None = None,
    script: dict[str, Any] | None = None,
    logic_links: Sequence[LogicLink] | tuple[LogicLink, ...] = (),
    cancel: threading.Event | None = None,
    brief: str = "",
) -> tuple[FileBlock, list[tuple[str, str]]]:
    """
    Run blueprint → drafts → compose. ``script`` injects JSON/text for tests.

    Returns the content FILE block and (collection_id, block_id) refs used.
    """
    script = script or {}
    relevant = _collections_for_page(node, collections)
    catalog = format_collection_catalog(relevant)
    clips_text = _clips_for(relevant)
    section_plan = _section_plan(node)
    links_text = format_logic_links_for_prompt(logic_links)

    def status(message: str) -> None:
        if on_status is not None:
            on_status(message)

    status(f"blueprint {page_name}")
    check_cancelled(cancel)
    raw_blueprint = script.get("blueprint")
    if raw_blueprint is None:
        if client is None:
            raise RuntimeError("blueprint requires an LLM client")
        prompt = build_blueprint_prompt(
            intent=intent,
            page_name=page_name,
            wiki_path=wiki_path,
            section_plan=section_plan,
            mention_names=mention_names,
            skip_names=skip_names,
            collection_catalog=catalog,
            clips_text=clips_text,
            logic_links_text=links_text,
            brief=brief,
        )
        raw_blueprint = client.chat(prompt.as_messages(), cancel=cancel)
    blueprint = parse_blueprint(
        raw_blueprint,
        page_name=page_name,
        wiki_path=wiki_path,
        collections=relevant,
    )

    by_id = {item.id: item for item in collections}
    unique: list[Contribution] = []
    seen_ids: set[str] = set()
    for contribution in blueprint.contributions:
        if contribution.collection_id in seen_ids:
            continue
        seen_ids.add(contribution.collection_id)
        unique.append(contribution)
    drafts: list[tuple[Contribution, str]] = []
    injected = script.get("drafts")
    injected_drafts: dict[str, Any] = injected if isinstance(injected, dict) else {}
    for contribution in unique:
        collection = by_id.get(contribution.collection_id)
        if collection is None:
            continue
        headline = collection_headline(collection)
        status(f"draft {headline}")
        check_cancelled(cancel)
        raw_draft = injected_drafts.get(contribution.collection_id)
        if raw_draft is None:
            if client is None:
                raise RuntimeError("draft requires an LLM client")
            prompt = build_draft_prompt(
                page_name=page_name,
                headline=headline,
                role=contribution.role,
                section_title=contribution.section_title,
                instruction=contribution.instruction,
                collection_text=format_collection_text(collection),
                collection_id=contribution.collection_id,
                brief=brief,
            )
            raw_draft = client.chat(prompt.as_messages(), cancel=cancel)
        text = ensure_clip_marker(contribution.collection_id, str(raw_draft or ""))
        if text.strip():
            drafts.append((contribution, text))

    drafts_text = _format_drafts(drafts, by_id)
    refs = [
        (item.collection_id, block_id)
        for item, _draft in drafts
        for block_id in item.block_ids
    ]
    locators = locators_for_refs(collections, refs)
    wiki_date = current_wiki_date()

    status(f"compose {page_name}")
    check_cancelled(cancel)
    raw_compose = script.get("compose")
    if raw_compose is None:
        if client is None:
            raise RuntimeError("compose requires an LLM client")
        prompt = build_compose_prompt(
            intent=intent,
            page_name=page_name,
            wiki_path=wiki_path,
            wiki_date=wiki_date,
            mention_names=mention_names,
            locators=locators,
            blueprint_text=blueprint.to_json_text(),
            drafts_text=drafts_text,
            logic_links_text=links_text,
            brief=brief,
        )
        raw_compose = client.chat(prompt.as_messages(), cancel=cancel)
    block = _file_from_compose(str(raw_compose), wiki_path, page_name, wiki_date, locators)
    stamped = stamp_clip_markers(block.content, unique)
    stamped = stamp_logic_wikilinks(stamped, logic_links)
    breaks = agent_marker_breaks(stamped, unique)
    if breaks:
        raise PageStructureError("; ".join(breaks))
    return FileBlock(path=block.path, content=stamped), refs


def _collections_for_page(
    node: TopicNode | None,
    collections: tuple[ClipCollection, ...],
) -> tuple[ClipCollection, ...]:
    if node is None or not node.collection_ids:
        return collections
    wanted = set(node.collection_ids)
    picked = tuple(item for item in collections if item.id in wanted)
    return picked or collections


def _section_plan(node: TopicNode | None) -> str:
    if node is None or not node.outline:
        return ""
    lines = []
    for section in node.outline:
        extra = ""
        if section.collection_id:
            extra = f" · collection {section.collection_id}"
        if section.role:
            extra += f" · {section.role}"
        lines.append(f"- {section.title}{extra}")
    return "\n".join(lines)


def _clips_for(collections: tuple[ClipCollection, ...] | list[ClipCollection]) -> str:
    return collections_as_source_text(collections)


def _fallback_contributions(
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> list[Contribution]:
    out: list[Contribution] = []
    for collection in collections:
        out.append(
            Contribution(
                collection_id=collection.id,
                role="other",
                section_title=collection_headline(collection),
                block_ids=collection.block_ids,
                instruction="Keep this collection as one unit; do not mix others into it.",
            )
        )
    return out


def _format_drafts(
    drafts: list[tuple[Contribution, str]],
    by_id: dict[str, ClipCollection],
) -> str:
    ordered = sorted(
        enumerate(drafts),
        key=lambda pair: (_ROLE_ORDER.get(pair[1][0].role, 9), pair[0]),
    )
    chunks: list[str] = []
    for _index, (contribution, body) in ordered:
        collection = by_id.get(contribution.collection_id)
        if collection is not None:
            headline = collection_headline(collection)
        else:
            headline = contribution.collection_id
        chunks.append(
            f"### UNIT role={contribution.role} · {headline}\n"
            f"heading: {contribution.section_title}\n"
            f"{clip_marker(contribution.collection_id)}\n\n"
            f"{body.strip()}"
        )
    return "\n\n".join(chunks) or "(no drafts)"


def _file_from_compose(
    text: str,
    wiki_path: str,
    page_name: str,
    wiki_date: str,
    locators: list[str],
) -> FileBlock:
    parsed = parse_file_blocks(text)
    wanted = wiki_path.replace("\\", "/")
    for block in parsed.blocks:
        if block.path.replace("\\", "/") == wanted:
            return FileBlock(path=wanted, content=block.content)
    if parsed.blocks:
        return FileBlock(path=wanted, content=parsed.blocks[0].content)
    body = text.strip()
    if not body.startswith("---"):
        body = (
            "---\n"
            f"title: {page_name}\n"
            f"created: {wiki_date}\n"
            f"updated: {wiki_date}\n"
            f"sources: {format_inline_list(locators)}\n"
            "---\n\n"
            f"# {page_name}\n\n"
            f"{body}\n"
        )
    return FileBlock(path=wanted, content=body)


__all__ = [
    "Contribution",
    "PageBlueprint",
    "PageStructureError",
    "agent_marker_breaks",
    "clip_marker",
    "ensure_clip_marker",
    "parse_blueprint",
    "stamp_clip_markers",
    "write_confirmed_page",
]
