"""The project's writing state: one truth for search, pages, frames and draft.

The state is a single versioned document per project. Every writer goes through
:class:`WritingStateStore`, which takes the project's lock, writes atomically,
and bumps ``stateVersion``. A missing file is an empty state; a damaged one is an
error, never a silent reset.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from noteagent.agent.clock import Clock, SystemClock
from noteagent.io.atomic import write_text_atomic
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.project.layout import ProjectLayout

#: The schema version written by this module. A state from another schema is not
#: read as if it were this one.
STATE_SCHEMA_VERSION = 1

#: The owner label the store uses while it holds the project lock. The state's
#: own ``writer`` field is set by the run holding the lock, not by this store.
STATE_WRITER = "writing-state"

#: A short wait for the project lock, so two tool calls in one round do not
#: turn a successful search into a lost one. A long holder still gets a refusal.
_LOCK_ATTEMPTS = 3
_LOCK_WAIT_S = 0.05

#: The statuses and failure kinds a selected page may carry. The wire is JSON,
#: so a hand-edited state is checked here rather than trusted.
PAGE_STATUSES = frozenset({"fetching", "ready", "failed"})
PAGE_ERROR_KINDS = frozenset({"fetch_failed", "login_wall"})

_PAGE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")


class WritingStateError(RuntimeError):
    """The writing state cannot be read as the state this build knows."""


class WritingStateConflict(RuntimeError):
    """The caller worked from an older state than the one on disk."""


@dataclass(frozen=True, slots=True)
class WritingState:
    """One project's writing state, in the shape the wire and tools read."""

    state_version: int = 0
    writer: Mapping[str, object] | None = None
    searches: tuple[Mapping[str, object], ...] = ()
    selected_pages: tuple[Mapping[str, object], ...] = ()
    frames: tuple[Mapping[str, object], ...] = ()
    draft: Mapping[str, object] = field(
        default_factory=lambda: {"pages": []}
    )
    attention: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        """The state as the wire and the file carry it."""
        return {
            "version": STATE_SCHEMA_VERSION,
            "stateVersion": self.state_version,
            "writer": dict(self.writer) if self.writer is not None else None,
            "searches": [dict(item) for item in self.searches],
            "selectedPages": [dict(item) for item in self.selected_pages],
            "frames": [dict(item) for item in self.frames],
            "draft": dict(self.draft),
            "attention": [dict(item) for item in self.attention],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "WritingState":
        """Read a state back, refusing a shape that means another thing."""
        if not isinstance(raw, Mapping):
            raise WritingStateError("the writing state must be an object")
        version = raw.get("version")
        if version != STATE_SCHEMA_VERSION:
            raise WritingStateError(
                f"unsupported writing state version: {version!r}"
            )
        writer = raw.get("writer")
        if writer is not None and not isinstance(writer, Mapping):
            raise WritingStateError(
                "the writing state's writer must be an object"
            )
        draft = raw.get("draft")
        if draft is None:
            draft = {"pages": []}
        if not isinstance(draft, Mapping):
            raise WritingStateError(
                "the writing state's draft must be an object"
            )
        state_version = raw.get("stateVersion")
        if (
            isinstance(state_version, bool)
            or not isinstance(state_version, int)
            or state_version < 0
        ):
            raise WritingStateError(
                "the writing state's stateVersion must be a non-negative integer"
            )
        return cls(
            state_version=state_version,
            writer=dict(writer) if isinstance(writer, Mapping) else None,
            searches=_items(raw.get("searches"), "searches"),
            selected_pages=_selected_pages(raw.get("selectedPages")),
            frames=_items(raw.get("frames"), "frames"),
            draft=dict(draft),
            attention=_items(raw.get("attention"), "attention"),
        )


class WritingStateStore:
    """Reads and writes one project's writing state, one writer at a time."""

    def __init__(
        self, layout: ProjectLayout, *, clock: Clock | None = None
    ) -> None:
        self._layout = layout
        self._clock = clock or SystemClock()

    def load(self) -> WritingState:
        """The state on disk, or an empty state when there is none."""
        path = self._layout.writing_state
        if not path.is_file():
            return WritingState()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise WritingStateError(
                f"the writing state cannot be read: {err}"
            ) from err
        return WritingState.from_dict(raw)

    def save(
        self, state: WritingState, *, owner: str = STATE_WRITER
    ) -> WritingState:
        """Write the state under the project lock, bumping its version.

        The lock is the project's one writer: a pipeline run or another writer
        holding it makes this refuse with ``ProjectBusy`` rather than overwrite.
        Under the lock the caller's version is checked against the state on
        disk; a save built on an older state is refused with
        ``WritingStateConflict`` instead of silently overwriting it.
        """
        lock = ProjectLock(self._layout.project_lock, clock=self._clock)
        for attempt in range(_LOCK_ATTEMPTS):
            try:
                lock.acquire(owner)
                break
            except ProjectBusy:
                if attempt + 1 == _LOCK_ATTEMPTS:
                    raise
                time.sleep(_LOCK_WAIT_S)
        try:
            current = self.load()
            if state.state_version != current.state_version:
                raise WritingStateConflict(
                    f"the writing state is at version "
                    f"{current.state_version}; the caller read version "
                    f"{state.state_version}"
                )
            next_state = replace(
                state, state_version=current.state_version + 1
            )
            path = self._layout.writing_state
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(
                path,
                json.dumps(
                    next_state.to_dict(), ensure_ascii=False, indent=2
                )
                + "\n",
            )
            return next_state
        finally:
            lock.release(owner)

    def append_search(self, row: Mapping[str, object]) -> WritingState:
        """Append one search row, retrying once if another writer moved first."""
        return self._append(
            row,
            lambda state, item: replace(
                state, searches=state.searches + (item,)
            ),
        )

    def append_selected_page(
        self, row: Mapping[str, object]
    ) -> WritingState:
        """Append one selected page, retrying once if another writer moved first."""
        return self._append(
            row,
            lambda state, item: replace(
                state, selected_pages=state.selected_pages + (item,)
            ),
        )

    def update_selected_page(
        self, page_id: str, row: Mapping[str, object]
    ) -> WritingState:
        """Replace one selected page by id, retrying once on a version conflict."""
        return self._append(
            row,
            lambda state, item: replace(
                state,
                selected_pages=tuple(
                    item if str(page.get("id")) == page_id else page
                    for page in state.selected_pages
                ),
            ),
        )

    def save_page_blocks(
        self, page_id: str, blocks: Sequence[Mapping[str, object]]
    ) -> None:
        """Keep one selected page's blocks beside the state index."""
        path = self._page_file(page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            path,
            json.dumps(
                {
                    "version": 1,
                    "blocks": [dict(block) for block in blocks],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

    def load_page_blocks(self, page_id: str) -> list[dict[str, object]]:
        """Read one selected page's blocks; a missing file is an error."""
        path = self._page_file(page_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise WritingStateError(
                f"the blocks for page {page_id} cannot be read: {err}"
            ) from err
        if not isinstance(raw, Mapping):
            raise WritingStateError(
                f"the blocks for page {page_id} must be an object"
            )
        blocks = raw.get("blocks")
        if not isinstance(blocks, list):
            raise WritingStateError(
                f"the blocks for page {page_id} must be a list"
            )
        return [dict(block) for block in blocks if isinstance(block, Mapping)]

    def _page_file(self, page_id: str) -> Path:
        key = (page_id or "").strip()
        if _PAGE_ID.fullmatch(key) is None:
            raise ValueError(f"not a page id: {page_id!r}")
        return self._layout.writing_pages / f"{key}.json"

    def _append(
        self,
        row: Mapping[str, object],
        build: Callable[
            [WritingState, Mapping[str, object]], WritingState
        ],
    ) -> WritingState:
        for attempt in range(2):
            state = self.load()
            next_state = build(state, row)
            try:
                return self.save(next_state)
            except WritingStateConflict:
                if attempt:
                    raise
        raise AssertionError("unreachable")


def state_summary(state: WritingState) -> dict[str, object]:
    """The bounded answer ``get_state()`` gives: counts, writer, attention."""
    pages = _draft_pages(state.draft)
    sections = sum(len(_draft_sections(page)) for page in pages)
    latest = state.searches[-1] if state.searches else None
    attention = [dict(item) for item in state.attention]
    attention.extend(
        {
            "kind": "fetch_failed",
            "target": str(row.get("id") or ""),
            "message": str(row.get("error") or "the search failed"),
        }
        for row in state.searches
        if _is_failed(row)
    )
    attention.extend(
        {
            "kind": str(row.get("errorKind") or "fetch_failed"),
            "target": str(row.get("id") or ""),
            "message": str(row.get("error") or "the page could not be fetched"),
        }
        for row in state.selected_pages
        if _is_failed(row)
    )
    return {
        "stateVersion": state.state_version,
        "writer": dict(state.writer) if state.writer is not None else None,
        "areas": {
            "searches": {
                "count": len(state.searches),
                "latest": (
                    {
                        "id": str(latest.get("id") or ""),
                        "query": str(latest.get("query") or ""),
                        "status": str(latest.get("status") or ""),
                    }
                    if latest is not None
                    else None
                ),
            },
            "selectedPages": {
                "count": len(state.selected_pages),
                "failed": sum(
                    1
                    for row in state.selected_pages
                    if _is_failed(row)
                ),
            },
            "frames": {"count": len(state.frames)},
            "draft": {"pages": len(pages), "sections": sections},
        },
        "attention": attention,
    }


def _items(value: object, name: str) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise WritingStateError(f"the writing state's {name} must be a list")
    items: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise WritingStateError(
                f"the writing state's {name} must hold objects"
            )
        items.append(dict(item))
    return tuple(items)


def _selected_pages(value: object) -> tuple[Mapping[str, object], ...]:
    rows = _items(value, "selectedPages")
    for row in rows:
        status = row.get("status")
        if status is not None and str(status) not in PAGE_STATUSES:
            raise WritingStateError(
                f"unknown selected page status: {status!r}"
            )
        kind = row.get("errorKind")
        if kind and str(kind) not in PAGE_ERROR_KINDS:
            raise WritingStateError(
                f"unknown selected page error kind: {kind!r}"
            )
    return rows


def _is_failed(row: Mapping[str, object]) -> bool:
    return str(row.get("status") or "") == "failed"


def _draft_pages(draft: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = draft.get("pages")
    if not isinstance(raw, list):
        return []
    return [page for page in raw if isinstance(page, Mapping)]


def _draft_sections(page: Mapping[str, object]) -> list[object]:
    raw = page.get("sections")
    return list(raw) if isinstance(raw, list) else []


__all__ = [
    "PAGE_ERROR_KINDS",
    "PAGE_STATUSES",
    "STATE_SCHEMA_VERSION",
    "STATE_WRITER",
    "WritingState",
    "WritingStateConflict",
    "WritingStateError",
    "WritingStateStore",
    "state_summary",
]
