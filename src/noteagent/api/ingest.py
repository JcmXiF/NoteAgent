"""HTTP helpers for compiling selected raw files into wiki via auto_ingest."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from noteagent.api.sse import iter_job_sse
from noteagent.ingest.auto_ingest import IngestResult, StatusFn, auto_ingest
from noteagent.llm.client import LlmError
from noteagent.llm.config import LlmConfig, load_llm_config
from noteagent.project.doctor import doctor_payload
from noteagent.project.layout import ProjectLayout
from noteagent.util.cancel import JobCancelled
from noteagent.util.run_log import RunLogger

MAX_INGEST_PATHS = 8


def resolve_http_ingest_paths(layout: ProjectLayout, paths: list[str]) -> list[Path]:
    """
    Only markdown files under ``raw/``. No directories, no wiki paths.
    """
    if not paths:
        raise ValueError("at least one source path is required")
    if len(paths) > MAX_INGEST_PATHS:
        raise ValueError(f"at most {MAX_INGEST_PATHS} sources per ingest")

    raw_root = layout.raw.resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("source path must be a non-empty string")
        normalized = raw.replace("\\", "/").lstrip("/")
        if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
            raise ValueError(f"unsafe source path: {raw}")
        target = (layout.root / normalized).resolve()
        try:
            target.relative_to(raw_root)
        except ValueError as err:
            raise ValueError(f"ingest path must be under raw/: {raw}") from err
        if not target.is_file():
            raise FileNotFoundError(f"source path not found: {normalized}")
        if target.suffix.lower() != ".md":
            raise ValueError(f"ingest source must be markdown: {normalized}")
        if target not in seen:
            seen.add(target)
            found.append(target)
    return found


def ingest_result_payload(result: IngestResult, *, dry_run: bool = False) -> dict[str, object]:
    index_added = 0
    index_skipped = 0
    if result.index is not None:
        index_added = len(result.index.added)
        index_skipped = len(result.index.skipped)
    return {
        "sourceIdentity": result.source_identity.replace("\\", "/"),
        "sourceSummaryPath": result.source_summary_path,
        "cacheHit": result.cache_hit,
        "analysisChars": len(result.analysis),
        "generationChars": len(result.generation),
        "parsedBlocks": len(result.parse.blocks),
        "dryRun": dry_run,
        "written": [
            {
                "path": item.relative_path.replace("\\", "/"),
                "mode": item.mode,
                "bytes": item.bytes_written,
            }
            for item in result.write.written
        ],
        "writeWarnings": [
            {"code": warning.code, "message": warning.message, "path": warning.path}
            for warning in result.write.warnings
        ],
        "parseWarnings": list(result.parse.warnings),
        "indexAdded": index_added,
        "indexSkipped": index_skipped,
        "doctor": doctor_payload(result.doctor),
    }


def run_ingest_job(
    layout: ProjectLayout,
    paths: list[str],
    *,
    on_status: StatusFn | None = None,
    force: bool = False,
    dry_run: bool = False,
    llm_config: LlmConfig | None = None,
    analysis_text: str | None = None,
    generation_text: str | None = None,
    cancel=None,
) -> dict[str, object]:
    """
    Compile selected raw files. ``analysis_text`` / ``generation_text`` are for
    tests only; the HTTP layer never passes them.
    """
    sources = resolve_http_ingest_paths(layout, paths)
    logger = RunLogger.for_layout(layout)
    results: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    def status(message: str, **kwargs) -> None:
        if on_status is not None:
            try:
                on_status(message, **kwargs)
            except TypeError:
                on_status(message)

    for target in sources:
        rel = target.relative_to(layout.root.resolve()).as_posix()
        status(f"ingest {rel}")
        try:
            result = auto_ingest(
                layout,
                target,
                llm_config=llm_config,
                dry_run=dry_run,
                force=force,
                on_status=on_status,
                analysis_text=analysis_text,
                generation_text=generation_text,
                run_logger=logger,
                cancel=cancel,
            )
        except JobCancelled:
            raise
        except (LlmError, RuntimeError, OSError, ValueError) as err:
            logger.append("ingest.error", message=str(err), path=rel)
            errors.append({"path": rel, "detail": str(err)})
            status(f"error {rel}: {err}")
            continue
        results.append(ingest_result_payload(result, dry_run=dry_run))

    return {"results": results, "errors": errors}


def iter_ingest_sse(
    layout: ProjectLayout,
    paths: list[str],
    *,
    force: bool = False,
) -> Iterator[bytes]:
    """Server-sent events: ``status``, then ``done`` or ``error``."""

    def worker(on_status, cancel, _emit):
        return run_ingest_job(
            layout,
            paths,
            on_status=on_status,
            force=force,
            cancel=cancel,
        )

    return iter_job_sse(worker, default_step_id="ingest")


def ensure_llm_configured() -> None:
    load_llm_config()


__all__ = [
    "MAX_INGEST_PATHS",
    "ensure_llm_configured",
    "ingest_result_payload",
    "iter_ingest_sse",
    "resolve_http_ingest_paths",
    "run_ingest_job",
]
