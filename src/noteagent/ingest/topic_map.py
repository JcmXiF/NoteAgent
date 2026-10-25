"""Parse, resolve, and merge topic maps. Never writes wiki pages."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    GateDecision,
    collection_headline,
    collections_as_source_text,
    format_collection_catalog,
    load_clip_store,
    require_durable_sources,
)
from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.ingest.proposal import (
    COVERAGES,
    PageProposal,
    PageSection,
    proposal_to_wire,
)
from noteagent.ingest.source_citations import is_citing_wiki_page
from noteagent.io.read_source import SourceDocument
from noteagent.llm.client import LlmClient, language_name
from noteagent.llm.config import LlmConfig, load_llm_config
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.topic_map import build_topic_map_prompt
from noteagent.util.cancel import check_cancelled
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger, emit_status
from noteagent.wiki.link_syntax import WIKILINK_SOURCE
from noteagent.wiki.links import normalize_wikilink_target, wiki_slug_for_path

RELATIONS = frozenset({"prerequisite", "used_by", "contrast", "cooccurs"})
NODE_RELATIONS = RELATIONS | {"primary"}
COLLECTION_ROLES = frozenset(
    {"defines", "derives", "applies", "alias", "mentions"}
)
SECTION_ROLES = frozenset(
    {"definition", "principle", "derivation", "application", "related", "other"}
)
MAX_ALSO = 16
MAX_CANDIDATES = 24
MAX_EDGES = 48
MAX_OUTLINE = 12
MAX_NAME = 120
INVERSE_RELATION = {
    "prerequisite": "used_by",
    "used_by": "prerequisite",
    "contrast": "contrast",
    "cooccurs": "cooccurs",
}
_SLUG_SAFE = re.compile(r"[^\w\-]+", re.UNICODE)
#: One index line: a link, then an optional ``— description``. The link half is
#: the shared pattern, so an index line and a page link are read by one rule.
_INDEX_ITEM = re.compile(rf"^-\s*{WIKILINK_SOURCE}(?:\s*[—–-]\s*(.+))?\s*$")


@dataclass(frozen=True, slots=True)
class CollectionSummary:
    id: str
    title: str
    gist: str


#: A page a plan proposes. The same type a round proposes pages with, so the two
#: stages of one proposal cannot drift apart in what they can say.
TopicNode = PageProposal


@dataclass(frozen=True, slots=True)
class TopicEdge:
    source: str
    target: str
    relation: str
    evidence: str = ""
    collection_id: str = ""
    block_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicMap:
    intent: str
    primary: TopicNode
    also_in_sources: tuple[TopicNode, ...]
    notes: str
    collection_summaries: tuple[CollectionSummary, ...] = ()
    candidates: tuple[TopicNode, ...] = ()
    edges: tuple[TopicEdge, ...] = ()

    def nodes(self) -> tuple[TopicNode, ...]:
        if self.candidates:
            return self.candidates
        return (self.primary, *self.also_in_sources)

    def node_for_slug(self, slug: str) -> TopicNode | None:
        for node in self.nodes():
            if node.slug == slug:
                return node
        return None

    def to_dict(self) -> dict[str, Any]:
        nodes = self.nodes()
        return {
            "intent": self.intent,
            "collectionSummaries": [
                {"id": item.id, "title": item.title, "gist": item.gist}
                for item in self.collection_summaries
            ],
            "candidates": [_node_to_dict(item) for item in nodes],
            "primary": _node_to_dict(self.primary),
            "alsoInSources": [_node_to_dict(item) for item in self.also_in_sources],
            "edges": [_edge_to_dict(item) for item in self.edges],
            "notes": self.notes,
        }


def _slug_text(value: str) -> str:
    """One name as a slug: lowercase, hyphenated, nothing but word characters left."""
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = _SLUG_SAFE.sub("", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def topic_slug(name: str) -> str:
    """A name flattened into a single slug, for a title or a file name.

    A flat slug is what a *name* wants: a file name cannot hold folders, and a title
    that happens to contain one should not become one. Page identity is different —
    see `page_slug`.
    """
    return _slug_text(name)[:80] or "topic"


def page_slug(text: str) -> str:
    """A page's identity, however the page was written down.

    `wiki/entropy.md`, `entropy` and `/entropy/` are one page, and a page still
    sitting in a retired type folder keeps that folder in its identity, so an
    unmigrated page does not collide with a flat note of the same name.
    """
    value = (text or "").strip().replace("\\", "/").strip("/")
    if value.lower().startswith("wiki/"):
        value = value[len("wiki/") :]
    if value.lower().endswith(".md"):
        value = value[:-3]
    parts = [part for part in (_slug_text(part) for part in value.split("/")) if part]
    return "/".join(parts)[:80] or "topic"


def page_slug_from_path(path: str) -> str:
    """`wiki/entropy.md` → `entropy`, the graph's page id."""
    clean = (path or "").replace("\\", "/").strip()
    if not clean.startswith("wiki/"):
        return ""
    return page_slug(clean)


def wiki_path_for_slug(slug: str) -> str:
    """Where a page with this identity lives when nobody said where.

    A new note is flat: ``wiki/<slug>.md``. An identity that already names a
    folder keeps it, so a page read from an unmigrated project still resolves to
    the file it came from.
    """
    return f"wiki/{page_slug(slug)}.md"


def node_for_reference(
    topic_map: TopicMap,
    *,
    slug: str = "",
    wiki_path: str = "",
) -> TopicNode | None:
    """The plan's page for a reference someone sent back, however it was spelled.

    A page can be referred to by its slug or by its path, and either may have had
    its folders kept or dropped along the way — a plan's slug has its folders
    stripped when it is normalised, while the page's path keeps them. So the path is
    read as well as the name, and a name without folders only counts when one page
    answers to it.
    """
    nodes = topic_map.nodes()
    names = [slug.strip(), page_slug_from_path(wiki_path), topic_slug(slug)]
    for name in names:
        if not name:
            continue
        hit = next((node for node in nodes if node.slug == name), None)
        if hit is not None:
            return hit
    for name in names:
        if not name:
            continue
        tail = name.rsplit("/", 1)[-1]
        loose = [node for node in nodes if node.slug.rsplit("/", 1)[-1] == tail]
        if len(loose) == 1:
            return loose[0]
    return None


@dataclass(frozen=True, slots=True)
class PlanTarget:
    """A page the caller says this planning pass is about.

    Giving these turns a pass from open (the plan names the pages) into scoped (the
    caller names them and the plan works out what goes on each one). The plan is told
    about them, and the pages it comes back with are bound to them.
    """

    slug: str
    title: str = ""
    #: `add` / `revise` when the caller knows, empty otherwise.
    action: str = ""


def target_index(
    targets: Sequence[PlanTarget],
) -> tuple[
    dict[str, PlanTarget],
    dict[str, list[PlanTarget]],
    dict[str, list[PlanTarget]],
]:
    """The caller's pages, reachable by identity, by title, and by the last part of a name.

    Titles are indexed because a page gets named by its title as often as by its
    identity — a model asked what to write about answers with the name a reader knows.
    They are kept apart from identities, and as lists, because two pages can share a
    title while an identity is unique by construction.
    """
    by_slug: dict[str, PlanTarget] = {}
    by_title: dict[str, list[PlanTarget]] = {}
    by_tail: dict[str, list[PlanTarget]] = {}
    for target in targets:
        slug = (target.slug or "").strip()
        if not slug:
            continue
        by_slug.setdefault(slug, target)
        by_tail.setdefault(slug.rsplit("/", 1)[-1], []).append(target)
        title = (target.title or "").strip()
        if title:
            by_title.setdefault(title, []).append(target)
            by_tail.setdefault(title, []).append(target)
    return by_slug, by_title, by_tail


def target_for_name(
    name: str,
    by_slug: dict[str, PlanTarget],
    by_title: dict[str, list[PlanTarget]],
    by_tail: dict[str, list[PlanTarget]],
) -> PlanTarget | None:
    """Which of the caller's pages a name refers to, if that can be told.

    An identity that matches exactly wins, because that is the model saying precisely
    which page it means. Then a title only one page has. Then the last part of the
    name, which is what a name looks like when someone leaves the folders out — and
    again only when one page answers to it: a pass may be about both
    two pages whose names share a tail, and a bare name is not evidence for either.
    """

    def unique(hits: list[PlanTarget] | None) -> PlanTarget | None:
        return hits[0] if hits and len(hits) == 1 else None

    text = (name or "").strip()
    if not text:
        return None
    exact = by_slug.get(text)
    if exact is not None:
        return exact
    titled = unique(by_title.get(text))
    if titled is not None:
        return titled
    tail = text.rsplit("/", 1)[-1]
    if tail != text:
        without_folders = by_slug.get(tail)
        if without_folders is not None:
            return without_folders
    found = unique(by_tail.get(tail))
    if found is not None:
        return found
    return unique(by_tail.get(text))


def resolve_map_to_targets(
    topic_map: TopicMap,
    targets: Sequence[PlanTarget],
) -> TopicMap:
    """Binds a plan's pages to the caller's list, and marks the ones it does not have.

    Binding is by name, and a page bound by a shorter name is renamed to the
    caller's own: the list is what the rest of the pass refers to, so a page the
    model called `mutual-information` has to become the target's own identity if
    that is the page it means.

    A page the list does not have is kept rather than dropped. Reading is how
    material turns up, and material can be about a page nobody could have named
    before reading it; the page is marked instead, and the human decides at the gate
    whether to write it.
    """
    by_slug, by_title, by_tail = target_index(targets)
    if not by_slug:
        return topic_map

    renamed: dict[str, str] = {}
    nodes: list[TopicNode] = []
    seen: set[str] = set()
    for node in topic_map.nodes():
        # The plan's own slug first: folders belong to a page, so when the model used
        # a name-shaped slug the path is where the page's real identity survives.
        target = target_for_name(node.slug, by_slug, by_title, by_tail) or target_for_name(
            page_slug_from_path(node.wiki_path), by_slug, by_title, by_tail
        )
        slug = node.slug if target is None else target.slug
        if slug in seen:
            # Two names bound to one page: the first one carries the material.
            continue
        seen.add(slug)
        if target is not None and slug != node.slug:
            renamed[node.slug] = slug
        nodes.append(replace(node, slug=slug, unlisted=target is None))

    slugs = {node.slug for node in nodes}
    edges = tuple(
        replace(
            edge,
            source=renamed.get(edge.source, edge.source),
            target=renamed.get(edge.target, edge.target),
        )
        for edge in topic_map.edges
    )
    edges = tuple(edge for edge in edges if edge.source in slugs and edge.target in slugs)

    primary, extras = _split_primary(nodes)
    return TopicMap(
        intent=topic_map.intent,
        primary=primary,
        also_in_sources=tuple(extras),
        notes=topic_map.notes,
        collection_summaries=topic_map.collection_summaries,
        candidates=tuple(nodes),
        edges=edges,
    )


def parse_topic_map(
    payload: str | dict[str, Any],
    *,
    intent: str,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
    layout: ProjectLayout | None = None,
) -> TopicMap:
    """
    Accept a JSON object or LLM text wrapping one. Bind ids to known collections.
    """
    data = payload if isinstance(payload, dict) else extract_json_object(payload)
    if not isinstance(data, dict):
        raise ValueError("topic map must be a JSON object")
    known_collections = {item.id: item for item in collections}
    known_blocks = {
        block.id for collection in collections for block in collection.blocks
    }
    for collection in collections:
        known_blocks.update(collection.block_ids)

    notes = data.get("notes") or ""
    if not isinstance(notes, str):
        notes = str(notes)
    notes = notes.strip()[:800]
    summaries = _parse_summaries(
        data.get("collectionSummaries") or data.get("collection_summaries"),
        collections,
    )
    raw_candidates = data.get("candidates")
    if isinstance(raw_candidates, list) and raw_candidates:
        nodes = _parse_candidate_list(
            raw_candidates,
            known_collections=known_collections,
            known_blocks=known_blocks,
        )
        nodes = _finalize_nodes(nodes, layout)
        if not nodes:
            raise ValueError("topic map has no candidates")
        primary, extras = _split_primary(nodes)
        return TopicMap(
            intent=(intent or "").strip(),
            primary=primary,
            also_in_sources=tuple(extras),
            notes=notes,
            collection_summaries=tuple(summaries),
            candidates=tuple(nodes),
            edges=resolve_topic_edges(
                data,
                nodes,
                known_collections=known_collections,
                known_blocks=known_blocks,
            ),
        )

    primary_raw = data.get("primary")
    if not isinstance(primary_raw, dict):
        raise ValueError("topic map is missing primary")
    primary = _parse_node(
        primary_raw,
        default_name=intent or "untitled",
        default_relation="primary",
        known_collections=known_collections,
        known_blocks=known_blocks,
        force_relation="primary",
        default_coverage="write",
    )
    also_raw = data.get("alsoInSources")
    if also_raw is None:
        also_raw = data.get("also_in_sources") or []
    if not isinstance(also_raw, list):
        raise ValueError("alsoInSources must be a list")
    extras: list[TopicNode] = []
    seen_slugs = {primary.slug}
    for item in also_raw[:MAX_ALSO]:
        if not isinstance(item, dict):
            continue
        node = _parse_node(
            item,
            default_name="",
            default_relation="cooccurs",
            known_collections=known_collections,
            known_blocks=known_blocks,
            required_relation=True,
            default_coverage="skip",
        )
        if not node.title:
            continue
        if node.slug in seen_slugs:
            continue
        seen_slugs.add(node.slug)
        extras.append(node)
    extras = _finalize_nodes([primary, *extras], layout)
    if not extras:
        raise ValueError("topic map is missing primary")
    primary = extras[0]
    rest = extras[1:]
    return TopicMap(
        intent=(intent or "").strip(),
        primary=primary,
        also_in_sources=tuple(rest),
        notes=notes,
        collection_summaries=tuple(summaries),
        candidates=tuple(extras),
        edges=resolve_topic_edges(
            data,
            extras,
            known_collections=known_collections,
            known_blocks=known_blocks,
        ),
    )


def map_clips(
    layout: ProjectLayout,
    *,
    intent: str | None = None,
    llm_config: LlmConfig | None = None,
    map_text: str | None = None,
    store: ClipStore | None = None,
    targets: Sequence[PlanTarget] | None = None,
    language: str = "",
    on_status: Callable[..., None] | None = None,
    run_logger: RunLogger | None = None,
    cancel: threading.Event | None = None,
) -> TopicMap:
    """
    Build a topic map from persisted clips. ``map_text`` injects fake LLM JSON.

    ``targets`` names the pages the pass is about, when the caller has them. They
    are given to the model and the answer is bound to them — the constraint travels
    with the request instead of being applied to the answer afterwards.
    """
    logger = run_logger or RunLogger.disabled()
    check_cancelled(cancel)
    clip_store = store if store is not None else load_clip_store(layout)
    if not clip_store.collections:
        raise Refusal("plan.noSelection", "no clip collections to map")
    require_durable_sources(layout, clip_store.collections)
    goal = clip_store.intent.strip() if intent is None else intent.strip()

    def status(message: str, **kwargs) -> None:
        logger.status(message, **kwargs)
        emit_status(on_status, message, **kwargs)

    catalog = format_collection_catalog(clip_store.collections)
    doc = SourceDocument(
        project_root=layout.root,
        identity="raw/clip-session",
        path=layout.meta / "clip-session.md",
        content=collections_as_source_text(clip_store.collections),
    )
    status("mapping clipped passages", step_id="plan")
    check_cancelled(cancel)
    if map_text is not None:
        raw = map_text
    else:
        prompt = build_topic_map_prompt(
            doc,
            layout,
            intent=goal,
            collection_catalog=catalog,
            targets=tuple(targets or ()),
            language=language_name(language),
        )
        client = LlmClient(llm_config or load_llm_config())
        raw = client.chat(prompt.as_messages(), cancel=cancel)
    check_cancelled(cancel)
    if not str(raw).strip():
        raise RuntimeError("topic map returned empty content")
    topic_map = parse_topic_map(
        raw,
        intent=goal,
        collections=clip_store.collections,
        layout=layout,
    )
    if targets:
        topic_map = resolve_map_to_targets(topic_map, targets)
    logger.append(
        "topic_map.end",
        intent=goal,
        also=len(topic_map.also_in_sources),
        candidates=len(topic_map.nodes()),
        unlisted=sum(1 for node in topic_map.nodes() if node.unlisted),
    )
    return topic_map


def merge_plan_board(
    existing: TopicMap,
    incoming: TopicMap,
    *,
    covered: Sequence[ClipCollection],
) -> TopicMap:
    """Folds a pass over part of the material into the board it was planned on.

    A pass answers about the material it was handed, so its answer is about that
    material's pages and nothing else. A page that material covers is replaced by the
    pass's version of it, or dropped when the pass no longer has it; a page the
    material has nothing to do with keeps its place, its order and its evidence. That
    is what makes re-planning one source a change to one source's pages.

    A page fed by two sources comes back as the pass's version of it, since the pass
    read only one of them; a pass over everything is what sees both again.
    """
    collection_ids = frozenset(item.id for item in covered)
    block_ids = frozenset(
        block_id
        for item in covered
        for block_id in (*item.block_ids, *(block.id for block in item.blocks))
    )
    answers = {node.slug: node for node in incoming.nodes()}

    nodes: list[TopicNode] = []
    seen: set[str] = set()
    for node in existing.nodes():
        if node.slug in seen:
            continue
        seen.add(node.slug)
        answer = answers.get(node.slug)
        if answer is not None:
            # The pass has the newest word on a page it named.
            nodes.append(answer)
        elif not _covers_page(node, collection_ids, block_ids):
            nodes.append(node)
        # Covered and unnamed: the material no longer says anything about it.
    for node in incoming.nodes():
        if node.slug in seen:
            continue
        seen.add(node.slug)
        nodes.append(node)

    slugs = {node.slug for node in nodes}
    edges: dict[tuple[str, str, str], TopicEdge] = {
        (edge.source, edge.target, edge.relation): edge for edge in existing.edges
    }
    for edge in incoming.edges:
        edges[(edge.source, edge.target, edge.relation)] = edge
    summaries = {item.id: item for item in existing.collection_summaries}
    for item in incoming.collection_summaries:
        summaries[item.id] = item

    primary, extras = _split_primary(nodes)
    return TopicMap(
        intent=incoming.intent or existing.intent,
        primary=primary,
        also_in_sources=tuple(extras),
        notes=incoming.notes or existing.notes,
        collection_summaries=tuple(summaries.values()),
        candidates=tuple(nodes),
        edges=tuple(
            edge
            for edge in edges.values()
            if edge.source in slugs and edge.target in slugs
        ),
    )


def _covers_page(
    node: TopicNode,
    collection_ids: frozenset[str],
    block_ids: frozenset[str],
) -> bool:
    """Whether a page is written from the material a pass was handed."""
    if any(item in collection_ids for item in node.collection_ids):
        return True
    if any(section.collection_id in collection_ids for section in node.outline):
        return True
    return any(item in block_ids for item in node.block_ids)


def carry_over_choices(
    choices: Sequence[GateDecision],
    topic_map: TopicMap | None,
) -> tuple[GateDecision, ...]:
    """The reader's earlier answers, for the pages the board still has.

    An action is the reader's own, so it outlives the plan it was answered against
    and wins over what a later pass suggests. Its sections are names for parts of the
    outline they were answered against, so a page whose outline changed keeps the
    sections still there, and falls back to the whole outline when none of them are.
    """
    if topic_map is None or not choices:
        return tuple(choices)
    out: list[GateDecision] = []
    seen: set[str] = set()
    for choice in choices:
        node = topic_map.node_for_slug(choice.slug)
        if node is None or choice.slug in seen:
            continue
        seen.add(choice.slug)
        allowed = [section.id for section in node.outline]
        kept = [section_id for section_id in choice.section_ids if section_id in allowed]
        if choice.section_ids and not kept:
            kept = allowed
        out.append(
            GateDecision(
                slug=choice.slug,
                action=choice.action,
                section_ids=tuple(kept),
            )
        )
    return tuple(out)


def _parse_node(
    raw: dict[str, Any],
    *,
    default_name: str,
    default_relation: str,
    known_collections: dict[str, ClipCollection],
    known_blocks: set[str],
    required_relation: bool = False,
    force_relation: str | None = None,
    default_coverage: str = "skip",
) -> TopicNode:
    # A model says `name`; this side says `title`. Both are read, one is written.
    name = str(raw.get("title") or raw.get("name") or default_name).strip()[:MAX_NAME]
    slug_raw = str(raw.get("slug") or "").strip()
    slug = page_slug(slug_raw or name)
    wiki_path = _safe_wiki_path(raw.get("wikiPath") or raw.get("wiki_path"), slug)
    relation = str(
        raw.get("relationToPrimary") or raw.get("relation_to_primary") or default_relation
    ).strip()
    if force_relation:
        relation = force_relation
    elif required_relation:
        if relation not in RELATIONS:
            relation = "cooccurs"
    elif relation not in NODE_RELATIONS:
        relation = default_relation if default_relation in NODE_RELATIONS else "cooccurs"
    collection_ids = _filter_ids(
        raw.get("collectionIds") or raw.get("collection_ids"),
        set(known_collections),
    )
    if collection_ids:
        allowed_blocks: set[str] = set()
        for cid in collection_ids:
            collection = known_collections.get(cid)
            if collection is not None:
                allowed_blocks.update(collection.block_ids)
    else:
        allowed_blocks = known_blocks
    block_ids = _filter_ids(raw.get("blockIds") or raw.get("block_ids"), allowed_blocks)
    if not block_ids:
        # Fall back to all blocks in named collections so evidence stays checkable.
        for cid in collection_ids:
            collection = known_collections.get(cid)
            if collection is None:
                continue
            for block_id in collection.block_ids:
                if block_id not in block_ids:
                    block_ids.append(block_id)
    evidence = str(raw.get("evidence") or "").strip()[:400]
    outline = _parse_outline(raw.get("outline"), known_collections)
    roles = _parse_collection_roles(raw, collection_ids)
    coverage = _infer_coverage(raw, roles, default_coverage)
    return TopicNode(
        title=name or slug,
        slug=slug,
        wiki_path=wiki_path,
        relation_to_primary=relation,
        collection_ids=tuple(collection_ids),
        block_ids=tuple(block_ids),
        evidence=evidence,
        outline=tuple(outline),
        coverage=coverage,
        collection_roles=roles,
        # Read back so a stored plan keeps what it said about its own pages.
        unlisted=bool(raw.get("unlisted")),
    )


def _parse_candidate_list(
    raw_candidates: list[object],
    *,
    known_collections: dict[str, ClipCollection],
    known_blocks: set[str],
) -> list[TopicNode]:
    nodes: list[TopicNode] = []
    seen_slugs: set[str] = set()
    for item in raw_candidates[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        node = _parse_node(
            item,
            default_name="",
            default_relation="cooccurs",
            known_collections=known_collections,
            known_blocks=known_blocks,
            default_coverage="skip",
        )
        if not node.title:
            continue
        if node.slug in seen_slugs:
            continue
        seen_slugs.add(node.slug)
        nodes.append(node)
    return nodes


def _split_primary(nodes: list[TopicNode]) -> tuple[TopicNode, list[TopicNode]]:
    """The head page (a write page when there is one) and the pages after it."""
    writes = [item for item in nodes if item.coverage == "write"]
    head = writes[0] if writes else nodes[0]
    extras = [item for item in nodes if item.slug != head.slug]
    return head, extras


def _enforce_write_atomic(nodes: list[TopicNode]) -> list[TopicNode]:
    claimed: set[str] = set()
    out: list[TopicNode] = []
    for node in nodes:
        if node.coverage != "write":
            out.append(node)
            continue
        keep_ids = [cid for cid in node.collection_ids if cid not in claimed]
        for cid in keep_ids:
            claimed.add(cid)
        if not keep_ids:
            out.append(
                replace(
                    node,
                    coverage="skip",
                    collection_ids=(),
                    collection_roles=(),
                    outline=(),
                )
            )
            continue
        keep_set = set(keep_ids)
        roles = tuple(
            (cid, role) for cid, role in node.collection_roles if cid in keep_set
        )
        outline = tuple(
            section
            for section in node.outline
            if not section.collection_id or section.collection_id in keep_set
        )
        out.append(
            replace(
                node,
                collection_ids=tuple(keep_ids),
                collection_roles=roles,
                outline=outline,
            )
        )
    return out


def hang_topic_map(topic_map: TopicMap, layout: ProjectLayout | None) -> TopicMap:
    """Re-pin ``wikiPath`` on an already-parsed map (compile path, saved JSON)."""
    if layout is None:
        return topic_map
    nodes = _hang_existing_wiki_paths(list(topic_map.nodes()), layout)
    nodes = _collapse_same_wiki_path(nodes)
    if not nodes:
        return topic_map
    primary, extras = _split_primary(nodes)
    return TopicMap(
        intent=topic_map.intent,
        primary=primary,
        also_in_sources=tuple(extras),
        notes=topic_map.notes,
        collection_summaries=topic_map.collection_summaries,
        candidates=tuple(nodes),
        edges=topic_map.edges,
    )


def _finalize_nodes(
    nodes: list[TopicNode], layout: ProjectLayout | None
) -> list[TopicNode]:
    nodes = _enforce_write_atomic(nodes)
    if layout is None:
        return nodes
    nodes = _hang_existing_wiki_paths(nodes, layout)
    return _collapse_same_wiki_path(nodes)


def _normalize_topic_title(name: str) -> str:
    return " ".join((name or "").casefold().split())


def _page_title_for_hang(relative: str, content: str) -> str:
    page = parse_page(content)
    if page.has_frontmatter:
        title = unquote_scalar(page.fields.get("title", "")).strip()
        if title:
            return title
    stem = relative.rsplit("/", 1)[-1]
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return stem.replace("-", " ").strip()


def _index_path_for_slug(slug: str) -> str | None:
    text = normalize_wikilink_target(slug)
    if not text or ".." in text.split("/"):
        return None
    path = f"wiki/{text}.md"
    if not is_citing_wiki_page(path):
        return None
    return path


@dataclass(frozen=True, slots=True)
class _WikiHangCatalog:
    by_path: dict[str, str]
    by_slug: dict[str, str]
    by_bare: dict[str, str]
    by_title: dict[str, str]


def _remember_hang(bucket: dict[str, str], key: str, path: str) -> None:
    if key and path and key not in bucket:
        bucket[key] = path


def load_wiki_hang_catalog(layout: ProjectLayout) -> _WikiHangCatalog:
    """Disk notes plus index titles. Clip summaries / nav pages are not hang targets."""
    by_path: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    by_bare: dict[str, str] = {}
    by_title: dict[str, str] = {}
    if layout.wiki.is_dir():
        root = layout.root.expanduser().resolve()
        for path in sorted(
            layout.wiki.rglob("*.md"), key=lambda item: item.as_posix().casefold()
        ):
            if not path.is_file():
                continue
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            if not is_citing_wiki_page(rel):
                continue
            try:
                slug = wiki_slug_for_path(rel)
            except ValueError:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            by_path[rel] = rel
            _remember_hang(by_slug, slug, rel)
            _remember_hang(by_bare, slug.split("/")[-1], rel)
            _remember_hang(
                by_title,
                _normalize_topic_title(_page_title_for_hang(rel, content)),
                rel,
            )

    if layout.wiki_index.is_file():
        try:
            index_text = layout.wiki_index.read_text(encoding="utf-8")
        except OSError:
            index_text = ""
        for raw_line in index_text.splitlines():
            match = _INDEX_ITEM.match(raw_line.strip())
            if match is None:
                continue
            index_path = _index_path_for_slug(match.group(1).strip())
            if index_path is None:
                continue
            _remember_hang(by_path, index_path, index_path)
            try:
                slug = wiki_slug_for_path(index_path)
            except ValueError:
                continue
            _remember_hang(by_slug, slug, index_path)
            _remember_hang(by_bare, slug.split("/")[-1], index_path)
            title = (match.group(3) or "").strip()
            _remember_hang(by_title, _normalize_topic_title(title), index_path)
    return _WikiHangCatalog(by_path, by_slug, by_bare, by_title)


def _hang_existing_wiki_paths(
    nodes: list[TopicNode], layout: ProjectLayout
) -> list[TopicNode]:
    catalog = load_wiki_hang_catalog(layout)
    out: list[TopicNode] = []
    for node in nodes:
        hung = _resolve_existing_wiki_path(node, catalog)
        if hung and hung != node.wiki_path:
            out.append(replace(node, wiki_path=hung))
        else:
            out.append(node)
    return out


def _resolve_existing_wiki_path(
    node: TopicNode, catalog: _WikiHangCatalog
) -> str | None:
    proposed = node.wiki_path.replace("\\", "/").strip()
    if proposed in catalog.by_path:
        return catalog.by_path[proposed]
    try:
        slug = wiki_slug_for_path(proposed) if proposed.startswith("wiki/") else ""
    except ValueError:
        slug = ""
    if slug and slug in catalog.by_slug:
        return catalog.by_slug[slug]
    bare = (node.slug or "").strip() or (slug.split("/")[-1] if slug else "")
    if bare and bare in catalog.by_bare:
        return catalog.by_bare[bare]
    title_key = _normalize_topic_title(node.title)
    if title_key and title_key in catalog.by_title:
        return catalog.by_title[title_key]
    return None


def _collapse_same_wiki_path(nodes: list[TopicNode]) -> list[TopicNode]:
    seen: set[str] = set()
    out: list[TopicNode] = []
    for node in nodes:
        key = node.wiki_path.replace("\\", "/").strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(node)
    return out


def _parse_collection_roles(
    raw: dict[str, Any], collection_ids: list[str]
) -> tuple[tuple[str, str], ...]:
    allowed = set(collection_ids)
    items = raw.get("collectionRoles") or raw.get("collection_roles")
    if not isinstance(items, list):
        return ()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("collectionId") or item.get("collection_id") or "").strip()
        role = str(item.get("role") or "").strip().lower()
        if cid not in allowed or role not in COLLECTION_ROLES or cid in seen:
            continue
        seen.add(cid)
        out.append((cid, role))
    return tuple(out)


def _infer_coverage(
    raw: dict[str, Any],
    roles: tuple[tuple[str, str], ...],
    default_coverage: str,
) -> str:
    explicit = str(raw.get("coverage") or "").strip().lower()
    if explicit in COVERAGES:
        return explicit
    role_names = {role for _cid, role in roles}
    if role_names & {"defines", "derives", "applies", "alias"}:
        return "write"
    if "mentions" in role_names:
        return "mention"
    return default_coverage if default_coverage in COVERAGES else "skip"


def _parse_outline(
    raw: object, known_collections: dict[str, ClipCollection]
) -> list[PageSection]:
    if not isinstance(raw, list):
        return []
    sections: list[PageSection] = []
    seen: set[str] = set()
    for index, item in enumerate(raw[:MAX_OUTLINE], start=1):
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or f"s{index}").strip()[:40] or f"s{index}"
        if ident in seen:
            continue
        title = str(item.get("title") or "").strip()[:MAX_NAME]
        if not title:
            continue
        collection_id = str(
            item.get("collectionId") or item.get("collection_id") or ""
        ).strip()
        if collection_id not in known_collections:
            collection_id = _infer_collection_id(
                item.get("blockIds") or item.get("block_ids"),
                known_collections,
            )
        if collection_id not in known_collections:
            continue
        allowed = set(known_collections[collection_id].block_ids)
        block_ids = _filter_ids(
            item.get("blockIds") or item.get("block_ids"), allowed
        )
        if not block_ids:
            block_ids = list(known_collections[collection_id].block_ids)
        role = str(item.get("role") or "").strip()
        if role not in SECTION_ROLES:
            role = "other"
        seen.add(ident)
        sections.append(
            PageSection(
                id=ident,
                title=title,
                block_ids=tuple(block_ids),
                collection_id=collection_id,
                role=role,
            )
        )
    return sections


def _infer_collection_id(
    raw_blocks: object, known_collections: dict[str, ClipCollection]
) -> str:
    if not isinstance(raw_blocks, list):
        return ""
    wanted = {
        str(item).strip()
        for item in raw_blocks
        if isinstance(item, str) and item.strip()
    }
    if not wanted:
        return ""
    matches = [
        cid
        for cid, collection in known_collections.items()
        if wanted <= set(collection.block_ids)
    ]
    if len(matches) == 1:
        return matches[0]
    return ""


def _parse_summaries(
    raw: object,
    collections: tuple[ClipCollection, ...] | list[ClipCollection],
) -> list[CollectionSummary]:
    by_id = {item.id: item for item in collections}
    found: dict[str, CollectionSummary] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id") or "").strip()
            if ident not in by_id:
                continue
            title = str(item.get("title") or "").strip()[:MAX_NAME]
            gist = str(item.get("gist") or "").strip()[:160]
            found[ident] = CollectionSummary(
                id=ident,
                title=title or collection_headline(by_id[ident]),
                gist=gist,
            )
    out: list[CollectionSummary] = []
    for collection in collections:
        existing = found.get(collection.id)
        if existing is not None:
            out.append(existing)
        else:
            out.append(
                CollectionSummary(
                    id=collection.id,
                    title=collection_headline(collection),
                    gist="",
                )
            )
    return out


def _filter_ids(raw: object, allowed: set[str]) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        ident = item.strip()
        if ident in allowed and ident not in out:
            out.append(ident)
    return out


def _safe_wiki_path(raw: object, slug: str) -> str:
    fallback = wiki_path_for_slug(slug)
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    path = raw.replace("\\", "/").strip().lstrip("/")
    if not path.endswith(".md"):
        path = path + ".md"
    if not path.startswith("wiki/"):
        return fallback
    if ".." in path.split("/"):
        return fallback
    return path


_PAYLOAD_KEYS = frozenset(
    {
        "primary",
        "contributions",
        "candidates",
        "collectionSummaries",
        "collection_summaries",
        "alsoInSources",
        "also_in_sources",
        "edges",
        "pages",
        "blocks",
    }
)


def extract_json_object(text: str) -> dict[str, Any]:
    """
    Pull the first JSON object out of an LLM reply.

    Do not slice from the first ``{`` to the last ``}``: clip text often
    contains set-notation like ``{ x 1 , ..., x n }``, which is not JSON and
    yields ``Expecting property name enclosed in double quotes`` at char 2.
    """
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw[: raw.rfind("```")]
        raw = raw.strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    fallback: dict[str, Any] | None = None
    index = 0
    while index < len(raw):
        start = raw.find("{", index)
        if start < 0:
            break
        try:
            data, _end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError as err:
            last_error = err
            index = start + 1
            continue
        if isinstance(data, dict):
            if _PAYLOAD_KEYS.intersection(data):
                return data
            fallback = data
        index = start + 1
    if fallback is not None:
        return fallback
    if last_error is not None:
        raise ValueError(f"topic map JSON is invalid: {last_error}") from last_error
    raise ValueError("topic map response did not contain JSON")


def star_edges_from_nodes(nodes: Sequence[TopicNode]) -> tuple[TopicEdge, ...]:
    """One typed edge from each satellite toward the primary page."""
    if not nodes:
        return ()
    primary = next(
        (item for item in nodes if item.relation_to_primary == "primary"),
        nodes[0],
    )
    edges: list[TopicEdge] = []
    for node in nodes:
        if node.slug == primary.slug:
            continue
        relation = node.relation_to_primary
        if relation not in RELATIONS:
            relation = "cooccurs"
        edges.append(
            TopicEdge(
                source=node.slug,
                target=primary.slug,
                relation=relation,
                evidence=node.evidence,
                collection_id=node.collection_ids[0] if node.collection_ids else "",
                block_ids=node.block_ids,
            )
        )
    return tuple(edges)


def resolve_topic_edges(
    data: dict[str, Any],
    nodes: Sequence[TopicNode],
    *,
    known_collections: dict[str, ClipCollection],
    known_blocks: set[str],
) -> tuple[TopicEdge, ...]:
    declared = _parse_edges(
        data.get("edges") or data.get("relations"),
        nodes,
        known_collections=known_collections,
        known_blocks=known_blocks,
    )
    if declared:
        return declared
    return star_edges_from_nodes(nodes)


def _parse_edges(
    raw: object,
    nodes: Sequence[TopicNode],
    *,
    known_collections: dict[str, ClipCollection],
    known_blocks: set[str],
) -> tuple[TopicEdge, ...]:
    if not isinstance(raw, list):
        return ()
    by_slug = {item.slug: item for item in nodes}
    by_path = {
        item.wiki_path.replace("\\", "/"): item
        for item in nodes
        if item.wiki_path
    }
    out: list[TopicEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw[: MAX_EDGES * 2]:
        if not isinstance(item, dict):
            continue
        source = _lookup_edge_slug(
            item.get("from") or item.get("source") or item.get("src"),
            by_slug,
            by_path,
        )
        target = _lookup_edge_slug(
            item.get("to") or item.get("target") or item.get("dst"),
            by_slug,
            by_path,
        )
        if not source or not target or source == target:
            continue
        relation = str(item.get("relation") or item.get("type") or "").strip()
        if relation not in RELATIONS:
            continue
        key = (source, target, relation)
        inverse = (target, source, INVERSE_RELATION.get(relation, relation))
        if key in seen or inverse in seen:
            continue
        collection_id = str(
            item.get("collectionId") or item.get("collection_id") or ""
        ).strip()
        if collection_id and collection_id not in known_collections:
            collection_id = ""
        block_ids = tuple(
            str(block).strip()
            for block in (item.get("blockIds") or item.get("block_ids") or [])
            if isinstance(block, str) and str(block).strip() in known_blocks
        )
        evidence = str(item.get("evidence") or "").strip()[:400]
        seen.add(key)
        out.append(
            TopicEdge(
                source=source,
                target=target,
                relation=relation,
                evidence=evidence,
                collection_id=collection_id,
                block_ids=block_ids,
            )
        )
        if len(out) >= MAX_EDGES:
            break
    return tuple(out)


def _lookup_edge_slug(
    raw: object,
    by_slug: dict[str, TopicNode],
    by_path: dict[str, TopicNode],
) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text in by_slug:
        return text
    if text.startswith("wiki/") and text.lower().endswith(".md"):
        node = by_path.get(text)
        return node.slug if node is not None else ""
    return ""


def _edge_to_dict(edge: TopicEdge) -> dict[str, Any]:
    return {
        "from": edge.source,
        "to": edge.target,
        "relation": edge.relation,
        "evidence": edge.evidence,
        "collectionId": edge.collection_id,
        "blockIds": list(edge.block_ids),
    }


def _node_to_dict(node: TopicNode) -> dict[str, Any]:
    """One proposal as JSON, in the same shape a round's proposal is written in."""
    return proposal_to_wire(node)


__all__ = [
    "COLLECTION_ROLES",
    "COVERAGES",
    "CollectionSummary",
    "INVERSE_RELATION",
    "MAX_CANDIDATES",
    "MAX_EDGES",
    "PageSection",
    "RELATIONS",
    "SECTION_ROLES",
    "TopicEdge",
    "TopicMap",
    "TopicNode",
    "carry_over_choices",
    "extract_json_object",
    "hang_topic_map",
    "map_clips",
    "merge_plan_board",
    "parse_topic_map",
    "resolve_topic_edges",
    "star_edges_from_nodes",
    "topic_slug",
]
