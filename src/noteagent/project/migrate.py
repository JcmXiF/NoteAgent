"""Move a project written for the older layout onto the current one.

Two layouts are readable, but only one is written: pages directly under
``wiki/``, originals directly under ``raw/<kind>/``, and the project's own
settings under ``config/``. A project made before that shape keeps working --
nothing refuses to read it -- until this module moves it.

The command is a person's decision, not a background repair: it reports every
collision first and changes nothing while one is unresolved, because choosing
    which of two pages keeps a name is not something a program can know. What it
    does move, it rewrites in one pass: a stored path is only useful if every place
that holds it agrees, so the moves and the rewrites happen together or not at
all.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from noteagent.chat.limits import (
    default_limits,
    limits_bounds,
    limits_from_dict,
    save_limits,
)
from noteagent.ingest.frontmatter import format_scalar, unquote_scalar
from noteagent.ingest.compile_cache import read_records, write_records
from noteagent.ingest.update_index import rebuild_wiki_index
from noteagent.io.paths import prune_empty_parents
from noteagent.notes.store import parse_note_text
from noteagent.project.layout import RAW_DIR, WIKI_DIR, ProjectLayout
from noteagent.project.raw_kinds import LEGACY_RAW_SOURCES
from noteagent.util.refusal import Refusal
from noteagent.util.run_log import RunLogger
from noteagent.wiki.link_syntax import find_wikilinks, format_wikilink
from noteagent.wiki.links import normalize_wikilink_target
from noteagent.wiki.page_kinds import LEGACY_TYPE_DIRS

__all__ = [
    "Collision",
    "MigrationPlan",
    "MigrationResult",
    "Move",
    "format_migration_result",
    "migrate_project",
    "plan_migration",
]

#: Where the retired layout kept a person's notes about a page.
_NOTES_DIR = "notes"
_MEDIA_DIR = ".noteagent/media"
_DERIVED_DIR = ".noteagent/derived"
_INDEX_FILE = f"{WIKI_DIR}/index.md"
_WIKI_PREFIX = f"{WIKI_DIR}/"
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", re.DOTALL)

#: A string that is source text, not a stored path. Migration leaves it alone:
#: a quoted passage must read the same after the move as it did before.
_QUOTED_KEYS = frozenset(
    {
        "caption",
        "gist",
        "intent",
        "name",
        "notes",
        "notice",
        "preview",
        "query",
        "reason",
        "snippet",
        "sourceLabel",
        "text",
        "title",
    }
)

#: Project-relative JSON stores that hold stored paths.
_STORES = (
    ".noteagent/clips.json",
    ".noteagent/deep-search.json",
    ".noteagent/revision-inbox.json",
    ".noteagent/ingest-recovery.json",
)


@dataclass(frozen=True, slots=True)
class Move:
    """One file or folder going from one project-relative path to another."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class Collision:
    """More than one thing would land on one path, or a path is already taken."""

    target: str
    sources: tuple[str, ...]

    @property
    def message(self) -> str:
        return f"{self.target} <- {', '.join(self.sources)}"


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """Everything the migration would do, worked out before anything is written."""

    raw_moves: tuple[Move, ...] = ()
    page_moves: tuple[Move, ...] = ()
    note_moves: tuple[Move, ...] = ()
    media_moves: tuple[Move, ...] = ()
    derived_moves: tuple[Move, ...] = ()
    #: Files removed after their content has been carried elsewhere.
    drops: tuple[str, ...] = ()
    #: Directories that hold nothing once the moves are done.
    legacy_folders: tuple[str, ...] = ()
    #: ``purpose.md`` when it is the only copy of the brief.
    brief_from: str = ""
    limits_move: bool = False
    cache_drops: tuple[str, ...] = ()
    collisions: tuple[Collision, ...] = ()
    #: Old raw identity -> new raw identity.
    raw_targets: Mapping[str, str] = field(default_factory=dict)
    #: Old wiki path -> new wiki path.
    page_targets: Mapping[str, str] = field(default_factory=dict)
    #: Old page slug -> new page slug.
    slug_targets: Mapping[str, str] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return bool(self.collisions)

    @property
    def empty(self) -> bool:
        """Whether the project is already in the current shape."""
        return not (
            self.raw_moves
            or self.page_moves
            or self.note_moves
            or self.media_moves
            or self.derived_moves
            or self.drops
            or self.legacy_folders
            or self.brief_from
            or self.limits_move
            or self.cache_drops
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """What the migration found and, unless it was a dry run, did."""

    dry_run: bool
    blocked: bool
    raw_moves: tuple[Move, ...] = ()
    page_moves: tuple[Move, ...] = ()
    note_moves: tuple[Move, ...] = ()
    media_moves: tuple[Move, ...] = ()
    derived_moves: tuple[Move, ...] = ()
    rewritten_files: tuple[str, ...] = ()
    rewritten_stores: tuple[str, ...] = ()
    dropped_files: tuple[str, ...] = ()
    dropped_folders: tuple[str, ...] = ()
    brief_written: bool = False
    limits_moved: bool = False
    cache_dropped: tuple[str, ...] = ()
    collisions: tuple[Collision, ...] = ()
    index_rebuilt: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.raw_moves
            or self.page_moves
            or self.note_moves
            or self.media_moves
            or self.derived_moves
            or self.rewritten_files
            or self.rewritten_stores
            or self.dropped_files
            or self.dropped_folders
            or self.brief_written
            or self.limits_moved
            or self.cache_dropped
            or self.index_rebuilt
        )


def plan_migration(layout: ProjectLayout) -> MigrationPlan:
    """Work out every move and every collision without touching the disk."""
    raw_moves = _raw_moves(layout)
    page_moves = _page_moves(layout)
    raw_targets = {move.source: move.target for move in raw_moves}
    page_targets = {move.source: move.target for move in page_moves}
    slug_targets = {
        _slug_of(move.source): _slug_of(move.target) for move in page_moves
    }

    note_moves = _note_moves(layout, page_targets)
    media_moves = _mirror_moves(layout, raw_moves, _MEDIA_DIR)
    derived_moves = _mirror_moves(layout, raw_moves, _DERIVED_DIR)
    cache_drops = _cache_drops(layout, raw_targets, page_moves)

    collisions = (
        _collisions(layout, raw_moves)
        + _collisions(layout, page_moves)
        + _collisions(layout, note_moves)
        + _collisions(layout, media_moves)
        + _collisions(layout, derived_moves)
    )
    drops, brief_from = _retired_files(layout)
    legacy_folders = _legacy_folders(layout)
    limits_move = layout.legacy_chat_limits.is_file()

    return MigrationPlan(
        raw_moves=raw_moves,
        page_moves=page_moves,
        note_moves=note_moves,
        media_moves=media_moves,
        derived_moves=derived_moves,
        drops=drops,
        legacy_folders=legacy_folders,
        brief_from=brief_from,
        limits_move=limits_move,
        cache_drops=cache_drops,
        collisions=tuple(sorted(collisions, key=lambda item: item.target.casefold())),
        raw_targets=raw_targets,
        page_targets=page_targets,
        slug_targets=slug_targets,
    )


def migrate_project(layout: ProjectLayout, *, dry_run: bool = False) -> MigrationResult:
    """Plan, then -- unless this is a dry run -- carry the migration out.

    Collisions stop a real run before the first write. A dry run still reports
    them, so the person can resolve them and know the plan is complete.
    """
    plan = plan_migration(layout)
    result = _result_from_plan(plan, dry_run=dry_run)
    if plan.blocked:
        if not dry_run:
            listing = "\n".join(f"  - {item.message}" for item in plan.collisions)
            raise Refusal(
                "project.migrateCollisions",
                f"migration blocked by {len(plan.collisions)} collision(s):\n{listing}",
                params={
                    "count": str(len(plan.collisions)),
                    "items": "; ".join(item.message for item in plan.collisions),
                },
                status=409,
            )
        return result
    if dry_run or plan.empty:
        return result

    rewritten_files: list[str] = []
    _move_all(layout.root, plan.raw_moves)
    _move_all(layout.root, plan.media_moves)
    _move_all(layout.root, plan.derived_moves)
    _move_all(layout.root, plan.page_moves)
    _move_all(layout.root, plan.note_moves)

    for relative in _text_files(layout, plan.drops):
        if _rewrite_file(
            layout,
            relative,
            raw_targets=plan.raw_targets,
            page_targets=plan.page_targets,
            slug_targets=plan.slug_targets,
        ):
            rewritten_files.append(relative)

    rewritten_stores: list[str] = []
    for relative in _STORES:
        if _rewrite_json(
            layout.root / relative,
            raw_targets=plan.raw_targets,
            page_targets=plan.page_targets,
            slug_targets=plan.slug_targets,
        ):
            rewritten_stores.append(relative)

    brief_written = _carry_brief(layout, plan.brief_from)
    limits_moved = _carry_limits(layout) if plan.limits_move else False
    for relative in plan.drops:
        (layout.root / relative).unlink(missing_ok=True)

    if plan.cache_drops:
        records = read_records(layout)
        for identity in plan.cache_drops:
            records.pop(identity, None)
        write_records(layout, records)

    rebuild_wiki_index(layout)

    # Folders are only tidying: a locked or read-only one must not undo a
    # migration whose files and pages are already in their new places.
    dropped_folders = [
        relative
        for relative in plan.legacy_folders
        if _remove_empty(layout.root / relative)
    ]
    for move in plan.raw_moves:
        _prune((layout.root / move.source).parent, layout.root / LEGACY_RAW_SOURCES)
    for move in plan.page_moves:
        _prune((layout.root / move.source).parent, layout.wiki)
    for move in plan.note_moves:
        _prune((layout.root / move.source).parent, layout.notes)
    for root_relative in (_MEDIA_DIR, _DERIVED_DIR):
        _remove_empty(layout.root / root_relative / "raw" / "sources")

    RunLogger.for_layout(layout).append(
        "project.migrate",
        raw=len(plan.raw_moves),
        pages=len(plan.page_moves),
        notes=len(plan.note_moves),
        files_dropped=len(plan.drops),
        cache_dropped=len(plan.cache_drops),
    )

    return MigrationResult(
        dry_run=False,
        blocked=False,
        raw_moves=plan.raw_moves,
        page_moves=plan.page_moves,
        note_moves=plan.note_moves,
        media_moves=plan.media_moves,
        derived_moves=plan.derived_moves,
        rewritten_files=tuple(rewritten_files),
        rewritten_stores=tuple(rewritten_stores),
        dropped_files=plan.drops,
        dropped_folders=tuple(dropped_folders),
        brief_written=brief_written,
        limits_moved=limits_moved,
        cache_dropped=plan.cache_drops,
        collisions=plan.collisions,
        index_rebuilt=True,
    )


def format_migration_result(result: MigrationResult) -> str:
    """The plan or the outcome, as a terminal reads it."""
    if result.dry_run:
        head = "Migration plan (dry run)"
    elif result.changed:
        head = "Migrated project"
    else:
        head = "Nothing to migrate"
    lines = [head]

    def count(label: str, items: Iterable[object]) -> None:
        values = list(items)
        if values:
            lines.append(f"- {label}: {len(values)}")

    count("raw originals", result.raw_moves)
    count("wiki pages", result.page_moves)
    count("notes", result.note_moves)
    count("media folders", result.media_moves)
    count("derived files", result.derived_moves)
    count("text files rewritten", result.rewritten_files)
    count("stores rewritten", result.rewritten_stores)
    if result.dropped_files:
        names = ", ".join(result.dropped_files)
        lines.append(f"- files dropped: {len(result.dropped_files)} ({names})")
    if result.dropped_folders:
        names = ", ".join(result.dropped_folders)
        lines.append(f"- retired folders cleared: {len(result.dropped_folders)} ({names})")
    count("cache entries dropped", result.cache_dropped)
    if result.brief_written:
        lines.append(
            "- brief: would come from purpose.md"
            if result.dry_run
            else "- brief: created config/brief.md from purpose.md"
        )
    if result.limits_moved:
        lines.append(
            "- chat limits: would move to config/chat-limits.json"
            if result.dry_run
            else "- chat limits: moved to config/chat-limits.json"
        )
    if result.index_rebuilt:
        lines.append("- wiki/index.md rebuilt")
    elif result.dry_run and result.changed:
        lines.append("- wiki/index.md would be rebuilt")
    if result.collisions:
        lines.append("")
        lines.append(f"Collisions ({len(result.collisions)}); nothing is changed until they are resolved:")
        lines.extend(f"  - {item.message}" for item in result.collisions)
    if result.dry_run and result.changed:
        lines.append("")
        lines.append("Dry run: nothing was changed. Re-run with --yes to perform it.")
    return "\n".join(lines)


def _result_from_plan(plan: MigrationPlan, *, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        dry_run=dry_run,
        blocked=plan.blocked,
        raw_moves=plan.raw_moves,
        page_moves=plan.page_moves,
        note_moves=plan.note_moves,
        media_moves=plan.media_moves,
        derived_moves=plan.derived_moves,
        dropped_files=plan.drops,
        dropped_folders=plan.legacy_folders,
        brief_written=bool(plan.brief_from),
        limits_moved=plan.limits_move,
        cache_dropped=plan.cache_drops,
        collisions=plan.collisions,
    )


def _raw_moves(layout: ProjectLayout) -> tuple[Move, ...]:
    """``raw/sources/<kind>/**/x.html`` -> ``raw/<kind>/x.html``."""
    legacy = layout.root / LEGACY_RAW_SOURCES
    if not legacy.is_dir():
        return ()
    moves: list[Move] = []
    for path in sorted(legacy.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(legacy).parts
        name = parts[-1]
        target = f"{RAW_DIR}/{parts[0]}/{name}" if len(parts) > 1 else f"{RAW_DIR}/{name}"
        moves.append(
            Move(
                source=f"{LEGACY_RAW_SOURCES}/{path.relative_to(legacy).as_posix()}",
                target=target,
            )
        )
    return tuple(moves)


def _page_moves(layout: ProjectLayout) -> tuple[Move, ...]:
    """A page in a retired type folder becomes a flat note."""
    if not layout.wiki.is_dir():
        return ()
    moves: list[Move] = []
    for path in sorted(layout.wiki.rglob("*.md")):
        relative = path.relative_to(layout.wiki)
        if len(relative.parts) < 2:
            continue
        if relative.parts[0].lower() not in LEGACY_TYPE_DIRS:
            continue
        moves.append(
            Move(
                source=f"{_WIKI_PREFIX}{relative.as_posix()}",
                target=f"{_WIKI_PREFIX}{relative.parts[-1]}",
            )
        )
    return tuple(moves)


def _note_moves(
    layout: ProjectLayout,
    page_targets: Mapping[str, str],
) -> tuple[Move, ...]:
    """A note follows the page it is about into the page's new mirror folder."""
    if not layout.notes.is_dir() or not page_targets:
        return ()
    targets = {key.casefold(): value for key, value in page_targets.items()}
    moves: list[Move] = []
    for path in sorted(layout.notes.rglob("*.md")):
        try:
            fields, _body = parse_note_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        page = fields.get("page") or _page_from_location(layout, path)
        new_page = targets.get(_clean(page).casefold())
        if new_page is None:
            continue
        new_folder = Path(new_page[len(_WIKI_PREFIX) :]).with_suffix("")
        target = layout.notes.joinpath(*new_folder.parts) / path.name
        if target.resolve() == path.resolve():
            continue
        moves.append(
            Move(
                source=path.relative_to(layout.root).as_posix(),
                target=target.relative_to(layout.root).as_posix(),
            )
        )
    return tuple(moves)


def _page_from_location(layout: ProjectLayout, path: Path) -> str:
    """The page a note claims by sitting where it sits."""
    try:
        parts = path.relative_to(layout.notes).parent.parts
    except ValueError:
        return ""
    if not parts:
        return ""
    return f"{_WIKI_PREFIX}{'/'.join(parts)}.md"


def _mirror_moves(
    layout: ProjectLayout,
    raw_moves: tuple[Move, ...],
    root_relative: str,
) -> tuple[Move, ...]:
    """Working files follow their original: ``<root>/<identity>`` -> ``<root>/<new>``."""
    base = layout.root / root_relative
    moves: list[Move] = []
    for move in raw_moves:
        source = base.joinpath(*move.source.split("/"))
        target = base.joinpath(*move.target.split("/"))
        if root_relative == _DERIVED_DIR:
            source = source.with_name(source.name + ".json")
            target = target.with_name(target.name + ".json")
        if not source.exists():
            continue
        moves.append(
            Move(
                source=source.relative_to(layout.root).as_posix(),
                target=target.relative_to(layout.root).as_posix(),
            )
        )
    return tuple(moves)


def _collisions(layout: ProjectLayout, moves: tuple[Move, ...]) -> tuple[Collision, ...]:
    """Paths two moves share, and targets an unrelated file already occupies."""
    sources = {move.source for move in moves}
    groups: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for move in moves:
        key = move.target.casefold()
        groups.setdefault(key, []).append(move.source)
        display.setdefault(key, move.target)
    found: list[Collision] = []
    for key, items in groups.items():
        target = display[key]
        occupants = list(items)
        if (layout.root / target).exists() and target not in sources:
            occupants.append(target)
        if len(occupants) > 1:
            found.append(Collision(target=target, sources=tuple(occupants)))
    return tuple(found)


def _retired_files(layout: ProjectLayout) -> tuple[tuple[str, ...], str]:
    """Files the current shape no longer has, and where the brief comes from."""
    drops: list[str] = []
    if layout.legacy_overview.is_file():
        drops.append(f"{_WIKI_PREFIX}overview.md")
    brief_from = ""
    if layout.legacy_purpose.is_file():
        drops.append("purpose.md")
        if not _has_text(layout.brief):
            brief_from = "purpose.md"
    if layout.legacy_schema.is_file():
        drops.append(layout.legacy_schema.name)
    if layout.legacy_compile_cache.is_file():
        # Its rows are keyed by the identities and output paths this run is
        # replacing, so none of them can match again; one recompile is cheaper
        # than rewriting a cache nobody can trust.
        drops.append(
            layout.legacy_compile_cache.relative_to(layout.root).as_posix()
        )
    return tuple(drops), brief_from


def _legacy_folders(layout: ProjectLayout) -> tuple[str, ...]:
    """The retired folders themselves, once their files have moved out."""
    folders: list[str] = []
    if (layout.root / LEGACY_RAW_SOURCES).is_dir():
        folders.append(LEGACY_RAW_SOURCES)
    if layout.wiki.is_dir():
        for child in sorted(layout.wiki.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_dir() and child.name.lower() in LEGACY_TYPE_DIRS:
                folders.append(f"{_WIKI_PREFIX}{child.name}")
    return tuple(folders)


def _cache_drops(
    layout: ProjectLayout,
    raw_targets: Mapping[str, str],
    page_moves: tuple[Move, ...],
) -> tuple[str, ...]:
    """Cache entries that can no longer match the file they describe."""
    if not raw_targets and not page_moves:
        return ()
    moved_pages = {move.source.casefold() for move in page_moves}
    dropped: list[str] = []
    for identity, entry in read_records(layout).items():
        normalized = identity.replace("\\", "/")
        if normalized.casefold().startswith(f"{LEGACY_RAW_SOURCES}/"):
            dropped.append(identity)
            continue
        written = {item.replace("\\", "/").casefold() for item in entry.wrote}
        pages = {key.replace("\\", "/").casefold() for key, _ in entry.pages}
        if written & moved_pages or pages & moved_pages:
            dropped.append(identity)
    return tuple(sorted(dropped))


def _text_files(layout: ProjectLayout, drops: tuple[str, ...]) -> list[str]:
    """Pages and notes whose content stores a path."""
    skip = {item.casefold() for item in drops} | {_INDEX_FILE.casefold()}
    files: list[str] = []
    for root in (layout.wiki, layout.notes):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            relative = path.relative_to(layout.root).as_posix()
            if relative.casefold() in skip:
                continue
            files.append(relative)
    return files


def _rewrite_file(
    layout: ProjectLayout,
    relative: str,
    *,
    raw_targets: Mapping[str, str],
    page_targets: Mapping[str, str],
    slug_targets: Mapping[str, str],
) -> bool:
    """Rewrite one page or note, returning whether its text changed."""
    path = layout.root / relative
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    rewritten = _rewrite_page_text(
        text,
        raw_targets=raw_targets,
        page_targets=page_targets,
        slug_targets=slug_targets,
        is_note=relative.startswith(f"{_NOTES_DIR}/"),
    )
    if rewritten == text:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def _rewrite_page_text(
    text: str,
    *,
    raw_targets: Mapping[str, str],
    page_targets: Mapping[str, str],
    slug_targets: Mapping[str, str],
    is_note: bool,
) -> str:
    folded = text.replace("\r\n", "\n").replace("\r", "\n")
    if not is_note:
        return _rewrite_links(_replace_identities(folded, raw_targets), slug_targets)
    match = _FRONTMATTER.match(folded)
    if match is None:
        return _rewrite_links(_replace_identities(folded, raw_targets), slug_targets)
    head = _rewrite_page_field(match.group(1), page_targets)
    body = _rewrite_links(_replace_identities(match.group(2), raw_targets), slug_targets)
    return f"---\n{head}\n---\n{body}"


def _rewrite_page_field(head: str, page_targets: Mapping[str, str]) -> str:
    """Point a note's ``page`` at the page's new identity."""
    lines = head.split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("page:"):
            continue
        value = unquote_scalar(line[len("page:") :].strip())
        target = page_targets.get(_clean(value))
        if target is not None:
            lines[index] = f"page: {format_scalar(target)}"
        break
    return "\n".join(lines)


def _replace_identities(text: str, raw_targets: Mapping[str, str]) -> str:
    """Rewrite every stored ``raw/sources/...`` path to the file's new identity."""
    if not raw_targets:
        return text
    out = text
    for old in sorted(raw_targets, key=len, reverse=True):
        out = _replace_path(out, old, raw_targets[old])
    return out


def _replace_path(text: str, old: str, new: str) -> str:
    """Replace *old* wherever it is a whole path, not the start of a deeper one."""
    if old not in text:
        return text
    pieces: list[str] = []
    start = 0
    while True:
        index = text.find(old, start)
        if index < 0:
            pieces.append(text[start:])
            return "".join(pieces)
        end = index + len(old)
        if end < len(text) and text[end] == "/":
            pieces.append(text[start:end])
            start = end
            continue
        pieces.append(text[start:index])
        pieces.append(new)
        start = end


def _rewrite_links(text: str, slug_targets: Mapping[str, str]) -> str:
    """Rewrite ``[[concepts/x]]`` to ``[[x]]``, keeping anchor and label."""
    if not slug_targets:
        return text
    out = text
    for link in reversed(find_wikilinks(text)):
        target = link.target
        path, separator, anchor = target.partition("#")
        slug = normalize_wikilink_target(path)
        new_slug = slug_targets.get(slug) or _flat_slug(slug)
        if new_slug is None:
            continue
        label = link.label
        if label is not None and label.strip() == target.strip():
            label = None
        new_target = new_slug + (f"{separator}{anchor}" if separator else "")
        out = out[: link.start] + format_wikilink(new_target, label) + out[link.end :]
    return out


def _rewrite_json(
    path: Path,
    *,
    raw_targets: Mapping[str, str],
    page_targets: Mapping[str, str],
    slug_targets: Mapping[str, str],
) -> bool:
    """Rewrite the stored paths in one JSON store, leaving quoted text alone."""
    if not path.is_file() or not (raw_targets or page_targets or slug_targets):
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    rewritten = _rewrite_json_value(
        payload,
        raw_targets=raw_targets,
        page_targets=page_targets,
        slug_targets=slug_targets,
    )
    if rewritten == payload:
        return False
    path.write_text(
        json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def _rewrite_json_value(
    value: object,
    *,
    raw_targets: Mapping[str, str],
    page_targets: Mapping[str, str],
    slug_targets: Mapping[str, str],
) -> object:
    if isinstance(value, dict):
        return {
            key: (
                item
                if key in _QUOTED_KEYS
                else _rewrite_json_value(
                    item,
                    raw_targets=raw_targets,
                    page_targets=page_targets,
                    slug_targets=slug_targets,
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _rewrite_json_value(
                item,
                raw_targets=raw_targets,
                page_targets=page_targets,
                slug_targets=slug_targets,
            )
            for item in value
        ]
    if not isinstance(value, str):
        return value
    cleaned = _clean(value)
    exact_page = page_targets.get(cleaned) or _flat_wiki_path(cleaned)
    if exact_page is not None:
        return exact_page
    exact_slug = slug_targets.get(cleaned) or _flat_slug(cleaned)
    if exact_slug is not None:
        return exact_slug
    return _rewrite_links(_replace_identities(value, raw_targets), slug_targets)


def _carry_brief(layout: ProjectLayout, purpose: str) -> bool:
    """Turn ``purpose.md`` into ``config/brief.md`` when the brief has no words."""
    if not purpose:
        return False
    source = layout.root / purpose
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not text.strip():
        return False
    layout.config.mkdir(parents=True, exist_ok=True)
    layout.brief.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return True


def _carry_limits(layout: ProjectLayout) -> bool:
    """Move the limits out of program state, folding the two old brief knobs into one."""
    legacy = layout.legacy_chat_limits
    if not legacy.is_file():
        return False
    if layout.chat_limits.is_file():
        # A limits file already in the new place is the one the person edited.
        legacy.unlink(missing_ok=True)
        return False
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("chat limits must be an object")
        limits = limits_from_dict(_fold_brief_knobs(raw))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        limits = default_limits()
    save_limits(layout, limits)
    legacy.unlink(missing_ok=True)
    return True


def _fold_brief_knobs(raw: dict[str, object]) -> dict[str, object]:
    """``maxPurposeChars`` and ``maxSchemaChars`` become the one ``maxBriefChars``.

    Values are clamped to the range the core enforces rather than rejected: an
    old file may hold a number this version narrowed, and dropping every other
    setting because of one is not a migration a person asked for.
    """
    folded = dict(raw)
    bounds = limits_bounds()
    brief: int | None = None
    for key in ("maxPurposeChars", "maxSchemaChars"):
        value = folded.pop(key, None)
        if isinstance(value, int) and not isinstance(value, bool):
            brief = value if brief is None else max(brief, value)
    if brief is not None:
        low, high = bounds["maxBriefChars"]["min"], bounds["maxBriefChars"]["max"]
        folded["maxBriefChars"] = max(low, min(high, brief))
    for key, span in bounds.items():
        value = folded.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            folded.pop(key, None)
            continue
        folded[key] = max(span["min"], min(span["max"], value))
    return folded


def _move_all(root: Path, moves: tuple[Move, ...]) -> None:
    for move in moves:
        source = root / move.source
        if not source.exists():
            continue
        target = root / move.target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.replace(target)
        except OSError:
            # Windows may keep a read-only bit from an earlier copy. Add write
            # rather than replace the mode: a file or folder that loses its
            # other bits cannot be read or entered on a case-sensitive system.
            os.chmod(source, source.stat().st_mode | stat.S_IWRITE)
            source.replace(target)


def _remove_empty(path: Path) -> bool:
    """Remove *path* when nothing is left in it, at any depth; say whether it went."""
    if not path.is_dir():
        return False
    for child in list(path.iterdir()):
        if child.is_dir():
            _remove_empty(child)
    if any(path.iterdir()):
        return False
    return _rmdir(path)


def _rmdir(path: Path) -> bool:
    """Remove an empty folder, clearing the read-only bit Windows may carry."""
    try:
        path.rmdir()
        return True
    except OSError:
        try:
            os.chmod(path, path.stat().st_mode | stat.S_IWRITE)
            path.rmdir()
            return True
        except OSError:
            return False


def _prune(start: Path, stop: Path) -> None:
    """Tidy empty parents, but never let a locked folder fail the migration."""
    try:
        prune_empty_parents(start, stop)
    except OSError:
        return


def _has_text(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeDecodeError):
        return False


def _slug_of(wiki_relative: str) -> str:
    text = _clean(wiki_relative)
    if text.startswith(_WIKI_PREFIX):
        text = text[len(_WIKI_PREFIX) :]
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text


def _flat_slug(slug: str) -> str | None:
    """A link that names a retired folder but no page on disk: drop the folder."""
    head, separator, rest = slug.partition("/")
    if separator and head.lower() in LEGACY_TYPE_DIRS and rest:
        return rest
    return None


def _flat_wiki_path(value: str) -> str | None:
    """A ``wiki/<type>/x.md`` path for a page not on disk: the flat path it will have."""
    if not value.startswith(_WIKI_PREFIX):
        return None
    rest = value[len(_WIKI_PREFIX) :]
    head, separator, tail = rest.partition("/")
    if separator and head.lower() in LEGACY_TYPE_DIRS and tail:
        return f"{_WIKI_PREFIX}{tail}"
    return None


def _clean(value: str) -> str:
    return (value or "").replace("\\", "/").strip().lstrip("./")
