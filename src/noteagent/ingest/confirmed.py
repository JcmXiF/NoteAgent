"""Generate wiki pages only for topics the human confirmed."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any

from noteagent.ingest.auto_ingest import (
    INGEST_LOCK,
    IngestResult,
    StatusFn,
    hard_write_failure,
    is_program_owned_page,
)
from noteagent.ingest.clip_ledger import render_clip_ledger_page
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    load_clip_store,
    locators_for_blocks,
    locators_for_refs,
    require_durable_sources,
)
from noteagent.ingest.ecg import logic_links_for_page
from noteagent.ingest.fingerprint import (
    durable_identities_from_collections,
    record_confirmed_fingerprints,
)
from noteagent.ingest.frontmatter import (
    format_inline_list,
    parse_inline_list,
    parse_page,
    set_frontmatter_scalar,
    union_preserve_order,
)
from noteagent.ingest.ingest_recovery import (
    complete_confirmed_job,
    mark_recovery_error,
    remember_confirmed_job,
)
from noteagent.ingest.page_agents import PageStructureError, write_confirmed_page
from noteagent.ingest.page_evidence import page_passages
from noteagent.ingest.parse_file_blocks import (
    FileBlock,
    ParseFileBlocksResult,
    parse_file_blocks,
)
from noteagent.ingest.topic_map import (
    TopicMap,
    TopicNode,
    hang_topic_map,
    node_for_reference,
    page_slug,
    parse_topic_map,
    resolve_map_to_targets,
    topic_slug,
    wiki_path_for_slug,
)
from noteagent.ingest.update_index import update_wiki_index
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    WriteFileBlocksResult,
    WriteWarning,
    write_file_blocks,
)
from noteagent.llm.client import LlmClient
from noteagent.llm.config import LlmConfig, load_llm_config
from noteagent.project.doctor import (
    doctor_after_write,
    doctor_payload,
    session_unknown_clip_warnings,
)
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.context import read_brief
from noteagent.prompts.generation import current_wiki_date
from noteagent.prompts.merge import build_page_merger, build_page_repairer
from noteagent.util.cancel import JobCancelled, check_cancelled
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger, emit_status
from noteagent.wiki.deep_search import (
    collections_for_planning,
    plan_targets,
    proposals_map,
)

ACTIONS = frozenset({"write", "skip", "mention"})


@dataclass(frozen=True, slots=True)
class ConfirmedPage:
    name: str
    slug: str
    wiki_path: str
    action: str
    section_ids: tuple[str, ...]


def confirmed_from_dict(raw: dict[str, Any], topic_map: TopicMap) -> ConfirmedPage:
    action = str(raw.get("action") or "skip").strip()
    if action not in ACTIONS:
        raise ValueError(f"unknown confirm action: {action}")
    reference = str(raw.get("slug") or raw.get("name") or "").strip()
    raw_path = str(raw.get("wikiPath") or raw.get("wiki_path") or "").strip()
    # The page is looked up the way it was referred to, so a page whose name lost
    # its folders on the way through the plan is still the page that was meant.
    node = node_for_reference(topic_map, slug=reference, wiki_path=raw_path)
    slug = node.slug if node is not None else page_slug(reference)
    name = str(raw.get("name") or (node.title if node else slug)).strip()
    if node is not None:
        wiki_path = node.wiki_path
    else:
        wiki_path = raw_path
    if action == "write":
        if not wiki_path:
            wiki_path = wiki_path_for_slug(slug)
        wiki_path = wiki_path.replace("\\", "/").strip()
        if not wiki_path.startswith("wiki/") or ".." in wiki_path.split("/"):
            raise ValueError(f"unsafe wikiPath: {wiki_path}")
    section_raw = raw.get("sectionIds") or raw.get("section_ids") or []
    if not isinstance(section_raw, list):
        raise ValueError("sectionIds must be a list")
    section_ids = tuple(
        str(item).strip() for item in section_raw if isinstance(item, str) and item.strip()
    )
    return ConfirmedPage(
        name=name,
        slug=slug or page_slug(name),
        wiki_path=wiki_path,
        action=action,
        section_ids=section_ids,
    )


def ingest_confirmed(
    layout: ProjectLayout,
    *,
    confirmed: list[dict[str, Any]] | list[ConfirmedPage],
    intent: str | None = None,
    topic_map: TopicMap | dict[str, Any] | None = None,
    llm_config: LlmConfig | None = None,
    generation_text: str | None = None,
    agent_scripts: dict[str, dict[str, object]] | None = None,
    dry_run: bool = False,
    on_status: StatusFn | None = None,
    run_logger: RunLogger | None = None,
    store: ClipStore | None = None,
    cancel: threading.Event | None = None,
) -> IngestResult:
    """
    Generation and write for human-confirmed pages only. Records source
    fingerprints after a successful write; does not skip compile on cache hit.
    """
    logger = run_logger or RunLogger.disabled()

    def status(message: str, **kwargs) -> None:
        logger.status(message, **kwargs)
        emit_status(on_status, message, **kwargs)

    clip_store = store if store is not None else load_clip_store(layout)
    if clip_store.collections:
        clip_store = replace(
            clip_store, collections=collections_for_planning(layout, clip_store)
        )
    if not clip_store.collections:
        raise Refusal("compile.nothingSelected", "no clip collections to ingest")
    require_durable_sources(layout, clip_store.collections)

    raw_map = topic_map if topic_map is not None else clip_store.topic_map
    goal = (intent if intent is not None else clip_store.intent).strip()
    # A search carries its own list of pages: the one it proposed and its plan
    # refined. Everything else is planned into the project's own plan.
    round_map = proposals_map(layout, intent=goal)
    if round_map is not None and topic_map is None:
        raw_map = round_map
    if raw_map is None:
        raise Refusal(
            "compile.needPlan", "topic map is required; plan this round before compiling"
        )
    targets = plan_targets(layout, clip_store)
    parsed_map = (
        hang_topic_map(raw_map, layout)
        if isinstance(raw_map, TopicMap)
        else parse_topic_map(
            raw_map, intent=goal, collections=clip_store.collections, layout=layout
        )
    )
    if targets:
        # The same binding the planning pass made, from the stored plan: a page
        # named as the reader's own is written under that name.
        parsed_map = resolve_map_to_targets(parsed_map, targets)
    if not goal:
        goal = (parsed_map.intent or parsed_map.primary.title).strip() or "clip-session"

    pages: list[ConfirmedPage] = []
    for item in confirmed:
        if isinstance(item, ConfirmedPage):
            pages.append(item)
        elif isinstance(item, dict):
            pages.append(confirmed_from_dict(item, parsed_map))
        else:
            raise ValueError("confirmed pages must be objects")
    writes = [item for item in pages if item.action == "write"]
    if writes:
        # What may be written is what this pass planned: the pages it was given,
        # plus the ones reading turned up. Approving them is the human's, which
        # already happened above; this only checks a write has a page behind it.
        planned = {node.slug for node in parsed_map.nodes()}
        extra = [item.slug for item in writes if item.slug not in planned]
        if extra:
            raise Refusal(
                "compile.notInPlan",
                "pages not in this plan: " + ", ".join(extra[:8]),
                params={"pages": ", ".join(extra[:8])},
            )
    if not writes:
        raise Refusal(
            "compile.nothingTicked", "select at least one page to write this round"
        )

    with INGEST_LOCK:
        return _ingest_confirmed_unlocked(
            layout,
            clip_store=clip_store,
            topic_map=parsed_map,
            writes=writes,
            pages=pages,
            goal=goal,
            llm_config=llm_config,
            generation_text=generation_text,
            agent_scripts=agent_scripts,
            dry_run=dry_run,
            status=status,
            run_logger=logger,
            cancel=cancel,
        )


def _ingest_confirmed_unlocked(
    layout: ProjectLayout,
    *,
    clip_store: ClipStore,
    topic_map: TopicMap,
    writes: list[ConfirmedPage],
    pages: list[ConfirmedPage],
    goal: str,
    llm_config: LlmConfig | None,
    generation_text: str | None,
    agent_scripts: dict[str, dict[str, object]] | None,
    dry_run: bool,
    status: StatusFn,
    run_logger: RunLogger,
    cancel: threading.Event | None,
) -> IngestResult:
    collections = clip_store.collections
    summary_path = _clip_summary_path(goal)
    source_identities = durable_identities_from_collections(collections)
    identity = ", ".join(source_identities)
    mention_names = [item.name for item in pages if item.action == "mention"]
    skip_names = [item.name for item in pages if item.action == "skip"]
    action_by_slug = {item.slug: item.action for item in pages}
    all_refs: list[tuple[str, str]] = []
    for page in writes:
        all_refs.extend(
            _refs_for_page(page, topic_map.node_for_slug(page.slug), collections)
        )
    locators = locators_for_refs(collections, all_refs)
    if not locators:
        locators = locators_for_blocks(
            collections,
            {bid for col in collections for bid in col.block_ids},
        )

    run_logger.append(
        "ingest_confirmed.start",
        intent=goal,
        write_pages=[item.wiki_path for item in writes],
        dry_run=dry_run,
    )
    check_cancelled(cancel)
    job_id = ""
    if not dry_run:
        job_id = remember_confirmed_job(
            layout,
            intent=goal,
            confirmed=[
                {
                    "name": item.name,
                    "slug": item.slug,
                    "wikiPath": item.wiki_path,
                    "action": item.action,
                    "sectionIds": list(item.section_ids),
                }
                for item in pages
            ],
            write_paths=[item.wiki_path for item in writes],
        )

    try:
        return _ingest_confirmed_write(
            layout,
            topic_map=topic_map,
            writes=writes,
            goal=goal,
            llm_config=llm_config,
            generation_text=generation_text,
            agent_scripts=agent_scripts,
            dry_run=dry_run,
            status=status,
            run_logger=run_logger,
            cancel=cancel,
            collections=collections,
            summary_path=summary_path,
            identity=identity,
            mention_names=mention_names,
            skip_names=skip_names,
            action_by_slug=action_by_slug,
            locators=locators,
            job_id=job_id,
        )
    except JobCancelled:
        raise
    except Exception as err:
        if job_id:
            mark_recovery_error(layout, job_id, str(err))
        raise


def _ingest_confirmed_write(
    layout: ProjectLayout,
    *,
    topic_map: TopicMap,
    writes: list[ConfirmedPage],
    goal: str,
    llm_config: LlmConfig | None,
    generation_text: str | None,
    agent_scripts: dict[str, dict[str, object]] | None,
    dry_run: bool,
    status: StatusFn,
    run_logger: RunLogger,
    cancel: threading.Event | None,
    collections: tuple[ClipCollection, ...],
    summary_path: str,
    identity: str,
    mention_names: list[str],
    skip_names: list[str],
    action_by_slug: dict[str, str],
    locators: list[str],
    job_id: str,
) -> IngestResult:
    client: LlmClient | None = None

    def ensure_client() -> LlmClient:
        nonlocal client
        if client is None:
            client = LlmClient(llm_config or load_llm_config())
        return client

    extra_warnings: list[str] = []
    page_skips: list[WriteWarning] = []
    blocks: list[FileBlock] = []
    # page path -> the sources that page was written from, so the merge can tell
    # a page it may rewrite from one whose existing material is not all here.
    page_material: dict[str, frozenset[str]] = {}
    generation_parts: list[str] = []

    if generation_text is not None:
        status(f"generating confirmed wiki pages for {goal}", step_id="compose")
        generation_parts.append(generation_text)
        parsed = parse_file_blocks(generation_text)
        extra_warnings.extend(parsed.warnings)
        allowed = {item.wiki_path.replace("\\", "/") for item in writes}
        allowed.add(summary_path)
        allowed.add("wiki/log.md")
        for block in parsed.blocks:
            path = block.path.replace("\\", "/")
            if is_program_owned_page(path):
                extra_warnings.append(
                    f'Ignored model-generated "{path}"; '
                    "the catalog is rebuilt from the pages on disk."
                )
                continue
            if path not in allowed:
                extra_warnings.append(
                    f'Ignored unconfirmed FILE "{path}"; only gated pages are written.'
                )
                continue
            if path == summary_path:
                blocks.append(
                    FileBlock(
                        path=path,
                        content=render_clip_ledger_page(
                            title=goal,
                            collections=collections,
                            locators=locators,
                        ),
                    )
                )
                continue
            content = block.content
            if path != "wiki/log.md":
                page_locators = (
                    _locators_for_confirmed(
                        path, writes, topic_map, collections
                    )
                    or locators
                )
                content = apply_block_sources(content, page_locators)
                page_material[path] = frozenset(page_locators)
            blocks.append(FileBlock(path=path, content=content))
    else:
        scripts = agent_scripts or {}
        llm = None if scripts else ensure_client()
        page_refs: list[tuple[str, str]] = []
        for page in writes:
            check_cancelled(cancel)
            status(f"writing {page.name}", step_id="compose")
            node = topic_map.node_for_slug(page.slug)
            script = scripts.get(page.wiki_path.replace("\\", "/"))
            if not isinstance(script, dict):
                script = None
            if script is None and llm is None:
                llm = ensure_client()
            try:
                block, refs = write_confirmed_page(
                    intent=goal,
                    page_name=page.name,
                    wiki_path=page.wiki_path,
                    node=node,
                    collections=collections,
                    mention_names=mention_names,
                    skip_names=skip_names,
                    client=llm,
                    on_status=status,
                    script=script,
                    logic_links=logic_links_for_page(
                        topic_map, page.slug, action_by_slug
                    ),
                    cancel=cancel,
                    brief=read_brief(layout),
                )
            except PageStructureError as err:
                # A page whose markers do not all reach a heading would read as if
                # no agent wrote part of it. Skip it instead of writing that gap.
                relative = page.wiki_path.replace("\\", "/")
                message = f"skipped {relative}: {err}"
                page_skips.append(WriteWarning("skipped", message, relative))
                run_logger.warning(message, source_identity=identity, stage="write")
                status(f"skipped {page.name}", step_id="compose")
                continue
            page_refs.extend(refs)
            page_locators = locators_for_refs(collections, refs) or locators
            content = apply_block_sources(block.content, page_locators)
            relative = page.wiki_path.replace("\\", "/")
            page_material[relative] = frozenset(page_locators)
            blocks.append(FileBlock(path=relative, content=content))
            generation_parts.append(content)

    status("Parsing the generated pages")
    if not any(block.path.replace("\\", "/") == summary_path for block in blocks):
        status(f"Adding clip summary at {summary_path}")
        blocks.append(
            FileBlock(
                path=summary_path,
                content=render_clip_ledger_page(
                    title=goal,
                    collections=collections,
                    locators=locators,
                ),
            )
        )
    if not any(block.path.replace("\\", "/").lower() == "wiki/log.md" for block in blocks):
        wiki_date = current_wiki_date()
        log_lines = "\n".join(
            f"- `{item.wiki_path}`" for item in writes
        )
        blocks.append(
            FileBlock(
                path="wiki/log.md",
                content=f"## [{wiki_date}] ingest | {goal}\n{log_lines}\n",
            )
        )

    extra_warnings.extend(
        session_unknown_clip_warnings(
            [(block.path, block.content) for block in blocks],
            {item.id for item in collections},
        )
    )
    parse_result = ParseFileBlocksResult(
        blocks=tuple(blocks),
        warnings=tuple(extra_warnings),
    )
    for warning in parse_result.warnings:
        run_logger.warning(warning, source_identity=identity, stage="parse")

    merger = None
    repair = None
    if generation_text is None and not agent_scripts:
        merger = build_page_merger(
            ensure_client(), passages=page_passages(layout), cancel=cancel
        )
        repair = build_page_repairer(
            ensure_client(), passages=page_passages(layout), cancel=cancel
        )

    check_cancelled(cancel)
    status("Writing wiki files" + (" (dry-run)" if dry_run else ""))
    write_result = write_file_blocks(
        layout,
        blocks,
        dry_run=dry_run,
        source_identity=identity,
        material=page_material,
        merger=merger,
        repair=repair,
        run_logger=run_logger,
    )

    if page_skips:
        # A page refused for a marker gap is a hard write failure, the same as one
        # skipped for an unsafe path, so fingerprints and the job are not cleared.
        write_result = WriteFileBlocksResult(
            written=write_result.written,
            warnings=write_result.warnings + tuple(page_skips),
        )

    content_by_path = {
        block.path.replace("\\", "/"): block.content for block in blocks
    }
    index_pages: list[tuple[str, str]] = []
    for item in write_result.written:
        content = None
        if not dry_run and item.absolute_path.is_file():
            try:
                content = item.absolute_path.read_text(encoding="utf-8")
            except OSError:
                content = None
        if content is None:
            content = content_by_path.get(item.relative_path)
        if content is not None:
            index_pages.append((item.relative_path, content))

    status("Updating wiki/index.md" + (" (dry-run)" if dry_run else ""))
    index_result = update_wiki_index(layout, index_pages, dry_run=dry_run)
    doctor_report = doctor_after_write(
        layout, dry_run=dry_run, cache_hit=False, status=status
    )

    hard_failures = [w for w in write_result.warnings if hard_write_failure(w)]
    if not dry_run and write_result.written and not hard_failures:
        recorded = record_confirmed_fingerprints(
            layout,
            collections=collections,
            written_paths=[item.relative_path for item in write_result.written],
        )
        if recorded:
            status("Recorded ingest fingerprints for " + ", ".join(recorded))
        from noteagent.ingest.revision_inbox import note_source_change

        note_source_change(layout)

    run_logger.append(
        "ingest_confirmed.end",
        intent=goal,
        parsed_blocks=len(parse_result.blocks),
        written=len(write_result.written),
        dry_run=dry_run,
        hard_failures=len(hard_failures),
        codes=list(HARD_WRITE_CODES),
    )
    generation = "\n\n".join(generation_parts)
    result = IngestResult(
        source_identity=identity,
        analysis="",
        generation=generation,
        parse=parse_result,
        write=write_result,
        source_summary_path=summary_path,
        index=index_result,
        cache_hit=False,
        doctor=doctor_report,
    )
    if job_id and not dry_run and write_result.written and not hard_failures:
        # The round is over, so its job goes — but what it wrote is kept as the
        # last round, or the panel would have nothing to show once the window that
        # watched it is gone.
        complete_confirmed_job(
            layout,
            job_id,
            intent=goal,
            written=[
                (item.relative_path, item.mode) for item in write_result.written
            ],
            doctor=doctor_payload(doctor_report),
            write_warnings=[
                {
                    "code": warning.code,
                    "message": warning.message,
                    "path": warning.path,
                }
                for warning in write_result.warnings
            ],
            parse_warnings=parse_result.warnings,
        )
    return result


def apply_block_sources(
    content: str,
    locators: list[str],
) -> str:
    """Force ``sources`` after the model emits FILE blocks."""
    page = parse_page(content)
    if not page.has_frontmatter:
        return content
    existing = [
        item
        for item in parse_inline_list(page.fields.get("sources", "[]"))
        if not item.replace("\\", "/").strip().startswith("preview:")
    ]
    return set_frontmatter_scalar(
        content,
        "sources",
        format_inline_list(union_preserve_order(existing, locators)),
    )


def _sections_for(
    page: ConfirmedPage, node: TopicNode | None
) -> tuple[list[dict[str, object]], set[str]]:
    block_ids: set[str] = set()
    sections: list[dict[str, object]] = []
    if node is None:
        return sections, block_ids
    wanted = set(page.section_ids)
    for section in node.outline:
        if wanted and section.id not in wanted:
            continue
        sections.append(
            {
                "id": section.id,
                "title": section.title,
                "blockIds": list(section.block_ids),
                "collectionId": section.collection_id,
                "role": section.role,
            }
        )
        block_ids.update(section.block_ids)
    if not block_ids:
        block_ids.update(node.block_ids)
    return sections, block_ids


def _refs_for_page(
    page: ConfirmedPage,
    node: TopicNode | None,
    collections: tuple[ClipCollection, ...],
) -> list[tuple[str, str]]:
    sections, block_ids = _sections_for(page, node)
    refs: list[tuple[str, str]] = []
    for section in sections:
        cid = str(section.get("collectionId") or "")
        for bid in section.get("blockIds") or []:
            refs.append((cid, str(bid)))
    if refs:
        return refs
    if node is None:
        return []
    by_id = {item.id: item for item in collections}
    wanted = set(block_ids) or set(node.block_ids)
    for cid in node.collection_ids:
        collection = by_id.get(cid)
        if collection is None:
            continue
        for bid in collection.block_ids:
            if bid in wanted:
                refs.append((cid, bid))
    return refs


def _locators_for_confirmed(
    wiki_path: str,
    writes: list[ConfirmedPage],
    topic_map: TopicMap,
    collections: tuple[ClipCollection, ...],
) -> list[str]:
    for page in writes:
        if page.wiki_path.replace("\\", "/") == wiki_path:
            return locators_for_refs(
                collections,
                _refs_for_page(page, topic_map.node_for_slug(page.slug), collections),
            )
    return []


def _clip_summary_path(intent: str) -> str:
    return f"wiki/sources/{topic_slug(intent)}-clips.md"


__all__ = [
    "ACTIONS",
    "ConfirmedPage",
    "apply_block_sources",
    "confirmed_from_dict",
    "ingest_confirmed",
]
