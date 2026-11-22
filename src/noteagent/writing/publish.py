"""Publish one draft page to the project's wiki.

The draft is the only source: every section's citations are resolved to the
selected pages and frames behind them, the raw originals are promoted, and the
page is written through the existing page-write seam (effect journal and page
history). A draft version that is already published is not written twice.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Event

from noteagent.acquire.write_raw import resolve_raw_write_path
from noteagent.agent.clock import Clock, SystemClock, iso_timestamp
from noteagent.agent.tools import check_cancelled
from noteagent.ingest.frontmatter import (
    format_inline_list,
    format_scalar,
    parse_page,
    replace_page_body,
    set_frontmatter_scalar,
)
from noteagent.ingest.page_agents import clip_marker
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    WriteContext,
    write_wiki_page,
)
from noteagent.ingest.wiki_paths import resolve_wiki_write_path
from noteagent.io.atomic import write_bytes_atomic
from noteagent.project.layout import ProjectLayout
from noteagent.util.run_log import RunLogger
from noteagent.writing.state import (
    SECTION_STALE_REASONS,
    WritingStateStore,
    canonical_wiki_file,
    draft_sections,
    section_reason,
)


@dataclass(frozen=True, slots=True)
class PublishResult:
    """What one publish did, in the shape the tool answers with."""

    written_at: str
    effect_ids: tuple[str, ...]
    #: Sections whose pinned source drifted but still had their blocks.
    warnings: tuple[dict[str, str], ...] = ()


def publish_page(
    layout: ProjectLayout,
    wiki_file: str,
    draft_version: int,
    *,
    run_id: str = "",
    session_id: str = "",
    clock: Clock | None = None,
    cancel: Event | None = None,
) -> PublishResult:
    """Write one draft page, then mark that version published in the state."""
    moment = clock or SystemClock()
    store = WritingStateStore(layout, clock=moment)
    check_cancelled(cancel)
    raw_file = str(wiki_file or "").strip()
    # The disk path is canonical; the state lookup also accepts the caller's
    # older spelling, so a draft composed before canonicalization still goes out.
    target_file = canonical_wiki_file(raw_file)
    state, page = store.checked_draft_page(raw_file, draft_version)
    if str(page.get("status") or "") == "written":
        return PublishResult(
            written_at=str(page.get("writtenAt") or ""),
            effect_ids=tuple(
                str(item) for item in page.get("effectIds") or []
            ),
        )

    sections = draft_sections(page)
    if not sections:
        raise ValueError(f"draft {target_file} has no sections")
    selected = {
        str(item.get("id")): item for item in state.selected_pages
    }
    frames = {str(item.get("id")): item for item in state.frames}
    sources: list[str] = []
    rendered: list[str] = []
    materials: dict[str, str] = {}
    blocks_by_page: dict[str, set[str]] = {}
    warnings: list[dict[str, str]] = []
    for section in sections:
        section_id = str(section.get("id") or "")
        body = str(section.get("body") or "").strip()
        if not body:
            raise ValueError(
                f"section {section.get('id')} has no body"
            )
        heading = str(section.get("heading") or "").strip()
        heading = heading.lstrip("#").strip()
        raw_citations = section.get("citations")
        citations = (
            raw_citations if isinstance(raw_citations, list) else []
        )
        markers: list[str] = []
        for citation in citations:
            if not isinstance(citation, Mapping):
                continue
            frame_id = str(citation.get("frameId") or "").strip()
            frame = frames.get(frame_id)
            if frame is None:
                raise ValueError(f"no such frame: {frame_id}")
            frame_page = selected.get(str(frame.get("pageId") or ""))
            if frame_page is None:
                raise ValueError(f"no such page for frame {frame_id}")
            if str(frame_page.get("status") or "") != "ready":
                raise ValueError(
                    f"page {frame_page.get('id')} is not ready"
                )
            material = str(frame_page.get("material") or "")
            if not material:
                raise ValueError(
                    f"page {frame_page.get('id')} has no original"
                )
            page_id = str(frame_page.get("id") or "")
            materials[page_id] = material
            page_blocks = blocks_by_page.get(page_id)
            if page_blocks is None:
                page_blocks = {
                    str(block.get("id"))
                    for block in store.load_page_blocks(page_id)
                }
                blocks_by_page[page_id] = page_blocks
            citation_blocks: list[str] = []
            for block_id in citation.get("blockIds") or []:
                text = str(block_id).strip()
                if not text:
                    continue
                if text not in page_blocks:
                    raise ValueError(f"no such block: {text}")
                if text not in citation_blocks:
                    citation_blocks.append(text)
                locator = f"{material}#{text}"
                if locator not in sources:
                    sources.append(locator)
            markers.append(clip_marker(frame_id, citation_blocks))
        marker = "\n".join(markers) + "\n" if markers else ""
        reason = section_reason(
            section,
            frames,
            selected,
            lambda page_key: blocks_by_page.get(page_key),
        )
        if reason in SECTION_STALE_REASONS:
            warnings.append(
                {"section": section_id, "reason": reason}
            )
        rendered.append(f"{marker}## {heading}\n\n{body}\n")

    title = str(page.get("title") or Path(target_file).stem)
    body = f"# {title}\n\n" + "\n".join(rendered)
    target = resolve_wiki_write_path(layout, target_file)
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if parse_page(existing).has_frontmatter:
            content = replace_page_body(existing, body)
            content = set_frontmatter_scalar(
                content, "sources", format_inline_list(sources)
            )
            content = set_frontmatter_scalar(
                content, "updated", date.today().isoformat()
            )
        else:
            content = _new_page(title, sources) + body
    else:
        content = _new_page(title, sources) + body

    for page_id, material in materials.items():
        check_cancelled(cancel)
        raw_path = resolve_raw_write_path(layout, material)
        if not raw_path.is_file():
            source = store.page_material(page_id, material)
            if source is None:
                raise ValueError(
                    f"the original for {material} is gone"
                )
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(raw_path, source.read_bytes())
        store.remove_page_material(page_id, material)

    check_cancelled(cancel)
    result = write_wiki_page(
        layout,
        target_file,
        content,
        mode="overwrite" if target.is_file() else "create",
        write_context=WriteContext(
            run_id=run_id,
            session_id=session_id,
            actor="agent",
        ),
        run_logger=RunLogger.for_layout(layout),
        source_identity=target_file,
    )
    hard = [
        warning
        for warning in result.warnings
        if warning.code in HARD_WRITE_CODES
    ]
    if hard:
        raise ValueError(hard[0].message)
    effect_ids = (result.effect_id,) if result.effect_id else ()
    written_at = iso_timestamp(moment)
    store.mark_draft_written(
        raw_file, draft_version, effect_ids, written_at
    )
    return PublishResult(
        written_at=written_at,
        effect_ids=effect_ids,
        warnings=tuple(warnings),
    )



def _new_page(title: str, sources: list[str]) -> str:
    today = date.today().isoformat()
    return (
        "---\n"
        "type: note\n"
        f"title: {format_scalar(title)}\n"
        f"created: {today}\n"
        f"updated: {today}\n"
        f"sources: {format_inline_list(sources)}\n"
        "---\n\n"
    )


__all__ = ["PublishResult", "publish_page"]
