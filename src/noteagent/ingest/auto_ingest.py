from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from noteagent.ingest.compile_cache import file_digest, lookup, record_ingest
from noteagent.ingest.parse_file_blocks import (
    FileBlock,
    ParseFileBlocksResult,
    parse_file_blocks,
)
from noteagent.ingest.page_evidence import page_passages
from noteagent.ingest.page_merge import MergeFn, RepairFn
from noteagent.ingest.page_rewrite import RewriteFn, rewrite_existing_pages
from noteagent.ingest.update_index import (
    IndexUpdateResult,
    format_index_update_result,
    update_wiki_index,
)
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    WriteFileBlocksResult,
    WriteWarning,
    format_write_result,
    write_file_blocks,
)
from noteagent.io.read_source import list_markdown_sources, read_source
from noteagent.llm.client import LlmClient
from noteagent.llm.config import LlmConfig, load_llm_config
from noteagent.project.layout import ProjectLayout
from noteagent.prompts.analysis import build_analysis_prompt
from noteagent.prompts.generation import (
    build_generation_prompt,
    source_summary_path_for_identity,
)
from noteagent.prompts.merge import build_page_merger, build_page_repairer
from noteagent.prompts.page_rewrite import build_page_rewrite_prompt
from noteagent.util.cancel import check_cancelled
from noteagent.util.run_log import RunLogger, emit_status

if TYPE_CHECKING:
    from noteagent.project.doctor import DoctorReport

#: ``on_status(message, step_id=...)``; the step id is optional for callers that
#: only forward the message.
StatusFn = Callable[..., None]

#: One ingest at a time per process, so two runs cannot rewrite the index or the
#: cache from under each other.
INGEST_LOCK = threading.Lock()


def is_program_owned_page(path: str) -> bool:
    """Whether *path* is a page the program maintains, not one the model writes."""
    normalized = path.replace("\\", "/").lower()
    return normalized in {"wiki/index.md"}


@dataclass(frozen=True, slots=True)
class IngestResult:
    """
    Outcome of one minimal auto-ingest run.
    """

    source_identity: str
    analysis: str
    generation: str
    parse: ParseFileBlocksResult
    write: WriteFileBlocksResult
    source_summary_path: str
    index: IndexUpdateResult | None = None
    cache_hit: bool = False
    doctor: DoctorReport | None = None


def auto_ingest(
    layout: ProjectLayout,
    source_path: str | Path,
    *,
    llm_config: LlmConfig | None = None,
    dry_run: bool = False,
    force: bool = False,
    on_status: StatusFn | None = None,
    on_analysis_token: Callable[[str], None] | None = None,
    on_generation_token: Callable[[str], None] | None = None,
    on_merge_token: Callable[[str], None] | None = None,
    on_rewrite_token: Callable[[str], None] | None = None,
    analysis_text: str | None = None,
    generation_text: str | None = None,
    merger: MergeFn | None = None,
    repair: RepairFn | None = None,
    rewrite: RewriteFn | None = None,
    run_logger: RunLogger | None = None,
    cancel: threading.Event | None = None,
) -> IngestResult:
    """
    Minimal two-pass ingest:

      read → [cache check] → analyze → generate
      → parse → write (page merge) → index → [cache record]

    ``analysis_text`` / ``generation_text`` allow tests to inject fake LLM
    output without network calls. When omitted, a real ``LlmClient`` is used.

    ``merger`` injects a page-merge function (tests). When omitted and either
    stage uses a real client, a client-backed merger is built automatically.
    When both stages are injected and ``merger`` is omitted, colliding pages
    keep the existing body (merge-fallback). ``repair`` is the one second attempt
    at a page the merge refused; when omitted it is built alongside a merger this
    function built itself, and never when a merger was injected.

    ``rewrite`` injects the page-level rewrite of R0-1 (tests). Unlike ``merger``
    and ``repair`` it also runs when both stages were injected, because the pass
    reads the page and the draft rather than asking for them: an injected rewrite
    turns the pass on, and nothing else about the run changes.

    ``run_logger`` appends structured JSONL events under ``.noteagent/``.

    ``dry_run`` still runs both LLM stages (unless texts are injected), then
    skips disk writes via ``write_file_blocks(..., dry_run=True)`` and does
    not modify ``wiki/index.md`` or the compile cache.

    ``force`` bypasses the compile-cache lookup.

    Concurrent calls within the same process are serialized by a lock.
    """

    logger = run_logger or RunLogger.disabled()

    def status(message: str, **kwargs) -> None:
        logger.status(message, **kwargs)
        emit_status(on_status, message, **kwargs)

    with INGEST_LOCK:
        return _auto_ingest_unlocked(
            layout,
            source_path,
            llm_config=llm_config,
            dry_run=dry_run,
            force=force,
            status=status,
            on_analysis_token=on_analysis_token,
            on_generation_token=on_generation_token,
            on_merge_token=on_merge_token,
            on_rewrite_token=on_rewrite_token,
            analysis_text=analysis_text,
            generation_text=generation_text,
            merger=merger,
            repair=repair,
            rewrite=rewrite,
            run_logger=logger,
            cancel=cancel,
        )


def hard_write_failure(warning: WriteWarning) -> bool:
    """True for skipped/failed writes — soft merge-fallback warnings do not qualify."""
    return warning.code in HARD_WRITE_CODES


def _client_page_rewrite(
    client: LlmClient,
    *,
    analysis: str,
    layout: ProjectLayout,
    on_token: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> RewriteFn:
    """Wire one client call per page into the rewrite pass's ``RewriteFn``."""

    def rewrite(page_path: str, existing: str, draft: str) -> str:
        prompt = build_page_rewrite_prompt(
            page_path=page_path,
            existing_content=existing,
            draft_content=draft,
            analysis=analysis,
            layout=layout,
        )
        return client.chat(prompt.as_messages(), on_token=on_token, cancel=cancel)

    return rewrite


def _auto_ingest_unlocked(
    layout: ProjectLayout,
    source_path: str | Path,
    *,
    llm_config: LlmConfig | None,
    dry_run: bool,
    force: bool,
    status: StatusFn,
    on_analysis_token: Callable[[str], None] | None,
    on_generation_token: Callable[[str], None] | None,
    on_merge_token: Callable[[str], None] | None,
    on_rewrite_token: Callable[[str], None] | None,
    analysis_text: str | None,
    generation_text: str | None,
    merger: MergeFn | None,
    repair: RepairFn | None,
    rewrite: RewriteFn | None,
    run_logger: RunLogger,
    cancel: threading.Event | None = None,
) -> IngestResult:
    doc = read_source(source_path, project_root=layout.root)
    summary_path = source_summary_path_for_identity(doc.identity)
    client: LlmClient | None = None

    def ensure_client() -> LlmClient:
        nonlocal client
        if client is None:
            client = LlmClient(llm_config or load_llm_config())
        return client

    run_logger.append(
        "ingest.start",
        source_identity=doc.identity,
        dry_run=dry_run,
        force=force,
    )
    check_cancelled(cancel)

    # Cache short-circuit
    if not force and not dry_run:
        digest = file_digest(doc.path)
        answer = lookup(layout, doc.identity, doc.content, digest=digest)
        if answer.hit:
            status(f"Cache hit for {doc.identity}; skipping ingest")
            run_logger.append(
                "ingest.end",
                source_identity=doc.identity,
                cache_hit=True,
                dry_run=dry_run,
                force=force,
                analysis_chars=0,
                generation_chars=0,
                parsed_blocks=0,
                written=0,
            )
            return IngestResult(
                source_identity=doc.identity,
                analysis="",
                generation="",
                parse=ParseFileBlocksResult(blocks=(), warnings=()),
                write=WriteFileBlocksResult(written=(), warnings=()),
                source_summary_path=summary_path,
                index=None,
                cache_hit=True,
            )

    # Analysis
    status(f"analyzing {doc.identity}")
    check_cancelled(cancel)
    analysis_prompt = build_analysis_prompt(doc, layout)
    if analysis_text is not None:
        analysis = analysis_text
    else:
        analysis = ensure_client().chat(
            analysis_prompt.as_messages(),
            on_token=on_analysis_token,
            cancel=cancel,
        )
    if not analysis.strip():
        raise RuntimeError("analysis returned empty content")

    # Generation
    status(f"generating wiki files for {doc.identity}")
    check_cancelled(cancel)
    generation_prompt = build_generation_prompt(doc, layout, analysis)
    if generation_text is not None:
        generation = generation_text
    else:
        generation = ensure_client().chat(
            generation_prompt.as_messages(),
            on_token=on_generation_token,
            cancel=cancel,
        )
    if not generation.strip():
        raise RuntimeError("generation returned empty content")

    # Parse
    status("Parsing the generated pages")
    parsed = parse_file_blocks(generation)
    blocks: list[FileBlock] = []
    extra_warnings = list(parsed.warnings)
    for block in parsed.blocks:
        if is_program_owned_page(block.path):
            extra_warnings.append(
                f'Ignored model-generated "{block.path}"; '
                "the catalog is rebuilt from the pages on disk."
            )
            continue
        blocks.append(block)

    # The source page is the model's to write. If it is missing, say so rather
    # than inventing one here: a page the program wrote from the analysis would
    # look like the model's work and be reported as neither.
    if not any(b.path.replace("\\", "/") == summary_path for b in blocks):
        extra_warnings.append(
            f'no source page at "{summary_path}"; the reply did not write one'
        )

    parse_result = ParseFileBlocksResult(
        blocks=tuple(blocks),
        warnings=tuple(extra_warnings),
    )
    for warning in parse_result.warnings:
        run_logger.warning(warning, source_identity=doc.identity, stage="parse")

    # Rewrite the existing pages this reply touches, with the page in front of the
    # model. Only pages above the threshold are worth a call of their own; the
    # reply replaces the draft, and the write path records it as a rewrite.
    rewritten: dict[str, str] = {}
    if generation_text is None or rewrite is not None:
        active_rewrite = rewrite
        if active_rewrite is None:
            active_rewrite = _client_page_rewrite(
                ensure_client(),
                analysis=analysis,
                layout=layout,
                on_token=on_rewrite_token,
                cancel=cancel,
            )

        check_cancelled(cancel)
        status("Rewriting the pages this source lands on")
        rewrite_result = rewrite_existing_pages(
            layout,
            blocks,
            active_rewrite,
            on_rewrite=lambda path: status(f"rewriting {path}"),
        )
        blocks = list(rewrite_result.blocks)
        rewritten = dict(rewrite_result.drafts)
        for warning in rewrite_result.warnings:
            run_logger.warning(warning, source_identity=doc.identity, stage="rewrite")

    # Page merger and repairer (LLM or injected). The repairer is built only when
    # the merger is the client's own: a caller that injected one is supplying its
    # own answers, and a real client taking over the second attempt would be a call
    # into a service the caller did not ask for.
    active_merger = merger
    active_repair = repair
    if analysis_text is None or generation_text is None:
        if active_merger is None:
            active_merger = build_page_merger(
                ensure_client(),
                passages=page_passages(layout),
                on_token=on_merge_token,
                cancel=cancel,
            )
            if active_repair is None:
                active_repair = build_page_repairer(
                    ensure_client(),
                    passages=page_passages(layout),
                    on_token=on_merge_token,
                    cancel=cancel,
                )

    # Write
    check_cancelled(cancel)
    status("Writing wiki files" + (" (dry-run)" if dry_run else ""))
    write_result = write_file_blocks(
        layout,
        blocks,
        dry_run=dry_run,
        source_identity=doc.identity,
        merger=active_merger,
        repair=active_repair,
        rewrite_drafts=rewritten,
        run_logger=run_logger,
    )

    # Deterministic index
    content_by_path = {
        block.path.replace("\\", "/"): block.content for block in blocks
    }
    index_pages: list[tuple[str, str]] = []
    for item in write_result.written:
        content: str | None = None
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
    run_logger.append(
        "index.update",
        source_identity=doc.identity,
        added=len(index_result.added),
        skipped=len(index_result.skipped),
        dry_run=index_result.dry_run,
    )

    doctor_report = None
    if not dry_run:
        from noteagent.project.doctor import doctor_after_write

        doctor_report = doctor_after_write(
            layout, dry_run=dry_run, cache_hit=False, status=status
        )

    # Cache record (successful real writes only)
    hard_failures = [w for w in write_result.warnings if hard_write_failure(w)]
    if not dry_run and write_result.written and not hard_failures:
        files_written = [w.relative_path for w in write_result.written]
        # The index is maintained here; include it in the presence check too.
        if "wiki/index.md" not in files_written:
            files_written.append("wiki/index.md")
        record_ingest(
            layout,
            doc.identity,
            doc.content,
            files_written,
            digest=file_digest(doc.path),
        )
        status(f"Recorded compile cache for {doc.identity}")
    elif not dry_run and hard_failures:
        status("Skipping cache record due to write failures")

    run_logger.append(
        "ingest.end",
        source_identity=doc.identity,
        cache_hit=False,
        dry_run=dry_run,
        force=force,
        analysis_chars=len(analysis),
        generation_chars=len(generation),
        parsed_blocks=len(parse_result.blocks),
        written=len(write_result.written),
        write_warnings=len(write_result.warnings),
        modes=[item.mode for item in write_result.written],
    )

    return IngestResult(
        source_identity=doc.identity,
        analysis=analysis,
        generation=generation,
        parse=parse_result,
        write=write_result,
        source_summary_path=summary_path,
        index=index_result,
        cache_hit=False,
        doctor=doctor_report,
    )


def resolve_ingest_sources(
    layout: ProjectLayout,
    paths: list[Path] | tuple[Path, ...],
) -> list[Path]:
    """
    Expand CLI paths into a de-duplicated list of markdown files.

    Directories are scanned recursively for ``*.md``. Order follows the
    input path order, then sorted relative paths within each directory.
    """
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        candidate = raw.expanduser()
        target = (
            (layout.root / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
        if target.is_dir():
            for path in list_markdown_sources(target, recursive=True):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    found.append(resolved)
        elif target.is_file():
            if target not in seen:
                seen.add(target)
                found.append(target)
        else:
            raise FileNotFoundError(f"source path not found: {target}")
    return found


def format_ingest_result(result: IngestResult, *, dry_run: bool = False) -> str:
    """
    Human-readable summary for CLI output (omit raw LLM bodies).
    """
    parts = [
        f"source: {result.source_identity}",
        f"source_summary: {result.source_summary_path}",
        f"cache_hit: {result.cache_hit}",
        f"analysis_chars: {len(result.analysis)}",
        f"generation_chars: {len(result.generation)}",
        f"parsed_blocks: {len(result.parse.blocks)}",
        f"dry_run: {dry_run}",
        "",
        format_write_result(result.write),
    ]
    if result.index is not None:
        parts.append("")
        parts.append(format_index_update_result(result.index))
    if result.doctor is not None:
        from noteagent.project.doctor import format_doctor_report

        parts.append("")
        parts.append("--- wiki doctor ---")
        parts.append(format_doctor_report(result.doctor))
    if result.parse.warnings:
        parts.append("")
        parts.append("--- parse warnings ---")
        for warning in result.parse.warnings:
            parts.append(f"- {warning}")
    return "\n".join(parts)
