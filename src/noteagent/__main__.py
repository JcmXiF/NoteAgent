"""The command line: project setup, reading and compiling sources, serving the API.

Every subcommand is a thin layer over a function that the API also calls, so the
two front ends cannot disagree about what an operation does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from noteagent.io.read_source import (
    format_source_summary,
    list_markdown_sources,
    list_project_sources,
    read_source,
)
from noteagent.llm.client import LlmClient, LlmError
from noteagent.llm.config import LlmConfigError, load_llm_config
from noteagent.project.config import (
    ProjectNotConfiguredError,
    resolve_active_project,
    save_active_project,
)
from noteagent.project.init import init_project, is_valid_project
from noteagent.project.layout import RAW_DIR, ProjectLayout, default_layout
from noteagent.prompts.analysis import build_analysis_prompt
from noteagent.ingest.auto_ingest import (
    auto_ingest,
    format_ingest_result,
    resolve_ingest_sources,
)
from noteagent.ingest.parse_file_blocks import format_parse_result, parse_file_blocks
from noteagent.ingest.update_index import (
    format_index_update_result,
    rebuild_wiki_index,
)
from noteagent.ingest.write_file_blocks import format_write_result, write_file_blocks
from noteagent.project.doctor import doctor_project, format_doctor_report
from noteagent.project.migrate import format_migration_result, migrate_project
from noteagent.project.reset import format_reset_result, reset_wiki_content
from noteagent.util.run_log import RunLogger


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="noteagent", description="NOTEAGENT CLI")
    parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Wiki project directory (overrides .noteagent-project)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    project_cmd = sub.add_parser("project", help="Manage wiki projects")
    project_sub = project_cmd.add_subparsers(dest="project_command", required=True)

    init_cmd = project_sub.add_parser("init", help="Create a wiki project skeleton")
    init_cmd.add_argument("path", type=Path, help="Directory for the new project")
    init_cmd.add_argument(
        "--set-active",
        action="store_true",
        help="Write .noteagent-project in the current directory",
    )
    init_cmd.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace template files if they already exist",
    )

    use_cmd = project_sub.add_parser("use", help="Set the active wiki project")
    use_cmd.add_argument("path", type=Path, help="Wiki project directory")
    use_cmd.add_argument(
        "--config-dir",
        type=Path,
        default=Path.cwd(),
        help="Where to write .noteagent-project (default: current directory)",
    )

    project_sub.add_parser("info", help="Show the active wiki project")

    reset_cmd = project_sub.add_parser(
        "reset",
        help="Wipe generated wiki pages; restore empty index and log",
    )
    reset_cmd.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Confirm destructive reset (required unless --dry-run)",
    )
    reset_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without changing the disk",
    )
    reset_cmd.add_argument(
        "--keep-cache",
        action="store_true",
        help="Do not delete .noteagent/compile-cache.json",
    )
    reset_cmd.add_argument(
        "--clear-run-log",
        action="store_true",
        help="Also delete .noteagent/run-log.jsonl",
    )

    rebuild_index_cmd = project_sub.add_parser(
        "rebuild-index",
        help="Rebuild wiki/index.md from pages currently on disk",
    )
    rebuild_index_cmd.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="Project directory (default: the active project)",
    )
    rebuild_index_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be indexed without writing",
    )

    doctor_cmd = project_sub.add_parser(
        "doctor",
        help="Lint wiki health: ghosts, broken links, frontmatter, short pages",
    )
    doctor_cmd.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="Project directory (default: the active project)",
    )

    migrate_cmd = project_sub.add_parser(
        "migrate",
        help="Move a project written for the old layout onto the current one",
    )
    migrate_cmd.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help="Project directory (default: the active project)",
    )
    migrate_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without changing the disk",
    )
    migrate_cmd.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Perform the migration (required unless --dry-run)",
    )

    read_cmd = sub.add_parser("read", help="Read markdown source(s) and print metadata")
    read_cmd.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=None,
        help=f"Optional path under the project (default: {RAW_DIR})",
    )
    read_cmd.add_argument(
        "--no-recursive",
        action="store_true",
        help="When reading a directory, do not scan subfolders",
    )

    analyze_cmd = sub.add_parser(
        "analyze",
        help="Analyze one source file with the model (no wiki writes)",
    )
    analyze_cmd.add_argument(
        "path",
        type=Path,
        help="Source file path (absolute, or relative to the wiki project root)",
    )
    analyze_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the assembled prompt without calling the LLM",
    )

    parse_cmd = sub.add_parser(
        "parse",
        help="Parse FILE blocks from text (no LLM, no wiki writes)",
    )
    parse_cmd.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help="Text file containing ---FILE:--- blocks (default: stdin)",
    )

    write_cmd = sub.add_parser(
        "write",
        help="Parse FILE blocks and write them into the active wiki project",
    )
    write_cmd.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help="Text file containing ---FILE:--- blocks (default: stdin)",
    )
    write_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be written without touching the disk",
    )
    write_cmd.add_argument(
        "--no-run-log",
        action="store_true",
        help="Do not append events to .noteagent/run-log.jsonl",
    )

    ingest_cmd = sub.add_parser(
        "ingest",
        help="Ingest source file(s)/dirs: analyze → generate → parse → write",
    )
    ingest_cmd.add_argument(
        "paths",
        type=Path,
        nargs="+",
        help="Source file(s) or directories (absolute, or relative to project root)",
    )
    ingest_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Call the LLM for both stages, but do not write files to disk",
    )
    ingest_cmd.add_argument(
        "--force",
        action="store_true",
        help="Ignore the compile cache and re-run the full pipeline",
    )
    ingest_cmd.add_argument(
        "--quiet-llm",
        action="store_true",
        help="Do not stream LLM tokens to stderr (still print stage status)",
    )
    ingest_cmd.add_argument(
        "--no-run-log",
        action="store_true",
        help="Do not append events to .noteagent/run-log.jsonl",
    )

    sub.add_parser("paths", help="Print standard project layout paths")

    serve_cmd = sub.add_parser(
        "serve",
        help="Start, stop, or restart the local HTTP API (requires noteagent[api])",
    )
    serve_cmd.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=("start", "stop", "restart", "status"),
        help="start (default), stop, restart, or status",
    )
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)

    acquire_cmd = sub.add_parser(
        "acquire",
        help="Source acquisition (search, fetch, write raw/)",
    )
    acquire_sub = acquire_cmd.add_subparsers(dest="acquire_command", required=True)
    plan_cmd = acquire_sub.add_parser(
        "plan",
        help="Print what/how-to-search plan without using the network",
    )
    plan_cmd.add_argument("topic", help="Knowledge point or search topic")
    plan_cmd.add_argument(
        "--intent",
        default=None,
        help="Optional user intent, e.g. 入门 / 要推导 / 只要定义",
    )
    plan_cmd.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="NAME",
        help="Load a skill body (progressive disclosure). Repeatable.",
    )
    search_cmd = acquire_sub.add_parser(
        "search",
        help="Call web_search and print labeled resource cards (needs search API)",
    )
    search_cmd.add_argument("query", help="Search keyword, usually the topic itself")
    search_cmd.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Max cards (default: budget MAX_CANDIDATES)",
    )
    search_cmd.add_argument(
        "--no-run-log",
        action="store_true",
        help="Do not append events to .noteagent/run-log.jsonl",
    )
    acquire_sub.add_parser(
        "status",
        help="Show whether web_search is configured (provider only, no API key)",
    )
    fetch_cmd = acquire_sub.add_parser(
        "fetch",
        help="Fetch public pages to reader-mode HTML + quality gate; optional write to raw/",
    )
    fetch_cmd.add_argument(
        "urls",
        nargs="+",
        help="Page URL(s) to fetch",
    )
    fetch_cmd.add_argument(
        "--query",
        default="",
        help="Optional topic recorded in the run-log (not used by the quality gate)",
    )
    fetch_cmd.add_argument(
        "--write",
        action="store_true",
        help="Write ok/low captures into the active project's raw/web/",
    )
    fetch_cmd.add_argument(
        "--no-run-log",
        action="store_true",
        help="Do not append events to .noteagent/run-log.jsonl",
    )

    return parser


def active_layout(explicit: Path | None) -> ProjectLayout:
    config = resolve_active_project(explicit=explicit)
    layout = default_layout(config.project_path)
    if not is_valid_project(layout.root):
        raise ProjectNotConfiguredError(
            f"Not a valid wiki project (missing {RAW_DIR}/): {layout.root}"
        )
    return layout


def cmd_project_init(path: Path, *, set_active: bool, overwrite: bool) -> int:
    layout = init_project(path, overwrite=overwrite)
    print(f"Initialized wiki project: {layout.root}")
    if set_active:
        config = save_active_project(layout.root)
        print(f"Active project config: {config.config_path}")
    return 0


def cmd_project_use(path: Path, config_dir: Path) -> int:
    project = path.expanduser().resolve()
    if not is_valid_project(project):
        print(
            f"Warning: {project} does not look like a wiki project yet "
            f"(missing {RAW_DIR}/).",
            file=sys.stderr,
        )
    config = save_active_project(project, config_dir=config_dir)
    print(f"Active project: {config.project_path}")
    print(f"Config file: {config.config_path}")
    return 0


def cmd_project_info(explicit: Path | None) -> int:
    config = resolve_active_project(explicit=explicit)
    print(f"project: {config.project_path}")
    print(f"config: {config.config_path}")
    return 0


def cmd_project_reset(
    layout: ProjectLayout,
    *,
    yes: bool,
    dry_run: bool,
    keep_cache: bool,
    clear_run_log: bool,
) -> int:
    """
    Wipe generated wiki pages; restore empty aggregate templates.
    """
    if not dry_run and not yes:
        print(
            "Error: reset is destructive. Re-run with --yes to confirm "
            "(or use --dry-run).",
            file=sys.stderr,
        )
        return 2
    result = reset_wiki_content(
        layout,
        clear_cache=not keep_cache,
        clear_run_log=clear_run_log,
        dry_run=dry_run,
    )
    print(format_reset_result(result))
    return 0


def cmd_project_rebuild_index(layout: ProjectLayout, *, dry_run: bool) -> int:
    result = rebuild_wiki_index(layout, dry_run=dry_run)
    print(format_index_update_result(result))
    return 0


def cmd_project_doctor(layout: ProjectLayout) -> int:
    report = doctor_project(layout)
    print(format_doctor_report(report))
    return 1 if report.errors else 0


def cmd_project_migrate(layout: ProjectLayout, *, yes: bool, dry_run: bool) -> int:
    """Print the plan, or carry it out once the person has said yes."""
    if not dry_run and not yes:
        print(
            "Error: migrate rewrites the project. Re-run with --yes to confirm "
            "(or use --dry-run).",
            file=sys.stderr,
        )
        return 2
    try:
        result = migrate_project(layout, dry_run=dry_run)
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(format_migration_result(result))
    return 1 if result.blocked or (result.collisions and not dry_run) else 0


def cmd_read(
    layout: ProjectLayout,
    path: Path | None,
    *,
    recursive: bool,
) -> int:
    if path is None:
        target = layout.raw
    else:
        candidate = path.expanduser()
        target = (
            candidate.resolve()
            if candidate.is_absolute()
            else (layout.root / candidate).resolve()
        )

    if target.is_dir():
        if target.resolve() == layout.raw.resolve():
            sources = list_project_sources(layout, recursive=recursive)
        else:
            sources = list_markdown_sources(target, recursive=recursive)
        if not sources:
            print(f"No .md files in: {target}")
            return 1
        for index, source_path in enumerate(sources):
            if index > 0:
                print()
            doc = read_source(source_path, project_root=layout.root)
            print(format_source_summary(doc))
        return 0

    doc = read_source(target, project_root=layout.root)
    print(format_source_summary(doc))
    return 0


def cmd_analyze(layout: ProjectLayout, path: Path, *, dry_run: bool) -> int:
    candidate = path.expanduser()
    target = (
        candidate.resolve()
        if candidate.is_absolute()
        else (layout.root / candidate).resolve()
    )
    if not target.is_file():
        print(f"Error: expected a source file, got: {target}", file=sys.stderr)
        return 1

    doc = read_source(target, project_root=layout.root)
    prompt = build_analysis_prompt(doc, layout)

    if dry_run:
        print("=== SYSTEM ===")
        print(prompt.system)
        print()
        print("=== USER ===")
        print(prompt.user)
        return 0

    try:
        config = load_llm_config()
        client = LlmClient(config)
        print(f"# analyzing {doc.identity}", file=sys.stderr)
        print(f"# model={config.model} endpoint={config.base_url}", file=sys.stderr)
        client.chat(prompt.as_messages(), on_token=lambda token: print(token, end="", flush=True))
        print()
    except (LlmConfigError, LlmError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    return 0


def cmd_parse(path: Path | None) -> int:
    """Parse FILE blocks out of text, from a file or standard input.

    Needs no active project: it reads text and writes nothing.
    """
    if path is None:
        text = sys.stdin.read()
    else:
        target = path.expanduser().resolve()
        if not target.is_file():
            print(f"Error: file not found: {target}", file=sys.stderr)
            return 1
        text = target.read_text(encoding="utf-8")

    result = parse_file_blocks(text)
    print(format_parse_result(result))
    # Warnings do not change the exit code: some blocks may still have parsed.
    return 0


def _read_file_blocks_text(path: Path | None) -> str:
    """Load the text holding FILE blocks, from a file or standard input."""
    if path is None:
        return sys.stdin.read()
    target = path.expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {target}")
    return target.read_text(encoding="utf-8")


def cmd_write(
    layout: ProjectLayout,
    path: Path | None,
    *,
    dry_run: bool,
    run_log: bool = True,
) -> int:
    """
    Parse FILE blocks then write them under the active project's wiki/.
    """
    try:
        text = _read_file_blocks_text(path)
    except FileNotFoundError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    parsed = parse_file_blocks(text)
    if parsed.warnings:
        print("--- parse warnings ---", file=sys.stderr)
        for warning in parsed.warnings:
            print(f"- {warning}", file=sys.stderr)

    if not parsed.blocks:
        print("Error: no writable FILE blocks found", file=sys.stderr)
        return 1

    logger = RunLogger.for_layout(layout, enabled=run_log)
    result = write_file_blocks(
        layout, parsed.blocks, dry_run=dry_run, run_logger=logger
    )
    print(format_write_result(result))
    if not result.written:
        return 1
    return 0


def cmd_ingest(
    layout: ProjectLayout,
    paths: list[Path],
    *,
    dry_run: bool,
    force: bool,
    quiet_llm: bool,
    run_log: bool = True,
) -> int:
    """Compile sources one after another: analyze, then generate, then write."""
    try:
        sources = resolve_ingest_sources(layout, paths)
    except (FileNotFoundError, NotADirectoryError, OSError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if not sources:
        print("Error: no markdown sources found", file=sys.stderr)
        return 1

    logger = RunLogger.for_layout(layout, enabled=run_log)

    def on_status(message: str) -> None:
        print(f"# {message}", file=sys.stderr)

    def on_token(token: str) -> None:
        print(token, end="", flush=True, file=sys.stderr)

    try:
        config = load_llm_config()
    except LlmConfigError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    print(
        f"# model={config.model} endpoint={config.base_url}",
        file=sys.stderr,
    )
    print(f"# sources: {len(sources)}", file=sys.stderr)

    # Without a token callback the request is not streamed at all, which some
    # endpoints answer with an empty body.
    token_cb = None if quiet_llm else on_token

    exit_code = 0
    for index, target in enumerate(sources, start=1):
        print(
            f"# [{index}/{len(sources)}] ingest {target}",
            file=sys.stderr,
        )
        try:
            result = auto_ingest(
                layout,
                target,
                llm_config=config,
                dry_run=dry_run,
                force=force,
                on_status=on_status,
                on_analysis_token=token_cb,
                on_generation_token=token_cb,
                run_logger=logger,
            )
            if not quiet_llm and not result.cache_hit:
                print(file=sys.stderr)
        except (LlmError, RuntimeError, OSError, ValueError) as err:
            print(f"Error: {err}", file=sys.stderr)
            logger.append(
                "ingest.error",
                message=str(err),
                path=str(target),
            )
            exit_code = 2
            continue

        if result.parse.warnings:
            print("--- parse warnings ---", file=sys.stderr)
            for warning in result.parse.warnings:
                print(f"- {warning}", file=sys.stderr)

        print(format_ingest_result(result, dry_run=dry_run))
        if index < len(sources):
            print()

        if result.cache_hit:
            continue
        if not result.write.written:
            exit_code = max(exit_code, 1)

    return exit_code


def cmd_paths(layout: ProjectLayout) -> int:
    from noteagent.util.run_log import run_log_path

    entries = {
        "project_root": layout.root,
        "raw": layout.raw,
        "wiki": layout.wiki,
        "meta": layout.meta,
        "config": layout.config,
        "brief": layout.brief,
        "wiki_index": layout.wiki_index,
        "wiki_log": layout.wiki_log,
        "run_log": run_log_path(layout),
    }
    for name, value in entries.items():
        try:
            display = value.relative_to(layout.root).as_posix()
        except ValueError:
            display = value.as_posix()
        print(f"{name}: {display}")
    return 0


def cmd_serve(*, host: str, port: int, action: str = "start") -> int:
    from noteagent.api.serve_control import status_server, stop_server

    kind = (action or "start").lower()
    if kind == "stop":
        code, message = stop_server(host, port)
        print(message)
        return code
    if kind == "status":
        code, message = status_server(host, port)
        print(message)
        return code
    if kind == "restart":
        code, message = stop_server(host, port)
        print(message)
        if code != 0:
            return code
    from noteagent.api.__main__ import main as api_main

    return api_main(["--host", host, "--port", str(port)])


def cmd_acquire_plan(
    topic: str,
    *,
    intent: str | None,
    skills: list[str],
) -> int:
    """
    Show the search plan. Does not call search APIs or write files.
    """
    from noteagent.acquire.plan import format_search_plan, open_search_plan
    from noteagent.acquire.skills import SkillNotFoundError

    try:
        plan = open_search_plan(
            topic,
            intent=intent,
            load_skill_names=tuple(skills),
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except SkillNotFoundError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(format_search_plan(plan), end="")
    return 0


def _acquire_logger(explicit: Path | None, *, enabled: bool) -> RunLogger:
    if not enabled:
        return RunLogger.disabled()
    try:
        return RunLogger.for_layout(active_layout(explicit))
    except ProjectNotConfiguredError:
        return RunLogger.disabled()


def cmd_acquire_search(
    query: str,
    *,
    top_k: int | None,
    project: Path | None,
    run_log: bool = True,
) -> int:
    """
    Run generic web_search and print classified cards. Does not write files.
    """
    from noteagent.acquire.cards import format_cards
    from noteagent.acquire.run_search import run_web_search
    from noteagent.acquire.search_config import SearchConfigError, SearchError
    from noteagent.acquire.types import ProviderNotConfiguredError

    logger = _acquire_logger(project, enabled=run_log)
    try:
        run = run_web_search(
            query,
            top_k=top_k,
            run_logger=logger,
            caller="cli",
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except ProviderNotConfiguredError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    except (SearchConfigError, SearchError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    print(f"provider: {run.provider}")
    print(f"query: {run.query}")
    print(f"hits: {len(run.hits)}  cards: {len(run.cards)}")
    print()
    print(format_cards(run.cards), end="")
    return 0


def cmd_acquire_status() -> int:
    """Print search provider status without exposing the API key."""
    from noteagent.acquire.search_config import SearchConfigError, load_search_config

    try:
        cfg = load_search_config()
    except SearchConfigError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    print(f"provider: {cfg.provider if cfg.configured else 'none'}")
    print(f"configured: {cfg.configured}")
    return 0 if cfg.configured else 2


def cmd_acquire_fetch(
    urls: list[str],
    *,
    query: str,
    project: Path | None,
    run_log: bool = True,
    write: bool = False,
) -> int:
    """Fetch public HTML to reader-mode HTML. Optionally write passing captures to raw/."""
    from noteagent.acquire.run_fetch import format_capture, run_web_fetch_many
    from noteagent.acquire.write_raw import format_write_raw_result, write_raw_captures

    logger = _acquire_logger(project, enabled=run_log)
    try:
        captures = run_web_fetch_many(
            urls,
            query=query,
            run_logger=logger,
            caller="cli",
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    exit_code = 0
    for index, capture in enumerate(captures):
        if index:
            print()
        print(format_capture(capture), end="")
        if capture.fidelity == "failed":
            exit_code = max(exit_code, 2)
    if write:
        try:
            layout = active_layout(project)
        except ProjectNotConfiguredError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 2
        result = write_raw_captures(layout, captures, run_logger=logger)
        print()
        print(format_write_raw_result(result), end="")
        if not result.written_paths:
            exit_code = max(exit_code, 2)
    return exit_code


def main() -> None:
    _configure_stdio()
    raise SystemExit(_run())


def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "project":
            if args.project_command == "init":
                return cmd_project_init(
                    args.path,
                    set_active=args.set_active,
                    overwrite=args.overwrite,
                )
            if args.project_command == "use":
                return cmd_project_use(args.path, args.config_dir)
            if args.project_command == "info":
                return cmd_project_info(args.project)
            if args.project_command == "reset":
                layout = active_layout(args.project)
                return cmd_project_reset(
                    layout,
                    yes=args.yes,
                    dry_run=args.dry_run,
                    keep_cache=args.keep_cache,
                    clear_run_log=args.clear_run_log,
                )
            if args.project_command == "rebuild-index":
                layout = active_layout(args.path or args.project)
                return cmd_project_rebuild_index(layout, dry_run=args.dry_run)
            if args.project_command == "doctor":
                layout = active_layout(args.path or args.project)
                return cmd_project_doctor(layout)
            if args.project_command == "migrate":
                layout = active_layout(args.path or args.project)
                return cmd_project_migrate(
                    layout, yes=args.yes, dry_run=args.dry_run
                )
            return 0

        # Parsing needs no active project: it reads text and writes nothing.
        if args.command == "parse":
            return cmd_parse(args.path)
        if args.command == "serve":
            return cmd_serve(host=args.host, port=args.port, action=args.action)
        if args.command == "acquire":
            if args.acquire_command == "plan":
                return cmd_acquire_plan(
                    args.topic,
                    intent=args.intent,
                    skills=args.skill,
                )
            if args.acquire_command == "search":
                return cmd_acquire_search(
                    args.query,
                    top_k=args.top_k,
                    project=args.project,
                    run_log=not args.no_run_log,
                )
            if args.acquire_command == "status":
                return cmd_acquire_status()
            if args.acquire_command == "fetch":
                return cmd_acquire_fetch(
                    args.urls,
                    query=args.query,
                    project=args.project,
                    run_log=not args.no_run_log,
                    write=args.write,
                )
            return 0

        layout = active_layout(args.project)
        if args.command == "read":
            return cmd_read(layout, args.path, recursive=not args.no_recursive)
        if args.command == "analyze":
            return cmd_analyze(layout, args.path, dry_run=args.dry_run)
        if args.command == "write":
            return cmd_write(
                layout,
                args.path,
                dry_run=args.dry_run,
                run_log=not args.no_run_log,
            )
        if args.command == "ingest":
            return cmd_ingest(
                layout,
                args.paths,
                dry_run=args.dry_run,
                force=args.force,
                quiet_llm=args.quiet_llm,
                run_log=not args.no_run_log,
            )
        if args.command == "paths":
            return cmd_paths(layout)
    except ProjectNotConfiguredError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    main()
