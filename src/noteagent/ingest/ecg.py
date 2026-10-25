"""ECG compile operator Γ: typed plan neighbors → body wikilinks.

Does not score relatedness, does not write L-edges into GET /graph.
Neighbors come from topic-map edges (pairwise, or a synthesized star).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from noteagent.ingest.frontmatter import parse_page, replace_page_body

# The marker text and its line pattern live with the reading rule both stamped
# markers share; ``ECG_MARK`` stays importable from here for callers.
from noteagent.ingest.marker_lines import (
    ECG_MARK,
    ECG_MARK_LINE,
    heading_text,
    next_content_line,
    split_lines,
)
from noteagent.ingest.topic_map import (
    INVERSE_RELATION,
    RELATIONS,
    TopicMap,
    TopicNode,
)
from noteagent.wiki.link_syntax import format_wikilink
from noteagent.wiki.links import (
    normalize_wikilink_target,
    parse_wikilink_targets,
    wiki_slug_for_path,
)

ACTIONS = frozenset({"write", "mention", "skip"})
PLACEMENT = {
    "prerequisite": "needed first; near principles",
    "used_by": "follows or applies this page; in the close / applications",
    "contrast": "a comparison sentence",
    "cooccurs": "in the related close",
}
RELATION_LABEL = {
    "prerequisite": "前置",
    "used_by": "后置",
    "contrast": "对照",
    "cooccurs": "同现",
}
ACTION_LABEL = {
    "write": "本轮将写",
    "mention": "仅提及",
}
_REL_ORDER = {"prerequisite": 0, "contrast": 1, "cooccurs": 2, "used_by": 3}
_ACT_ORDER = {"write": 0, "mention": 1, "skip": 2}
#: One list item. The writer emits ``-``; the other bullet forms and ordered
#: markers are read as well, so a section a model rewrote comes off whole
#: instead of leaving its items behind.
_ITEM_LINE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")


@dataclass(frozen=True, slots=True)
class LogicLink:
    name: str
    slug: str
    wiki_path: str
    relation: str
    action: str
    wikilink: str


def wikilink_target(wiki_path: str, slug: str) -> str:
    path = (wiki_path or "").replace("\\", "/").strip()
    if path.startswith("wiki/") and path.lower().endswith(".md"):
        try:
            return wiki_slug_for_path(path)
        except ValueError:
            pass
    return (slug or "").strip().strip("/")


def logic_links_for_page(
    topic_map: TopicMap,
    page_slug: str,
    action_by_slug: Mapping[str, str],
) -> tuple[LogicLink, ...]:
    """Neighbors from typed edges. Source sees INVERSE; target sees the edge type."""
    current = topic_map.node_for_slug(page_slug)
    if current is None:
        return ()
    chosen: dict[str, LogicLink] = {}
    for edge in topic_map.edges:
        if edge.source == current.slug:
            other = topic_map.node_for_slug(edge.target)
            relation = INVERSE_RELATION.get(edge.relation, edge.relation)
        elif edge.target == current.slug:
            other = topic_map.node_for_slug(edge.source)
            relation = edge.relation
        else:
            continue
        if other is None:
            continue
        link = _link_from_node(
            other,
            relation,
            action_by_slug.get(other.slug, "skip"),
        )
        existing = chosen.get(other.slug)
        if existing is None or _REL_ORDER.get(link.relation, 9) < _REL_ORDER.get(
            existing.relation, 9
        ):
            chosen[other.slug] = link
    links = list(chosen.values())
    links.sort(
        key=lambda item: (
            _ACT_ORDER.get(item.action, 9),
            _REL_ORDER.get(item.relation, 9),
            item.slug.casefold(),
        )
    )
    return tuple(links)


def format_logic_links_for_prompt(links: Sequence[LogicLink]) -> str:
    if not links:
        return ""
    lines = [
        "## This-round typed neighbors (ECG compile; no scores)",
        "",
        "Logical position from this session's plan, not similarity.",
        "Put [[folder/slug]] in the body (not only frontmatter `related`).",
    ]
    for item in links:
        placement = PLACEMENT.get(item.relation, PLACEMENT["cooccurs"])
        target = format_wikilink(item.wikilink) if item.wikilink else item.name
        if item.action == "write":
            rule = f"body must include {target} ({placement})"
        elif item.action == "mention":
            rule = f"one honest sentence + {target}; do not emit a FILE for it"
        else:
            rule = "do not start this topic; no obligatory link"
        lines.append(
            f"- {item.action} / {item.relation}: {item.name} → {target} — {rule}"
        )
    return "\n".join(lines)


def stamp_logic_wikilinks(content: str, links: Sequence[LogicLink]) -> str:
    """Append missing write/mention [[wikilink]]s. Skip stays unlinked."""
    needed = [
        item
        for item in links
        if item.action in {"write", "mention"} and item.wikilink
    ]
    if not needed:
        return content
    page = parse_page(content)
    body = page.body if page.has_frontmatter else content
    body_wo = _strip_ecg_block(body)
    missing = [item for item in needed if not body_has_logic_link(body_wo, item)]
    new_body = body_wo.rstrip()
    if missing:
        new_body = f"{new_body}\n\n{_ecg_section(missing)}\n"
    else:
        new_body = f"{new_body}\n"
    if page.has_frontmatter:
        return replace_page_body(content, new_body)
    return new_body if new_body.endswith("\n") else new_body + "\n"


def body_has_logic_link(body: str, link: LogicLink) -> bool:
    targets = set(parse_wikilink_targets(body))
    wanted = {
        normalize_wikilink_target(link.wikilink),
        normalize_wikilink_target(link.slug),
        normalize_wikilink_target(link.name),
    }
    if "/" in (link.wikilink or ""):
        wanted.add(normalize_wikilink_target(link.wikilink.rsplit("/", 1)[-1]))
    wanted.discard("")
    return bool(targets & wanted)


def _link_from_node(node: TopicNode, relation: str, action: str) -> LogicLink:
    rel = relation if relation in RELATIONS else "cooccurs"
    act = action if action in ACTIONS else "skip"
    return LogicLink(
        name=node.title,
        slug=node.slug,
        wiki_path=node.wiki_path,
        relation=rel,
        action=act,
        wikilink=wikilink_target(node.wiki_path, node.slug),
    )


def _strip_ecg_block(body: str) -> str:
    """
    Remove every stamped ECG section from *body*.

    A section is the marker plus the heading it owns and that heading's list,
    read by the same rule the ``na-clip`` markers use (see ``marker_lines``):
    blank lines are allowed anywhere inside, so the shape the writer produces
    comes off whole and no bullet is left behind for the next section.
    """
    lines = split_lines(body)
    out: list[str] = []
    index = 0
    while index < len(lines):
        if not ECG_MARK_LINE.fullmatch(lines[index]):
            out.append(lines[index])
            index += 1
            continue
        # The blank lines before the marker go with it, so removing a section
        # does not accumulate spacing.
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
        end = _ecg_section_end(lines, index)
        index = index + 1 if end is None else end
    return "\n".join(out)


def _ecg_section_end(lines: Sequence[str], marker: int) -> int | None:
    """
    Index just past the section at *marker*, or ``None`` when the heading and
    list this module writes are not what follows it.

    A marker with anything else under it is still ours, so the caller drops the
    marker alone rather than guessing at the text below it.
    """
    heading = next_content_line(lines, marker + 1)
    if heading is None or heading_text(lines[heading]) is None:
        return None
    first_item = next_content_line(lines, heading + 1)
    if first_item is None or not _ITEM_LINE.match(lines[first_item]):
        return None
    index = first_item
    while index < len(lines):
        if _ITEM_LINE.match(lines[index]):
            index += 1
            continue
        if lines[index].strip():
            break
        following = next_content_line(lines, index)
        if following is None or not _ITEM_LINE.match(lines[following]):
            break
        index += 1
    return index


def _ecg_section(links: Sequence[LogicLink]) -> str:
    lines = [ECG_MARK, "## 相关", ""]
    for item in links:
        rel = RELATION_LABEL.get(item.relation, item.relation)
        act = ACTION_LABEL.get(item.action, item.action)
        lines.append(f"- {rel}（{act}）：{format_wikilink(item.wikilink)} {item.name}")
    return "\n".join(lines)


__all__ = [
    "ECG_MARK",
    "INVERSE_RELATION",
    "LogicLink",
    "body_has_logic_link",
    "format_logic_links_for_prompt",
    "logic_links_for_page",
    "stamp_logic_wikilinks",
    "wikilink_target",
]
