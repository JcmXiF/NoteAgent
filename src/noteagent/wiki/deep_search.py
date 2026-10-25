"""Deep Search round: Chew leftover then plan; Explore whitelist then fetch.

Does not write wiki, does not fetch originals, does not change GET /graph.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    collection_headline,
    decision_to_dict,
    decisions_from_raw,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.deep_search_store import (
    BLOCK_PREVIEW,
    BLOCKS_PER_PAGE,
    CARDS_PER_PAGE,
    GRAPH_EDGE_ROWS,
    GRAPH_NODE_ROWS,
    INDEX_CHARS,
    MAX_CARDS,
    MAX_OVERLAY_EDGES,
    MAX_ROUND_COLLECTIONS,
    MAX_WHITELIST,
    SKIM_FILES,
    SKIM_PER_FILE,
    SKIM_TOTAL,
    DeepSearchSeed,
    DeepSearchSession,
    ExploreCard,
    OverlayEdge,
    ProposedBlock,
    WhitelistPage,
    empty_session,
    load_session,
    save_session,
    session_to_dict,
)
from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.ingest.source_citations import review_clip_collections
from noteagent.ingest.topic_map import (
    PlanTarget,
    TopicMap,
    extract_json_object,
    page_slug,
    target_for_name,
    target_index,
    topic_slug,
)
from noteagent.ingest.update_index import iter_wiki_content_pages
from noteagent.llm.client import LlmClient, language_name
from noteagent.llm.config import load_llm_config
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import read_optional
from noteagent.prompts.deep_search import build_blocks_prompt, build_whitelist_prompt
from noteagent.util.cancel import check_cancelled
from noteagent.util.refusal import Refusal
from noteagent.wiki.graph import wiki_graph_payload
from noteagent.wiki.link_syntax import WikiLink, find_wikilinks
from noteagent.wiki.links import (
    normalize_wikilink_target,
    resolve_wikilink,
    wiki_slug_for_path,
    wiki_slug_maps,
)

BODY_SHORT_CHARS = 40
_NAV_TARGETS = frozenset({"index", "overview", "log"})


@dataclass(frozen=True, slots=True)
class GapProposal:
    """Ghost or thin page used as an Explore subgraph seed."""

    kind: str
    title: str
    slug: str
    query: str
    from_path: str
    from_slug: str
    #: Body length in characters, for a thin page; ``0`` for a ghost.
    body_chars: int = 0
    #: What the page that points here is called, for a reason that names both.
    from_title: str = ""


@dataclass(frozen=True, slots=True)
class GapReport:
    proposals: tuple[GapProposal, ...]
    omitted: int
    content_pages: int


def _report(on_status: Callable[..., None] | None, message: str) -> None:
    """Says what is about to be worked on, for a caller that is streaming."""
    if on_status is not None:
        on_status(message)


def get_session_payload(layout: ProjectLayout) -> dict[str, object]:
    session = load_session(layout)
    payload = session_to_dict(session)
    payload["potential"] = remainder_potential(layout)
    return payload


def act_session(
    layout: ProjectLayout,
    body: dict[str, Any],
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
    on_status: Callable[..., None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, object]:
    """Apply one action to the session and answer with it.

    ``on_status`` and ``cancel`` are for a caller that streams: the first is told
    what is being worked on, and the second is checked before each round of model
    work, so a caller that has gone away does not pay for another one. Both are
    optional, and the action itself behaves the same either way.
    """
    if "useLlm" in body:
        use_llm = bool(body.get("useLlm"))
    elif "use_llm" in body:
        use_llm = bool(body.get("use_llm"))
    action = str(body.get("action") or "").strip()
    if not action:
        raise ValueError("deep-search act needs action")
    if action == "clear":
        _abort_if_allowed(layout)
        save_session(layout, empty_session())
        return get_session_payload(layout)
    session = load_session(layout)
    if action == "start":
        session = _start(layout, body)
    elif action == "proposeWhitelist":
        session = _propose_whitelist(
            layout,
            session,
            script=script,
            use_llm=use_llm,
            language=str(body.get("language") or ""),
            on_status=on_status,
            cancel=cancel,
        )
    elif action == "togglePage":
        session = _toggle_page(session, str(body.get("slug") or ""), body.get("accepted"))
    elif action == "acceptWhitelist":
        session = _accept_whitelist(
            layout,
            session,
            script=script,
            use_llm=use_llm,
            on_status=on_status,
            cancel=cancel,
        )
    elif action == "proposeBlocks":
        session = _propose_blocks(
            layout, session, script=script, use_llm=use_llm, on_status=on_status, cancel=cancel
        )
    elif action == "toggleBlock":
        session = _toggle_block(session, str(body.get("locator") or ""), body.get("accepted"))
    elif action == "selectAllBlocks":
        session = _select_all_blocks(session, body.get("accepted"))
    elif action == "acceptBlocks":
        session = _accept_blocks(layout, session)
    elif action == "enterPlan":
        session = _enter_plan(layout, session)
    elif action == "toggleCard":
        session = _toggle_card(session, str(body.get("url") or ""), body.get("picked"))
    elif action == "redo":
        session = _redo(layout, session, int(body.get("step") or session.step))
    elif action == "markIngested":
        session = _mark_ingested(session, body.get("collectionIds") or body.get("collection_ids"))
    elif action == "restart":
        session = _restart(layout, session)
    elif action == "end":
        session = _end_round(layout, session, str(body.get("leftover") or ""))
    else:
        raise ValueError(
            "action must be start, proposeWhitelist, togglePage, acceptWhitelist, "
            "proposeBlocks, toggleBlock, selectAllBlocks, acceptBlocks, enterPlan, toggleCard, "
            "redo, restart, markIngested, end, or clear"
        )
    save_session(layout, session)
    return get_session_payload(layout)


def put_session_payload(layout: ProjectLayout, body: dict[str, object]) -> dict[str, object]:
    if body.get("clear"):
        return act_session(layout, {"action": "clear"})
    action = str(body.get("action") or "").strip()
    if action:
        return act_session(layout, body)
    raise ValueError("deep-search PUT needs action or clear")


def collections_for_planning(
    layout: ProjectLayout,
    store: ClipStore | None = None,
) -> tuple[ClipCollection, ...]:
    """Daily clips, or this round's collections once Deep Search is planning."""
    clip_store = store if store is not None else load_clip_store(layout)
    session = load_session(layout)
    if session.round_id and session.step >= 4:
        chosen = tuple(
            item for item in clip_store.collections if item.round_id == session.round_id
        )
        if not chosen:
            raise Refusal("search.noCollections", "this round has no collections to plan yet")
        return chosen
    return tuple(item for item in clip_store.collections if not item.round_id)


def plan_targets(
    layout: ProjectLayout,
    store: ClipStore | None = None,
) -> tuple[PlanTarget, ...] | None:
    """The pages a planning pass may write, or None when the pass names its own.

    Two sources, and the reader's own choices come first:

    1. What the material says: a selection can name the page it is meant for, which
       is how someone says "this passage is for that page" — including a page the
       round's list does not have, and including material outside any round.
    2. What the round approved: a search plans the pages the reader accepted before
       anything was read, so the plan works out what goes on each one.
    """
    clip_store = store if store is not None else load_clip_store(layout)
    chosen: list[PlanTarget] = []
    seen: set[str] = set()
    try:
        planning = collections_for_planning(layout, clip_store)
    except ValueError:
        planning = ()
    for collection in planning:
        slug = (collection.target_slug or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        chosen.append(PlanTarget(slug=slug, title=collection_headline(collection)))
    for target in round_targets(layout) or ():
        if target.slug in seen:
            continue
        seen.add(target.slug)
        chosen.append(target)
    return tuple(chosen) or None


def session_proposals(layout: ProjectLayout) -> tuple[WhitelistPage, ...]:
    """The pages the round has proposed, refined by its plan once there is one."""
    return load_session(layout).whitelist


def proposals_map(layout: ProjectLayout, *, intent: str = "") -> TopicMap | None:
    """A round's proposals as the plan-shaped value the write path already knows.

    Compiling a round reads its own proposals rather than a second plan kept
    elsewhere: a round has one list of pages, and this is it.
    """
    proposals = session_proposals(layout)
    if not proposals:
        return None
    nodes = list(proposals)
    writes = [item for item in nodes if item.coverage == "write"]
    primary = writes[0] if writes else nodes[0]
    return TopicMap(
        intent=intent,
        primary=primary,
        also_in_sources=tuple(item for item in nodes if item.slug != primary.slug),
        notes="",
        candidates=tuple(nodes),
    )


def refine_proposals(layout: ProjectLayout, topic_map: TopicMap) -> DeepSearchSession:
    """Folds a round's plan back into the proposals it was planned from.

    One list, two stages. When the round proposed pages before reading, the plan's
    version of a page replaces the round's for that page — coverage, sections,
    evidence, the path it will be written to — while the reader's own answer
    (`accepted`) is theirs and is kept; a page the plan did not mention stays on the
    list, since the reader approved it. When the round proposed none, the plan is
    where the pages come from and they *become* the round's list: a pass with no list
    of its own is one where the plan names them.
    """
    session = load_session(layout)
    if not session.round_id:
        return session
    by_slug = {item.slug: item for item in session.whitelist}
    refined: list[WhitelistPage] = []
    seen: set[str] = set()
    for node in topic_map.nodes():
        if node.slug in seen:
            continue
        seen.add(node.slug)
        drafted = by_slug.get(node.slug)
        refined.append(
            replace(
                node,
                accepted=drafted.accepted if drafted is not None else node.accepted,
                query=node.query or (drafted.query if drafted is not None else ""),
                section=node.section or (drafted.section if drafted is not None else ""),
                plan=node.plan or (drafted.plan if drafted is not None else ""),
                action=node.action or (drafted.action if drafted is not None else ""),
            )
        )
    for drafted in session.whitelist:
        if drafted.slug not in seen:
            refined.append(replace(drafted, coverage="skip"))
    next_session = replace(session, whitelist=tuple(refined))
    save_session(layout, next_session)
    return next_session


def round_is_planning(layout: ProjectLayout) -> bool:
    """Whether a round is at the step where a plan is drawn for it."""
    session = load_session(layout)
    return bool(session.round_id) and session.step >= 4


def round_targets(layout: ProjectLayout) -> tuple[PlanTarget, ...] | None:
    """The pages a search approved before it read anything, or None for other passes.

    A pass with no list is not an error: an open pass is one where the plan names the
    pages, and those become the round's list once it has run.
    """
    session = load_session(layout)
    if session.mode == "chew":
        return None
    if session.round_id and session.step >= 4 and session.whitelist:
        chosen = tuple(
            PlanTarget(slug=item.slug, title=item.title, action=item.action)
            for item in session.whitelist
            if item.slug and item.accepted
        )
        return chosen or None
    return None


def whitelist_slugs(layout: ProjectLayout) -> tuple[str, ...] | None:
    """The slugs of ``round_targets``, for callers that only need the names."""
    targets = round_targets(layout)
    return tuple(item.slug for item in targets) if targets else None


def remainder_potential(layout: ProjectLayout) -> dict[str, Any]:
    files = list(_iter_raw_files(layout))
    leftover_files: list[dict[str, object]] = []
    remainder_blocks = 0
    low_files = 0
    for rel in files:
        try:
            blocks = _source_blocks(layout, rel)
        except ValueError:
            continue
        if not blocks:
            continue
        clipped = _clipped_ids(layout, rel)
        leftover = [item for item in blocks if str(item.get("id") or "") not in clipped]
        if not leftover:
            continue
        remainder_blocks += len(leftover)
        ratio = (len(blocks) - len(leftover)) / len(blocks)
        leftover_files.append(
            {
                "path": rel,
                "leftover": len(leftover),
                "total": len(blocks),
            }
        )
        if ratio >= 0.8:
            low_files += 1
    leftover_files.sort(key=lambda item: (-int(item["leftover"]), str(item["path"])))
    source_files = len(files)
    chew_disabled = remainder_blocks == 0
    low_potential = (
        (not chew_disabled)
        and leftover_files
        and low_files == len(leftover_files)
    )
    return {
        "sourceFiles": source_files,
        "filesWithRemainder": len(leftover_files),
        "remainderBlocks": remainder_blocks,
        "topFiles": leftover_files[:3],
        "chewDisabled": chew_disabled,
        "lowPotential": low_potential,
    }


def propose_wiki_gaps(layout: ProjectLayout) -> GapReport:
    """Scan content pages for missing wikilinks and thin bodies."""
    slug_to_rel, bare_to_rel = wiki_slug_maps(layout)
    pages = iter_wiki_content_pages(layout)
    ghosts: list[GapProposal] = []
    thin: list[GapProposal] = []
    seen_ghost: set[str] = set()
    seen_thin: set[str] = set()

    for rel, content in pages:
        try:
            from_slug = wiki_slug_for_path(rel)
        except ValueError:
            continue
        page = parse_page(content)
        title = _page_title(page.fields, from_slug)

        if len(page.body.strip()) < BODY_SHORT_CHARS and from_slug not in seen_thin:
            seen_thin.add(from_slug)
            thin.append(
                GapProposal(
                    kind="thin",
                    title=title,
                    slug=from_slug,
                    query=_search_query(title),
                    from_path=rel,
                    from_slug=from_slug,
                    body_chars=len(page.body.strip()),
                )
            )

        for link in find_wikilinks(content):
            target = normalize_wikilink_target(link.target)
            if not target or _is_nav_target(target):
                continue
            if resolve_wikilink(target, slug_to_rel, bare_to_rel):
                continue
            if target in seen_ghost:
                continue
            seen_ghost.add(target)
            label = _display_label(link)
            ghosts.append(
                GapProposal(
                    kind="ghost",
                    title=label,
                    slug=target,
                    query=_search_query(label),
                    from_path=rel,
                    from_slug=from_slug,
                    from_title=title,
                )
            )

    ranked = ghosts + thin
    cap = MAX_WHITELIST * 4
    return GapReport(
        proposals=tuple(ranked[:cap]),
        omitted=max(0, len(ranked) - cap),
        content_pages=len(pages),
    )


def graph_summary_text(
    layout: ProjectLayout, *, seed_slug: str = ""
) -> tuple[str, bool, set[str]]:
    """The graph as a prompt shows it, and which pages it shows.

    The pages come back with the text because they decide things about the answer:
    whether a proposed page is new, and whether a proposed link can be drawn. Both are
    judged against the graph the model was given — this text, cut down by the row caps
    and, when there is a seed, to one neighbourhood — and not against the project,
    which holds pages that went into no request at all.
    """
    payload = wiki_graph_payload(layout)
    nodes = list(payload["nodes"])
    edges = list(payload["edges"])
    if seed_slug:
        keep = _subgraph_ids(payload, seed_slug)
        nodes = [item for item in nodes if str(item.get("id") or "") in keep]
        edges = [
            item
            for item in edges
            if isinstance(item, dict)
            and str(item.get("source") or "") in keep
            and str(item.get("target") or "") in keep
        ]
    node_rows: list[str] = []
    shown: set[str] = set()
    omitted_nodes = 0
    for item in nodes:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("id") or "")
        if len(node_rows) >= GRAPH_NODE_ROWS:
            omitted_nodes += 1
            continue
        title = str(item.get("title") or slug)
        marks = []
        if item.get("ghost"):
            marks.append("ghost")
        rel = str(item.get("path") or "")
        if rel and _is_thin_page(layout, rel):
            marks.append("thin")
        mark = ",".join(marks) or "page"
        node_rows.append(f"N {slug} | {title} | {mark} | deg={item.get('degree') or 0}")
        if slug:
            shown.add(slug)
    edge_rows: list[str] = []
    omitted_edges = 0
    for item in edges:
        if not isinstance(item, dict):
            continue
        if len(edge_rows) >= GRAPH_EDGE_ROWS:
            omitted_edges += 1
            continue
        edge_rows.append(f"E {item.get('source')} -> {item.get('target')}")
    truncated = omitted_nodes > 0 or omitted_edges > 0
    lines = node_rows + edge_rows
    if truncated:
        lines.append(f"omitted nodes={omitted_nodes} edges={omitted_edges}")
    return "\n".join(lines), truncated, shown


def remainder_skim_text(layout: ProjectLayout) -> tuple[str, bool]:
    files = remainder_potential(layout)["topFiles"]
    extra = [
        item
        for item in _remainder_file_stats(layout)
        if item["path"] not in {str(row["path"]) for row in files}
    ]
    ranked = list(files) + extra
    chunks: list[str] = []
    used = 0
    truncated = False
    for row in ranked[:SKIM_FILES]:
        rel = str(row["path"])
        leftover = _leftover_blocks(layout, rel)
        parts: list[str] = []
        file_chars = 0
        for item in leftover:
            text = " ".join(str(item.get("preview") or item.get("text") or "").split())
            if not text:
                continue
            piece = f"[{item.get('id')}] {text}"
            if file_chars + len(piece) > SKIM_PER_FILE:
                truncated = True
                break
            parts.append(piece)
            file_chars += len(piece) + 1
        body = "\n".join(parts)
        if not body:
            continue
        header = f"## {rel} leftover={len(leftover)}"
        block = f"{header}\n{body}"
        if used + len(block) > SKIM_TOTAL:
            truncated = True
            break
        chunks.append(block)
        used += len(block)
    return "\n\n".join(chunks), truncated


def leftover_block_previews(layout: ProjectLayout) -> tuple[str, bool]:
    buckets: list[list[str]] = []
    for rel in _iter_raw_files(layout):
        try:
            leftover = _leftover_blocks(layout, rel)
        except Exception:
            continue
        rows: list[str] = []
        for item in leftover:
            block_id = str(item.get("id") or "")
            if not block_id:
                continue
            preview = " ".join(str(item.get("preview") or item.get("text") or "").split())[
                :BLOCK_PREVIEW
            ]
            rows.append(f"{rel}#{block_id}\t{preview}")
        if rows:
            buckets.append(rows)
    lines: list[str] = []
    truncated = False
    while True:
        progressed = False
        for bucket in buckets:
            if not bucket:
                continue
            candidate = bucket.pop(0)
            if len("\n".join(lines + [candidate])) > SKIM_TOTAL:
                truncated = True
                return "\n".join(lines), True
            lines.append(candidate)
            progressed = True
        if not progressed:
            break
    return "\n".join(lines), truncated


def _start(layout: ProjectLayout, body: dict[str, Any]) -> DeepSearchSession:
    current = load_session(layout)
    if current.round_id and current.step:
        raise Refusal("search.running", "a Deep Search round is already running; end it first")
    mode = str(body.get("mode") or "").strip()
    if mode not in {"chew", "explore"}:
        raise ValueError("mode must be chew or explore")
    if mode == "chew":
        potential = remainder_potential(layout)
        if potential["chewDisabled"]:
            raise Refusal("search.nothingLeft", "no unclipped blocks left to chew")
    seed = _seed_from_body(body.get("seed"))
    if mode == "explore" and seed is not None:
        if seed.slug:
            seed = replace(seed, path="", kind="graph-node")
        else:
            seed = None
    intent = " ".join(str(body.get("intent") or "").split())[:200]
    if mode == "chew":
        return DeepSearchSession(
            round_id=uuid.uuid4().hex,
            mode=mode,
            step=3,
            intent=intent,
            seed=seed,
            notice="step 3: propose the unclipped blocks",
        )
    return DeepSearchSession(
        round_id=uuid.uuid4().hex,
        mode=mode,
        step=2,
        intent=intent,
        seed=seed,
        notice="step 2: propose the pages for this round",
    )


def _propose_whitelist(
    layout: ProjectLayout,
    session: DeepSearchSession,
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
    language: str = "",
    on_status: Callable[..., None] | None = None,
    cancel: threading.Event | None = None,
) -> DeepSearchSession:
    if session.mode == "chew":
        raise Refusal(
            "search.chewFirst",
            "chew reads the unclipped blocks first; it does not guess pages",
        )
    _require(session, mode_needed=True, step=2)
    truncated = False
    pages: tuple[WhitelistPage, ...]
    edges: tuple[OverlayEdge, ...]
    omitted = 0
    notice = ""
    if script is not None:
        # A script stands in for the model, and it is not an answer to anything the
        # prompt carried, so what it proposes is judged against the whole graph.
        pages, edges, omitted, truncated = _pages_from_payload(
            script, existing=_graph_node_ids(layout)
        )
        notice = "pages came from an injected script"
    elif use_llm:
        # The round about to start is the last chance to stop: a model call cannot
        # be interrupted once it is in flight, so a caller that has gone away is
        # noticed here instead.
        _report(on_status, "deep-search: proposing pages")
        check_cancelled(cancel)
        try:
            pages, edges, omitted, truncated, notice = _pages_from_llm(
                layout, session, language=language
            )
        except Exception as err:
            pages, edges, omitted, truncated = _fallback_whitelist(layout, session)
            notice = f"model unavailable; fell back to the graph summary ({err})"[:400]
    else:
        pages, edges, omitted, truncated = _fallback_whitelist(layout, session)
        notice = "pages came from the graph summary fallback"
    return replace(
        session,
        whitelist=pages,
        whitelist_omitted=omitted,
        overlay_edges=edges,
        proposed_blocks=(),
        cards=(),
        cards_omitted=0,
        truncated=truncated,
        notice=notice or "pages proposed; accept or untick them",
    )


def _toggle_page(session: DeepSearchSession, slug: str, accepted: object) -> DeepSearchSession:
    _require(session, step=2)
    if not slug:
        raise ValueError("togglePage needs slug")
    flag = bool(accepted) if accepted is not None else None
    pages = []
    for item in session.whitelist:
        if item.slug != slug:
            pages.append(item)
            continue
        pages.append(replace(item, accepted=(not item.accepted) if flag is None else flag))
    return replace(session, whitelist=tuple(pages))


def _accept_whitelist(
    layout: ProjectLayout,
    session: DeepSearchSession,
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
    on_status: Callable[..., None] | None = None,
    cancel: threading.Event | None = None,
) -> DeepSearchSession:
    _require(session, step=2)
    kept = tuple(item for item in session.whitelist if item.accepted)
    if not kept:
        raise Refusal("search.needPage", "accept at least one page")
    kept_slugs = {page.slug for page in kept}
    edges = _keep_overlay_edges(
        session.overlay_edges,
        whitelist_slugs=kept_slugs,
        graph_ids=_graph_node_ids(layout),
    )
    next_session = replace(
        session,
        whitelist=kept[:MAX_WHITELIST],
        overlay_edges=edges,
        step=3,
        proposed_blocks=(),
        cards=(),
        notice="step 3: attach the material",
    )
    if session.mode == "explore":
        # Searching for material is the other long round; the same check applies
        # before it starts.
        _report(on_status, "deep-search: searching for material")
        check_cancelled(cancel)
        return _search_cards(
            layout, next_session, script=script, use_search=use_llm
        )
    return next_session


def _propose_blocks(
    layout: ProjectLayout,
    session: DeepSearchSession,
    *,
    script: dict[str, Any] | None = None,
    use_llm: bool = True,
    on_status: Callable[..., None] | None = None,
    cancel: threading.Event | None = None,
) -> DeepSearchSession:
    _require(session, mode="chew", step=3)
    if script is not None:
        blocks, truncated = _blocks_from_payload(script)
        notice = "blocks came from an injected script"
    elif use_llm:
        _report(on_status, "deep-search: collecting blocks")
        check_cancelled(cancel)
        try:
            blocks, truncated, notice = _blocks_from_llm(layout, session)
        except Exception as err:
            blocks, truncated = _fallback_blocks(layout, session)
            notice = f"model unavailable; fell back to the unclipped blocks ({err})"[:400]
    else:
        blocks, truncated = _fallback_blocks(layout, session)
        notice = "blocks came from the unclipped-block fallback"
    blocks, extra_cut = _restrict_proposed_blocks(layout, session, blocks)
    truncated = truncated or extra_cut
    if not blocks and script is None and use_llm:
        blocks, extra_cut = _fallback_blocks(layout, session)
        blocks, extra_cut = _restrict_proposed_blocks(layout, session, blocks)
        truncated = truncated or extra_cut
        notice = "the model marked no unclipped blocks; fell back to what is left"
    if not blocks:
        notice = "no unclipped blocks to propose"
    elif truncated:
        notice = (notice or "unclipped blocks marked") + (
            f" (at most {BLOCKS_PER_PAGE} per file; trimmed)"
        )
    return replace(
        session,
        proposed_blocks=blocks,
        truncated=session.truncated or truncated,
        notice=(notice or "tick the blocks to take into this round")[:400],
    )


def _toggle_block(session: DeepSearchSession, locator: str, accepted: object) -> DeepSearchSession:
    _require(session, mode="chew", step=3)
    if not locator:
        raise ValueError("toggleBlock needs locator")
    flag = bool(accepted) if accepted is not None else None
    blocks = []
    for item in session.proposed_blocks:
        if item.locator != locator:
            blocks.append(item)
            continue
        blocks.append(replace(item, accepted=(not item.accepted) if flag is None else flag))
    return replace(session, proposed_blocks=tuple(blocks))


def _select_all_blocks(session: DeepSearchSession, accepted: object) -> DeepSearchSession:
    _require(session, mode="chew", step=3)
    if not session.proposed_blocks:
        raise Refusal("search.nothingToPick", "no blocks to tick yet")
    flag = True if accepted is None else bool(accepted)
    return replace(
        session,
        proposed_blocks=tuple(replace(item, accepted=flag) for item in session.proposed_blocks),
    )


def _accept_blocks(layout: ProjectLayout, session: DeepSearchSession) -> DeepSearchSession:
    _require(session, mode="chew", step=3)
    accepted = [item for item in session.proposed_blocks if item.accepted]
    if not accepted:
        raise Refusal("search.nothingPicked", "tick at least one block first")
    _, collections = _collections_from_blocks(layout, session, accepted)
    if not collections:
        raise Refusal("search.cannotCollect", "the ticked blocks cannot become collections")
    next_session = replace(
        session,
        whitelist=(),
        overlay_edges=(),
        proposed_blocks=(),
        collection_ids=tuple(item.id for item in collections),
        step=4,
        notice="step 4: plan this round's collections in the side panel",
    )
    return _stash_daily_plan(layout, next_session)


def _enter_plan(layout: ProjectLayout, session: DeepSearchSession) -> DeepSearchSession:
    _require(session, step=3)
    store = load_clip_store(layout)
    round_cols = [item for item in store.collections if item.round_id == session.round_id]
    if not round_cols:
        raise Refusal("search.noAcceptedBlocks", "this round has no accepted material blocks yet")
    # What a selection says it is for does not narrow the round's list. It is a
    # choice about a page, and it reaches planning as a target of its own — narrowing
    # here could leave the list empty, which reads downstream as "no list at all".
    next_session = replace(
        session,
        collection_ids=tuple(item.id for item in round_cols),
        step=4,
        notice="step 4: plan this round's collections in the side panel",
    )
    return _stash_daily_plan(layout, next_session)


def _toggle_card(session: DeepSearchSession, url: str, picked: object) -> DeepSearchSession:
    _require(session, mode="explore", step=3)
    if not url:
        raise ValueError("toggleCard needs url")
    flag = bool(picked) if picked is not None else None
    cards = []
    for item in session.cards:
        if item.url != url:
            cards.append(item)
            continue
        cards.append(replace(item, picked=(not item.picked) if flag is None else flag))
    return replace(session, cards=tuple(cards))


def _search_cards(
    layout: ProjectLayout,
    session: DeepSearchSession,
    *,
    script: dict[str, Any] | None = None,
    use_search: bool = True,
) -> DeepSearchSession:
    del layout
    if script is not None and script.get("cards") is not None:
        cards, omitted, truncated = _cards_from_payload(script, session)
        notice = "cards came from an injected script"
    elif not use_search:
        cards, omitted, truncated = (), 0, False
        notice = "no search engine was called"
    else:
        cards, omitted, truncated, notice = _cards_from_web_search(session)
    if omitted:
        notice = f"{notice} (cards trimmed: {omitted})"
    return replace(
        session,
        cards=cards,
        cards_omitted=omitted,
        truncated=session.truncated or truncated or omitted > 0,
        notice=notice[:400],
    )


def _cards_from_web_search(
    session: DeepSearchSession,
) -> tuple[tuple[ExploreCard, ...], int, bool, str]:
    # Imported here: the search stack pulls in the page extractors, which a round
    # that never searches should not pay for.
    from noteagent.acquire.run_search import run_web_search

    cards: list[ExploreCard] = []
    omitted = 0
    notice_parts: list[str] = []
    truncated = False
    seen: set[str] = set()
    pages = list(session.whitelist)
    for index, page in enumerate(pages):
        if len(cards) >= MAX_CARDS:
            omitted += len(pages) - index
            truncated = True
            break
        query = page.query.strip() or _search_query(page.title or page.slug)
        if not query:
            continue
        remaining = min(CARDS_PER_PAGE, MAX_CARDS - len(cards))
        try:
            run = run_web_search(query, top_k=remaining + 4, caller="deep_search")
        except Exception as err:
            notice_parts.append(f"[[{page.slug}]] search failed: {err}"[:160])
            truncated = True
            continue
        added = 0
        for card in run.cards:
            if card.url in seen:
                continue
            if added >= CARDS_PER_PAGE or len(cards) >= MAX_CARDS:
                omitted += 1
                truncated = True
                continue
            seen.add(card.url)
            cards.append(
                ExploreCard(
                    page_slug=page.slug,
                    title=card.title,
                    url=card.url,
                    snippet=" ".join((card.snippet or "").split())[:280],
                    site=card.site_name or card.site,
                    picked=False,
                )
            )
            added += 1
    notice = (
        "；".join(notice_parts)
        if notice_parts
        else "cards are attached to their pages; fetch the ones you pick from the browse page"
    )
    return tuple(cards), omitted, truncated, notice


def _redo(layout: ProjectLayout, session: DeepSearchSession, step: int) -> DeepSearchSession:
    _require(session, mode_needed=True)
    if session.mode == "chew" and step <= 2:
        step = 3
    if step < 2 or step > session.step:
        raise Refusal(
            "search.pastStepsOnly",
            "only the current or an earlier unfinished step can be redone",
        )
    if step <= 2:
        _drop_round_collections(layout, session.round_id, session.collection_ids)
        return replace(
            session,
            step=2,
            whitelist=(),
            whitelist_omitted=0,
            overlay_edges=(),
            proposed_blocks=(),
            cards=(),
            cards_omitted=0,
            collection_ids=(),
            ingested_ids=(),
            truncated=False,
            notice="redid step 2",
        )
    if step == 3:
        _drop_round_collections(layout, session.round_id, session.collection_ids)
        return replace(
            session,
            step=3,
            proposed_blocks=(),
            cards=() if session.mode == "chew" else session.cards,
            collection_ids=(),
            ingested_ids=(),
            notice="redid step 3",
        )
    if step == 4:
        _restore_daily_plan(layout, session)
        return replace(
            session,
            step=4,
            notice="redid the plan (this round's collections are still here)",
            stashed_intent=session.stashed_intent,
            stashed_topic_map=session.stashed_topic_map,
            stashed_decisions=session.stashed_decisions,
        )
    raise Refusal(
        "search.cannotUndo", "step 5 cannot be undone by redoing; it has been written"
    )


def _restart(layout: ProjectLayout, session: DeepSearchSession) -> DeepSearchSession:
    if not session.round_id:
        return empty_session()
    if session.ingested_ids or session.step >= 5:
        raise Refusal(
            "search.alreadyWritten",
            "already written into the wiki, so the round cannot start over",
        )
    store = load_clip_store(layout)
    kept = tuple(item for item in store.collections if item.round_id != session.round_id)
    _restore_daily_plan(layout, session, collections=kept)
    return empty_session()


def _mark_ingested(session: DeepSearchSession, raw_ids: object) -> DeepSearchSession:
    _require(session, step=4)
    extra: list[str] = []
    if isinstance(raw_ids, list):
        extra = [str(item).strip() for item in raw_ids if str(item).strip()]
    else:
        extra = list(session.collection_ids)
    ingested = list(session.ingested_ids)
    for ident in extra:
        if ident in session.collection_ids and ident not in ingested:
            ingested.append(ident)
    return replace(
        session,
        ingested_ids=tuple(ingested),
        step=5,
        notice="written. Settle the collections that were not written before ending",
    )


def _end_round(
    layout: ProjectLayout, session: DeepSearchSession, leftover: str
) -> DeepSearchSession:
    if not session.round_id:
        return empty_session()
    store = load_clip_store(layout)
    round_items = [item for item in store.collections if item.round_id == session.round_id]
    ingested = set(session.ingested_ids)
    pending = [item for item in round_items if item.id not in ingested]
    leftover_mode = leftover.strip()
    if pending and leftover_mode not in {"merge", "discard"}:
        raise Refusal(
            "search.unsettled",
            "this round still has unwritten collections: merge or discard them before ending",
            params={"count": str(len(pending))},
        )
    kept: list[ClipCollection] = []
    for item in store.collections:
        if item.round_id != session.round_id:
            kept.append(item)
            continue
        if item.id in ingested:
            continue
        if leftover_mode == "discard":
            continue
        kept.append(replace(item, round_id="", target_slug=""))
    _restore_daily_plan(
        layout,
        session,
        collections=tuple(kept),
    )
    return empty_session()


def _abort_if_allowed(layout: ProjectLayout) -> None:
    session = load_session(layout)
    if not session.round_id:
        return
    store = load_clip_store(layout)
    if any(item.round_id == session.round_id for item in store.collections):
        raise Refusal(
            "search.hasCollections",
            "this round has collections; end it instead (merge or discard)",
        )
    if session.step >= 4:
        raise Refusal("search.planStarted", "planning has started; use end round")


def _stash_daily_plan(layout: ProjectLayout, session: DeepSearchSession) -> DeepSearchSession:
    store = load_clip_store(layout)
    if session.stashed_topic_map is not None or session.stashed_decisions is not None:
        return session
    return replace(
        session,
        stashed_intent=store.intent,
        stashed_topic_map=store.topic_map,
        stashed_decisions=tuple(decision_to_dict(item) for item in store.decisions),
    )


def _restore_daily_plan(
    layout: ProjectLayout,
    session: DeepSearchSession,
    *,
    collections: tuple[ClipCollection, ...] | None = None,
) -> None:
    store = load_clip_store(layout)
    kept = collections if collections is not None else store.collections
    if session.stashed_decisions is None and session.stashed_topic_map is None:
        save_clip_store(layout, replace(store, collections=kept))
        return
    restored = replace(
        store,
        intent=session.stashed_intent,
        collections=kept,
        topic_map=session.stashed_topic_map,
        decisions=decisions_from_raw(list(session.stashed_decisions or ())),
    )
    save_clip_store(layout, restored)


def _drop_round_collections(
    layout: ProjectLayout, round_id: str, ids: tuple[str, ...]
) -> None:
    if not round_id:
        return
    store = load_clip_store(layout)
    drop = set(ids)
    kept = tuple(
        item
        for item in store.collections
        if item.round_id != round_id or (drop and item.id not in drop)
    )
    if len(kept) == len(store.collections):
        if not drop:
            kept = tuple(item for item in store.collections if item.round_id != round_id)
        else:
            return
    save_clip_store(layout, replace(store, collections=kept))


def _collections_from_blocks(
    layout: ProjectLayout,
    session: DeepSearchSession,
    accepted: list[ProposedBlock],
) -> tuple[tuple[WhitelistPage, ...], tuple[ClipCollection, ...]]:
    grouped: dict[str, list[ProposedBlock]] = {}
    for item in accepted:
        path, _, block_id = item.locator.partition("#")
        if not path.startswith("raw/") or not block_id:
            continue
        grouped.setdefault(path, []).append(item)
    existing = load_clip_store(layout)
    collections: list[ClipCollection] = []
    for path, items in grouped.items():
        if len(collections) >= MAX_ROUND_COLLECTIONS:
            break
        block_ids = []
        for item in items:
            bid = item.locator.split("#", 1)[-1]
            if bid not in block_ids:
                block_ids.append(bid)
        derived = _derived_for(layout, path, block_ids)
        if not derived:
            continue
        ident = f"ds-{session.round_id[:8]}-{len(collections) + 1}"
        collections.append(
            ClipCollection(
                id=ident,
                source_key=path,
                source_label=path.rsplit("/", 1)[-1],
                block_ids=tuple(block_ids),
                blocks=derived,
                round_id=session.round_id,
                target_slug="",
            )
        )
    if not collections:
        return (), ()
    merged = list(existing.collections) + collections
    save_clip_store(
        layout,
        # The round's own intent belongs to the round, which keeps it in its own
        # file; the project's document keeps the project's.
        replace(existing, collections=tuple(merged)),
    )
    return (), tuple(collections)


def _derived_for(
    layout: ProjectLayout, path: str, block_ids: list[str]
) -> tuple[DerivedBlock, ...]:
    by_id = {str(item.get("id") or ""): item for item in _source_blocks(layout, path)}
    out: list[DerivedBlock] = []
    for bid in block_ids:
        raw = by_id.get(bid)
        if raw is None:
            continue
        text = str(raw.get("text") or raw.get("preview") or "")
        preview = " ".join(str(raw.get("preview") or text).split())[:BLOCK_PREVIEW]
        out.append(
            DerivedBlock(
                id=bid,
                tag=str(raw.get("tag") or "p"),
                text=text,
                preview=preview,
            )
        )
    return tuple(out)


def _pages_from_llm(
    layout: ProjectLayout, session: DeepSearchSession, language: str = ""
) -> tuple[tuple[WhitelistPage, ...], tuple[OverlayEdge, ...], int, bool, str]:
    graph, graph_cut, shown = graph_summary_text(
        layout, seed_slug=session.seed.slug if session.seed else ""
    )
    index = read_optional(layout.wiki_index, INDEX_CHARS)
    skim, skim_cut = ("", False)
    if session.mode == "chew":
        skim, skim_cut = remainder_skim_text(layout)
    seed_line = ""
    if session.seed:
        seed_line = (
            f"{session.seed.kind} {session.seed.slug or session.seed.path} "
            f"{session.seed.title}"
        ).strip()
    prompt = build_whitelist_prompt(
        mode=session.mode,
        intent=session.intent,
        seed_line=seed_line,
        graph_summary=graph,
        index_text=index,
        remainder_skim=skim,
        language=language_name(language),
    )
    raw = LlmClient(load_llm_config()).chat(prompt.as_messages())
    pages, edges, omitted, parsed_cut = _pages_from_payload(
        extract_json_object(raw), existing=shown
    )
    truncated = graph_cut or skim_cut or parsed_cut
    notice = "pages proposed"
    if truncated:
        notice += " (working set trimmed)"
    return pages, edges, omitted, truncated, notice


def _blocks_from_llm(
    layout: ProjectLayout, session: DeepSearchSession
) -> tuple[tuple[ProposedBlock, ...], bool, str]:
    table = read_optional(layout.wiki_index, INDEX_CHARS)
    previews, cut = leftover_block_previews(layout)
    prompt = build_blocks_prompt(
        intent=session.intent,
        wiki_titles=table,
        remainder_previews=previews,
    )
    raw = LlmClient(load_llm_config()).chat(prompt.as_messages())
    blocks, parsed_cut = _blocks_from_payload(extract_json_object(raw))
    truncated = cut or parsed_cut
    notice = "unclipped blocks marked"
    if truncated:
        notice += " (previews trimmed)"
    return blocks, truncated, notice


def _pages_from_payload(
    data: dict[str, Any], *, existing: set[str]
) -> tuple[tuple[WhitelistPage, ...], tuple[OverlayEdge, ...], int, bool]:
    """Read a round's proposed pages and links out of a payload.

    ``existing`` is which pages the payload may be judged against, and it is handed in
    rather than read from the project because of where the payload came from: an answer
    to a prompt may only be weighed against what that prompt carried, since the graph
    it carried is capped and, for a seeded round, is one neighbourhood of the project.
    A payload the project itself produced — an injected script, the graph fallback — is
    weighed against the whole graph.
    """
    raw_pages = data.get("pages") or data.get("whitelist") or []
    if not isinstance(raw_pages, list):
        raise ValueError("pages must be a list")
    pages: list[WhitelistPage] = []
    seen: set[str] = set()
    for item in raw_pages:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        slug = str(item.get("slug") or "").strip() or page_slug(title)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        action = str(item.get("action") or "").strip()
        if action not in {"add", "revise"}:
            action = "revise" if slug in existing else "add"
        query = str(item.get("query") or "").strip() or _search_query(title or slug)
        pages.append(
            WhitelistPage(
                slug=slug[:160],
                title=(title or slug.split("/")[-1])[:160],
                action=action,
                plan=str(item.get("plan") or "").strip()[:400],
                query=query[:200],
                section=str(item.get("section") or "").strip()[:120],
                accepted=True,
            )
        )
    omitted = max(0, len(pages) - MAX_WHITELIST)
    pages = pages[:MAX_WHITELIST]
    slugs = {item.slug for item in pages}
    collected: list[OverlayEdge] = []
    raw_edges = data.get("edges") or []
    if isinstance(raw_edges, list):
        for item in raw_edges:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or item.get("from") or "").strip()
            target = str(item.get("target") or item.get("to") or "").strip()
            if not source or not target or source == target:
                continue
            collected.append(
                OverlayEdge(
                    source=source[:160],
                    target=target[:160],
                    relation=str(item.get("relation") or "").strip()[:40],
                )
            )
    edges = _keep_overlay_edges(
        collected,
        whitelist_slugs=slugs,
        graph_ids=existing,
    )
    truncated = bool(data.get("truncated")) or omitted > 0
    return tuple(pages), tuple(edges), omitted, truncated


def _blocks_from_payload(
    data: dict[str, Any],
) -> tuple[tuple[ProposedBlock, ...], bool]:
    raw_blocks = data.get("blocks") or []
    if not isinstance(raw_blocks, list):
        raise ValueError("blocks must be a list")
    out: list[ProposedBlock] = []
    per_page: dict[str, int] = {}
    seen: set[str] = set()
    truncated = False
    for item in raw_blocks:
        if not isinstance(item, dict):
            continue
        locator = str(item.get("locator") or "").strip()
        slug = str(item.get("pageSlug") or item.get("page_slug") or "").strip()
        if not locator or not slug or locator in seen:
            continue
        if per_page.get(slug, 0) >= BLOCKS_PER_PAGE:
            truncated = True
            continue
        seen.add(locator)
        per_page[slug] = per_page.get(slug, 0) + 1
        preview = " ".join(str(item.get("preview") or "").split())[:BLOCK_PREVIEW]
        out.append(
            ProposedBlock(
                page_slug=slug,
                locator=locator,
                preview=preview,
                accepted=False,
            )
        )
    return tuple(out), bool(data.get("truncated")) or truncated


def _cards_from_payload(
    data: dict[str, Any], session: DeepSearchSession
) -> tuple[tuple[ExploreCard, ...], int, bool]:
    raw_cards = data.get("cards") or []
    if not isinstance(raw_cards, list):
        raise ValueError("cards must be a list")
    allowed = {page.slug for page in session.whitelist}
    out: list[ExploreCard] = []
    per_page: dict[str, int] = {}
    seen: set[str] = set()
    omitted = 0
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        slug = str(item.get("pageSlug") or item.get("page_slug") or "").strip()
        if not url or not slug or slug not in allowed or url in seen:
            continue
        if per_page.get(slug, 0) >= CARDS_PER_PAGE or len(out) >= MAX_CARDS:
            omitted += 1
            continue
        seen.add(url)
        per_page[slug] = per_page.get(slug, 0) + 1
        out.append(
            ExploreCard(
                page_slug=slug[:160],
                title=(str(item.get("title") or "").strip() or url)[:200],
                url=url[:500],
                snippet=" ".join(str(item.get("snippet") or "").split())[:280],
                site=str(item.get("site") or "").strip()[:120],
                picked=False,
            )
        )
    return tuple(out), omitted, omitted > 0 or bool(data.get("truncated"))


def _fallback_whitelist(
    layout: ProjectLayout, session: DeepSearchSession
) -> tuple[tuple[WhitelistPage, ...], tuple[OverlayEdge, ...], int, bool]:
    report = propose_wiki_gaps(layout)
    payload = wiki_graph_payload(layout)
    graph_ids = {str(item.get("id") or "") for item in payload.get("nodes") or []}
    seed_slug = session.seed.slug if session.seed else ""
    subgraph = _subgraph_ids(payload, seed_slug) if seed_slug else set()
    pages: list[WhitelistPage] = []
    seen: set[str] = set()

    def add_page(item: GapProposal, action: str) -> None:
        if item.slug in seen:
            return
        seen.add(item.slug)
        # A gap the graph worked out is said in figures rather than in a sentence: this
        # path runs when no model is available, and a sentence written here would be in
        # the core's own language whatever the reader is using.
        # Pages are named by their names, not by their slugs: a reason is read by a
        # person, and `sources/kl-clips` is a path.
        if item.kind == "ghost":
            reason, args = (
                "dangling-link",
                (("from", item.from_title or item.from_slug), ("to", item.title or item.slug)),
            )
        else:
            reason, args = (
                "thin-page",
                (
                    ("page", item.title or item.slug),
                    ("chars", str(item.body_chars)),
                    ("floor", str(BODY_SHORT_CHARS)),
                ),
            )
        pages.append(
            WhitelistPage(
                slug=item.slug,
                title=item.title,
                action=action,
                reason=reason,
                reason_args=args,
                query=item.query,
                accepted=True,
            )
        )

    for item in report.proposals:
        if item.kind == "ghost":
            add_page(item, "add")
        elif item.kind == "thin":
            if not subgraph or item.slug in subgraph or not seed_slug:
                add_page(item, "revise")
        if len(pages) >= MAX_WHITELIST:
            break
    if seed_slug and seed_slug not in seen:
        title = session.seed.title if session.seed else seed_slug
        nodes = {str(item["id"]): item for item in payload["nodes"]}
        node = nodes.get(seed_slug)
        if node is not None:
            action = "add" if node.get("ghost") else "revise"
            pages.insert(
                0,
                WhitelistPage(
                    slug=seed_slug,
                    title=str(node.get("title") or title),
                    action=action,
                    reason="seed",
                    query=_search_query(str(node.get("title") or title)),
                    accepted=True,
                ),
            )
            seen.add(seed_slug)
    if session.mode == "chew" and session.seed and session.seed.path.startswith("raw/"):
        name = session.seed.title or session.seed.path.rsplit("/", 1)[-1]
        slug = page_slug(name)
        if slug not in seen and len(pages) < MAX_WHITELIST:
            pages.append(
                WhitelistPage(
                    slug=slug,
                    title=name,
                    action="add",
                    reason="unclipped-survey",
                    query=_search_query(name),
                    accepted=True,
                )
            )
    pages = pages[:MAX_WHITELIST]
    slugs = {item.slug for item in pages}
    collected: list[OverlayEdge] = []
    for item in report.proposals:
        if item.kind != "ghost" or item.slug not in slugs or not item.from_slug:
            continue
        collected.append(
            OverlayEdge(source=item.from_slug, target=item.slug, relation="proposed")
        )
    edges = _keep_overlay_edges(
        collected,
        whitelist_slugs=slugs,
        graph_ids=graph_ids,
    )
    omitted = max(0, len(report.proposals) - len(pages))
    return tuple(pages), tuple(edges), omitted, omitted > 0


def _fallback_blocks(
    layout: ProjectLayout, session: DeepSearchSession
) -> tuple[tuple[ProposedBlock, ...], bool]:
    out: list[ProposedBlock] = []
    per_page: dict[str, int] = {}
    truncated = False
    for rel in _iter_raw_files(layout):
        try:
            leftover = _leftover_blocks(layout, rel)
        except Exception:
            continue
        for item in leftover:
            block_id = str(item.get("id") or "")
            preview = " ".join(str(item.get("preview") or item.get("text") or "").split())
            if not block_id or not preview:
                continue
            locator = f"{rel}#{block_id}"
            if rel not in per_page and len(per_page) >= MAX_WHITELIST:
                truncated = True
                continue
            if per_page.get(rel, 0) >= BLOCKS_PER_PAGE:
                truncated = True
                continue
            per_page[rel] = per_page.get(rel, 0) + 1
            out.append(
                ProposedBlock(
                    page_slug="",
                    locator=locator,
                    preview=preview[:BLOCK_PREVIEW],
                    accepted=False,
                )
            )
    return tuple(out), truncated


def _is_leftover_locator(layout: ProjectLayout, path: str, block_id: str) -> bool:
    if not path.startswith("raw/") or not block_id:
        return False
    try:
        leftover = _leftover_blocks(layout, path)
    except ValueError:
        return False
    return any(str(item.get("id") or "") == block_id for item in leftover)


def _canonical_whitelist_slug(session: DeepSearchSession, slug: str) -> str:
    """The round's own page for a name the model used.

    The same matching the plan is bound with: a name that fits one page is that page,
    and one that fits several is left alone rather than guessed at.
    """
    targets = tuple(
        PlanTarget(slug=page.slug, title=page.title)
        for page in session.whitelist
        if page.slug
    )
    hit = target_for_name(slug, *target_index(targets))
    return hit.slug if hit is not None else ""


def _canonical_leftover_locator(layout: ProjectLayout, locator: str) -> str:
    locator = locator.replace("\\", "/").strip()
    path, _, block_id = locator.partition("#")
    if _is_leftover_locator(layout, path, block_id):
        return f"{path}#{block_id}"
    if not block_id:
        return ""
    name = path.rsplit("/", 1)[-1]
    hits: list[str] = []
    for rel in _iter_raw_files(layout):
        if name and rel.rsplit("/", 1)[-1] != name:
            continue
        if _is_leftover_locator(layout, rel, block_id):
            hits.append(f"{rel}#{block_id}")
    if len(hits) == 1:
        return hits[0]
    return ""


def _wiki_attach_slug(layout: ProjectLayout, slug: str) -> str:
    text = (slug or "").strip()
    if not text:
        return ""
    payload = wiki_graph_payload(layout)
    by_id = {
        str(item.get("id") or ""): str(item.get("title") or "")
        for item in payload.get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    if text in by_id:
        return text
    bare = text.split("/")[-1]
    hits = [
        ident
        for ident, title in by_id.items()
        if ident.split("/")[-1] == bare or title == text or title == bare
    ]
    if len(hits) == 1:
        return hits[0]
    return ""


def _match_preview_to_wiki(layout: ProjectLayout, preview: str) -> str:
    hay = preview.casefold()
    payload = wiki_graph_payload(layout)
    hits: list[str] = []
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ident = str(node.get("id") or "")
        title = str(node.get("title") or "")
        tokens = [part for part in (title, ident.split("/")[-1]) if part and len(part) >= 2]
        if any(token.casefold() in hay or hay[:12] in token.casefold() for token in tokens):
            hits.append(ident)
    if len(hits) == 1:
        return hits[0]
    return ""


def _cluster_slug(
    layout: ProjectLayout,
    session: DeepSearchSession,
    suggested: str,
    locator: str,
    preview: str,
) -> str:
    if session.whitelist:
        hit = _canonical_whitelist_slug(session, suggested)
        if hit:
            return hit
    attached = _wiki_attach_slug(layout, suggested)
    if attached:
        return attached
    if suggested.strip():
        return page_slug(suggested) or suggested.strip()[:80]
    matched = _match_preview_to_wiki(layout, preview)
    if matched:
        return matched
    path = locator.partition("#")[0]
    return topic_slug(path.rsplit("/", 1)[-1]) or "leftover"


def _restrict_proposed_blocks(
    layout: ProjectLayout,
    session: DeepSearchSession,
    blocks: tuple[ProposedBlock, ...],
) -> tuple[tuple[ProposedBlock, ...], bool]:
    out: list[ProposedBlock] = []
    per_page: dict[str, int] = {}
    seen: set[str] = set()
    truncated = False
    for item in blocks:
        locator = _canonical_leftover_locator(layout, item.locator)
        if not locator or locator in seen:
            continue
        path = locator.partition("#")[0]
        if session.mode == "chew":
            key = path
            page_slug = ""
        else:
            key = _cluster_slug(layout, session, item.page_slug, locator, item.preview)
            page_slug = key
        if key not in per_page and len(per_page) >= MAX_WHITELIST:
            truncated = True
            continue
        if per_page.get(key, 0) >= BLOCKS_PER_PAGE:
            truncated = True
            continue
        seen.add(locator)
        per_page[key] = per_page.get(key, 0) + 1
        out.append(replace(item, page_slug=page_slug, locator=locator))
    return tuple(out), truncated


def _graph_node_ids(layout: ProjectLayout) -> set[str]:
    payload = wiki_graph_payload(layout)
    return {str(item.get("id") or "") for item in payload["nodes"]}


def _keep_overlay_edges(
    edges: tuple[OverlayEdge, ...] | list[OverlayEdge],
    *,
    whitelist_slugs: set[str],
    graph_ids: set[str],
) -> tuple[OverlayEdge, ...]:
    """Keep dashed overlay links that touch the whitelist and can be drawn on C."""
    drawable = whitelist_slugs | graph_ids
    out: list[OverlayEdge] = []
    seen: set[tuple[str, str]] = set()
    for item in edges:
        source = item.source.strip()
        target = item.target.strip()
        if not source or not target or source == target:
            continue
        key = (source, target)
        if key in seen:
            continue
        if source not in drawable or target not in drawable:
            continue
        if source not in whitelist_slugs and target not in whitelist_slugs:
            continue
        seen.add(key)
        out.append(
            OverlayEdge(
                source=source[:160],
                target=target[:160],
                relation=item.relation[:40],
            )
        )
        if len(out) >= MAX_OVERLAY_EDGES:
            break
    return tuple(out)


def _subgraph_ids(payload: dict[str, Any], seed_slug: str) -> set[str]:
    """The seed page and the pages it links to or is linked from, by node id."""
    if not seed_slug:
        return set()
    keep = {seed_slug}
    for edge in payload.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source == seed_slug:
            keep.add(target)
        if target == seed_slug:
            keep.add(source)
    return keep


def _require(
    session: DeepSearchSession,
    *,
    mode: str | None = None,
    mode_needed: bool = False,
    step: int | None = None,
) -> None:
    if not session.round_id:
        raise Refusal("search.none", "no Deep Search round in progress")
    if mode_needed and session.mode not in {"chew", "explore"}:
        raise Refusal("search.noMode", "this round has no mode yet")
    if mode and session.mode != mode:
        raise Refusal("search.otherMode", f"this step is only for {mode}", params={"mode": mode})
    if step is not None and session.step != step:
        raise Refusal(
            "search.wrongStep",
            f"the round is at step {session.step}, so a step {step} action is too late",
            params={"current": str(session.step), "asked": str(step)},
        )


def _seed_from_body(raw: object) -> DeepSearchSeed | None:
    if not isinstance(raw, dict):
        return None
    seed = DeepSearchSeed(
        kind=str(raw.get("kind") or "").strip()[:40],
        path=str(raw.get("path") or "").strip().replace("\\", "/")[:500],
        slug=str(raw.get("slug") or "").strip()[:160],
        title=str(raw.get("title") or "").strip()[:160],
    )
    if not (seed.path or seed.slug):
        return None
    return seed


def _remainder_file_stats(layout: ProjectLayout) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rel in _iter_raw_files(layout):
        try:
            blocks = _source_blocks(layout, rel)
        except ValueError:
            continue
        leftover = [
            item
            for item in blocks
            if str(item.get("id") or "") not in _clipped_ids(layout, rel)
        ]
        if leftover:
            rows.append({"path": rel, "leftover": len(leftover), "total": len(blocks)})
    rows.sort(key=lambda item: (-int(item["leftover"]), str(item["path"])))
    return rows


def _leftover_blocks(layout: ProjectLayout, rel: str) -> list[dict[str, object]]:
    clipped = _clipped_ids(layout, rel)
    return [
        item
        for item in _source_blocks(layout, rel)
        if str(item.get("id") or "") not in clipped
    ]


def _iter_raw_files(layout: ProjectLayout) -> list[str]:
    root = layout.raw
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        out.append(path.relative_to(layout.root).as_posix())
        if len(out) >= 80:
            break
    return out


def _is_thin_page(layout: ProjectLayout, rel: str) -> bool:
    if not rel:
        return False
    path = layout.root / rel
    if not path.is_file():
        return False
    try:
        page = parse_page(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return len(page.body.strip()) < BODY_SHORT_CHARS


def _clipped_ids(layout: ProjectLayout, source_key: str) -> set[str]:
    key = source_key.replace("\\", "/").strip()
    out: set[str] = set()
    for collection in review_clip_collections(layout):
        source = collection.source_key.replace("\\", "/").strip().split("#", 1)[0]
        if source != key:
            continue
        out.update(collection.block_ids)
    return out


def _source_blocks(layout: ProjectLayout, relative: str) -> list[dict[str, object]]:
    # Imported here: the reader lives in the HTTP layer and brings the document
    # extractors with it, which a wiki-only round does not need.
    from noteagent.api.files import read_project_text

    try:
        payload = read_project_text(layout, relative)
    except FileNotFoundError as err:
        raise ValueError(str(err)) from err
    raw_blocks = payload.get("blocks")
    if not isinstance(raw_blocks, list):
        return []
    out: list[dict[str, object]] = []
    for item in raw_blocks:
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
    return out


def _page_title(fields: dict[str, str], slug: str) -> str:
    raw = fields.get("title") or ""
    title = unquote_scalar(str(raw)).strip()
    return title or slug.split("/")[-1]


def _search_query(label: str) -> str:
    return " ".join(label.replace("-", " ").replace("_", " ").split())


def _display_label(link: WikiLink) -> str:
    """What a ghost page should be titled: its alias, else its last segment."""
    alias = (link.label or "").strip()
    if alias:
        return alias
    return link.target.split("/")[-1]


def _is_nav_target(target: str) -> bool:
    return target.split("/")[-1].lower() in _NAV_TARGETS
