"""The two pure nodes behind a ``write`` op, and the page lock they share.

A ``write`` op is the one task operation whose answer leaves the task file: it
composes a page from the frame the person approved and saves it into the wiki.
The executor owns it, like every other op, but the nodes are ordinary injectable
functions so a test can script a page or a failure without a model or a network.

Two things keep the page and the task from disagreeing:

* the compose node is :func:`noteagent.ingest.page_agents.write_confirmed_page`,
  which stamps and enforces the source markers, so a page is only written when
  every section can be traced to the material it was composed from;
* the save node is :func:`noteagent.ingest.write_file_blocks.write_file_blocks`
  with a :class:`~noteagent.ingest.write_file_blocks.WriteContext`, so the write
  keeps a page-history version, an effect entry and the actor that asked for it,
  and :func:`noteagent.ingest.update_index.update_wiki_index` links the page.

The page lock is the in-process half of the person-first rule: a compose holds
it only while it writes, so a person editing during the (long) composition wins
and the executor detects the change with the base digest it captured first.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Iterator

from noteagent.agent.clock import SystemClock
from noteagent.effects.journal import (
    COMMITTED,
    WIKI_PAGE_WRITE,
    EffectJournal,
)
from noteagent.ingest.clips import ClipCollection, source_locator
from noteagent.ingest.frontmatter import parse_page
from noteagent.ingest.page_agents import write_confirmed_page
from noteagent.ingest.parse_file_blocks import FileBlock
from noteagent.ingest.topic_map import PageSection, TopicNode, wiki_path_for_slug
from noteagent.ingest.update_index import update_wiki_index
from noteagent.ingest.write_file_blocks import (
    HARD_WRITE_CODES,
    WriteContext,
    write_file_blocks,
)
from noteagent.llm.client import LlmClient
from noteagent.llm.config import load_llm_config
from noteagent.pipeline.materials import Materials, hydrate_collection
from noteagent.project.layout import (
    WIKI_INDEX,
    WIKI_LOG,
    WIKI_OVERVIEW,
    ProjectLayout,
)
from noteagent.prompts.context import read_brief
from noteagent.util.run_log import RunLogger
from noteagent.wiki.links import normalize_wiki_rel

#: Pages the program maintains. A writing spec cannot name one: the index is
#: rebuilt from the pages, the log is append-only, and the overview is
#: generated. A model that asked for one would corrupt a file it cannot see.
PROTECTED_PAGES = frozenset({WIKI_INDEX, WIKI_LOG, WIKI_OVERVIEW})

#: How much of a page diff a result keeps. A page can be rewritten whole, and a
#: panel is not a place to scroll through a thousand lines.
MAX_DIFF_LINES = 400
_DIFF_CONTEXT = 3


class PageBusy(RuntimeError):
    """Another writer holds the page; the later writer must not enter."""


class ComposePageError(RuntimeError):
    """The compose node could not build a page from the frame."""


#: One project's page locks, shared by the task executor and the human editor.
#: The lock is process-wide; the base-digest check at save time is what covers
#: two processes, because both write paths keep the version they replace.
class PageLocks:
    """A named lock per project page, so a save and an edit do not interleave."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._locks: dict[tuple[str, str], Lock] = {}

    def lock(self, project_key: str, page: str) -> Lock:
        key = (project_key, normalize_wiki_rel(page))
        with self._guard:
            return self._locks.setdefault(key, Lock())

    @contextmanager
    def holding(self, project_key: str, page: str) -> Iterator[None]:
        """Hold one page; a second writer is refused rather than queued."""
        lock = self.lock(project_key, page)
        if not lock.acquire(blocking=False):
            raise PageBusy(f"the page is being written: {page}")
        try:
            yield
        finally:
            lock.release()


def page_path_for_spec(spec: Mapping[str, object]) -> str:
    """Where a writing spec's page lives, as a project-relative ``wiki/`` path."""
    slug = spec.get("slug")
    relative = wiki_path_for_slug(slug if isinstance(slug, str) else "")
    if relative in PROTECTED_PAGES:
        raise ValueError(
            f"the program maintains {relative}; choose another page name"
        )
    return relative


def locators_for_frame(material: str, blocks: Sequence[str]) -> tuple[str, ...]:
    """The durable ``path#block`` locators a frame's blocks stand for."""
    return tuple(
        item
        for item in (source_locator(material, block) for block in blocks)
        if item
    )


@dataclass(frozen=True, slots=True)
class ComposeRequest:
    """Everything a compose node needs: the page spec and the staged frame."""

    task_id: str
    intent: str
    slug: str
    page_name: str
    action: str
    wiki_path: str
    material: str
    blocks: tuple[str, ...]
    locators: tuple[str, ...]
    spec: Mapping[str, object] = field(default_factory=dict)


#: A pure compose node: frame and spec in, one source-marked FILE block out.
ComposePageFn = Callable[
    [ProjectLayout, ComposeRequest, Event | None], FileBlock
]


class TaskPageCompose:
    """The production compose node: staged blocks through ``write_confirmed_page``.

    ``script`` injects the blueprint/drafts/compose answers, so a test can drive
    the real marker-stamping path without a model. ``client_factory`` builds the
    LLM client lazily; nothing is loaded when a script is supplied.
    """

    def __init__(
        self,
        *,
        script: Mapping[str, object] | None = None,
        client_factory: Callable[[], LlmClient] | None = None,
    ) -> None:
        self._script = dict(script) if script is not None else None
        self._client_factory = client_factory

    def __call__(
        self,
        layout: ProjectLayout,
        request: ComposeRequest,
        cancel: Event | None = None,
    ) -> FileBlock:
        materials = Materials(layout, clock=SystemClock())
        rows = materials.blocks(request.material, run_id=request.task_id) or []
        selection = ClipCollection(
            id=request.material,
            source_key=request.material,
            source_label=request.page_name,
            block_ids=request.blocks,
            blocks=(),
        )
        collection = hydrate_collection(selection, rows)
        if collection is None:
            raise ComposePageError(
                f"the staged blocks of {request.material!r} are gone; "
                "the frame cannot be composed from them any more"
            )
        sections = tuple(
            PageSection(
                id=f"s{index}",
                title=str(section.get("title") or ""),
                block_ids=tuple(
                    str(block)
                    for block in section.get("blocks") or ()
                    if isinstance(block, str)
                ),
                collection_id=request.material,
                role="other",
            )
            for index, section in enumerate(request.spec["sections"], 1)
        )
        node = TopicNode(
            slug=request.slug,
            title=request.page_name,
            wiki_path=request.wiki_path,
            action="add" if request.action == "create" else "revise",
            coverage="write",
            collection_ids=(request.material,),
            block_ids=request.blocks,
            outline=sections,
        )
        client = None
        if self._script is None:
            client = (
                self._client_factory()
                if self._client_factory is not None
                else LlmClient(load_llm_config())
            )
        block, _refs = write_confirmed_page(
            intent=request.intent,
            page_name=request.page_name,
            wiki_path=request.wiki_path,
            node=node,
            collections=(collection,),
            mention_names=[],
            skip_names=[],
            client=client,
            script=self._script,
            cancel=cancel,
            brief=read_brief(layout),
        )
        return FileBlock(path=request.wiki_path, content=block.content)


@dataclass(frozen=True, slots=True)
class SaveRequest:
    """One composed page about to be written, and who asked for it."""

    task_id: str
    op_id: str
    session_id: str
    wiki_path: str
    action: str
    content: str
    material: str
    locators: tuple[str, ...]
    expected_rev: int = 0


@dataclass(frozen=True, slots=True)
class SaveOutcome:
    """The writer's answer for one page: written, skipped or failed."""

    status: str
    written: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    #: Journal ids, kept for the op only; a task result never carries them.
    effect_ids: tuple[str, ...] = ()
    summary: str = ""
    reason: str = ""


#: A pure save node: one FILE block in, what the writer did out.
SavePageFn = Callable[[ProjectLayout, SaveRequest, Event | None], SaveOutcome]


class TaskPageSave:
    """The production save node: ``write_file_blocks`` plus the index.

    The write context names the op, so a page's history row and its effect are
    traceable to the one task operation that wrote it. Re-running the same op
    after a crash finds the committed effect and reports the page as written
    again without writing it twice.
    """

    def __call__(
        self,
        layout: ProjectLayout,
        request: SaveRequest,
        cancel: Event | None = None,
    ) -> SaveOutcome:
        journal = EffectJournal(layout)
        committed = journal.query(
            run_id=request.op_id,
            kind=WIKI_PAGE_WRITE,
            status=COMMITTED,
        )
        if any(effect.target == request.wiki_path for effect in committed):
            return SaveOutcome(
                status="written",
                written=(request.wiki_path,),
                effect_ids=tuple(effect.ident for effect in committed),
                summary=f"already wrote {request.wiki_path}",
            )
        result = write_file_blocks(
            layout,
            [FileBlock(path=request.wiki_path, content=request.content)],
            source_identity=request.material,
            material={request.wiki_path: request.locators},
            run_logger=RunLogger.disabled(),
            write_context=WriteContext(
                run_id=request.op_id,
                session_id=request.session_id,
                actor="agent",
                plan_revision=str(request.expected_rev),
            ),
        )
        written = tuple(item.relative_path for item in result.written)
        warnings = list(result.warnings)
        hard = [
            warning
            for warning in warnings
            if warning.code in HARD_WRITE_CODES and warning.code != "skipped"
        ]
        skips = [warning for warning in warnings if warning.code == "skipped"]
        fallbacks = [
            warning for warning in warnings if warning.code == "merge_fallback"
        ]
        duplicates = [
            warning for warning in warnings if warning.code == "duplicate"
        ]
        effects = journal.query(
            run_id=request.op_id,
            kind=WIKI_PAGE_WRITE,
            status=COMMITTED,
        )
        effect_ids = tuple(effect.ident for effect in effects)
        if written and not hard and not skips and not fallbacks:
            content = _read_written(layout, written[0], request.content)
            update_wiki_index(layout, [(written[0], content)])
            item = result.written[0]
            return SaveOutcome(
                status="written",
                written=written,
                effect_ids=effect_ids,
                summary=(
                    f"written {written[0]} ({item.mode}, "
                    f"{item.bytes_written} bytes)"
                ),
            )
        if duplicates and not written:
            return SaveOutcome(
                status="written",
                written=(request.wiki_path,),
                effect_ids=effect_ids,
                summary=f"already wrote {request.wiki_path}",
            )
        if hard:
            reason = hard[0].message
            return SaveOutcome(
                status="failed",
                failed=((request.wiki_path, reason),),
                effect_ids=effect_ids,
                summary=f"could not write {request.wiki_path}: {reason}",
                reason=reason,
            )
        if fallbacks or skips:
            warning = (fallbacks or skips)[0]
            return SaveOutcome(
                status="skipped",
                skipped=((request.wiki_path, warning.message),),
                effect_ids=effect_ids,
                summary=f"skipped {request.wiki_path}: {warning.message}",
                reason=warning.message,
            )
        return SaveOutcome(
            status="skipped",
            skipped=(
                (
                    request.wiki_path,
                    "the writer reported no page and no reason",
                ),
            ),
            effect_ids=effect_ids,
            summary=f"skipped {request.wiki_path}",
            reason="the writer reported no page and no reason",
        )


def _read_written(
    layout: ProjectLayout, relative: str, fallback: str
) -> str:
    try:
        return layout.root.joinpath(*relative.split("/")).read_text(
            encoding="utf-8"
        )
    except OSError:
        return fallback


def page_diff(before: str, after: str, *, limit: int = MAX_DIFF_LINES) -> str:
    """A bounded unified diff of two pages' prose, for the result view."""
    old = parse_page(before).body if before else ""
    new = parse_page(after).body if after else ""
    lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            lineterm="",
            n=_DIFF_CONTEXT,
        )
    )
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith(
        "+++ "
    ):
        lines = lines[2:]
    if len(lines) > limit:
        rest = len(lines) - limit
        lines = lines[:limit] + [f"… {rest} more lines"]
    return "\n".join(lines)


def diff_counts(before: str, after: str) -> tuple[int, int]:
    """How many prose lines a change adds and removes."""
    old = parse_page(before).body if before else ""
    new = parse_page(after).body if after else ""
    lines = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), lineterm="", n=0
        )
    )
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith(
        "+++ "
    ):
        lines = lines[2:]
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    return added, removed


__all__ = [
    "MAX_DIFF_LINES",
    "PROTECTED_PAGES",
    "ComposePageError",
    "ComposePageFn",
    "ComposeRequest",
    "PageBusy",
    "PageLocks",
    "SaveOutcome",
    "SavePageFn",
    "SaveRequest",
    "TaskPageCompose",
    "TaskPageSave",
    "diff_counts",
    "locators_for_frame",
    "page_diff",
    "page_path_for_spec",
]
