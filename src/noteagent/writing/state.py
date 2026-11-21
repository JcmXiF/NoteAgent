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
FRAME_SOURCES = frozenset({"agent", "human"})
DRAFT_STATUSES = frozenset({"draft", "written"})

#: The id shape a selected page, a frame and a draft section share.
WRITING_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")


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
        draft = _draft(raw.get("draft"))
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
            frames=_frames(raw.get("frames")),
            draft=draft,
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

    def page_for_frame(
        self, page_id: str
    ) -> tuple[Mapping[str, object], list[dict[str, object]]]:
        """The ready page a frame may cite, and its blocks."""
        state = self.load()
        page = next(
            (
                item
                for item in state.selected_pages
                if str(item.get("id")) == page_id
            ),
            None,
        )
        if page is None:
            raise ValueError(f"no such page: {page_id}")
        if str(page.get("status") or "") != "ready":
            raise ValueError(f"page {page_id} is not ready")
        return page, self.load_page_blocks(page_id)

    def append_frame(self, row: Mapping[str, object]) -> WritingState:
        """Append one frame, retrying once if another writer moved first."""
        return self._append(
            row,
            lambda state, item: replace(
                state, frames=state.frames + (item,)
            ),
        )

    def compose_draft(
        self,
        wiki_file: str,
        sections: Sequence[Mapping[str, object]],
        base_version: int,
    ) -> tuple[
        WritingState, Mapping[str, object], tuple[Mapping[str, object], ...]
    ]:
        """Merge sections into one draft page, checking its base version.

        An existing section id is replaced where it stands (its ``baseVersion``
        must match); a new id is inserted after its ``after`` anchor or
        appended. Frame ids are resolved here, so the citation pins the frame
        version and block ids the page was written from. The page's version
        moves on by one, and a caller built on an older page is refused.
        """
        key = wiki_file.strip()
        if not key:
            raise ValueError("a draft page needs a wiki file")
        if base_version < 0:
            raise ValueError("baseDraftVersion must be non-negative")
        for attempt in range(2):
            state = self.load()
            frames = {
                str(frame.get("id")): frame for frame in state.frames
            }
            pages = [dict(page) for page in draft_pages(state.draft)]
            index = _page_index(pages, key)
            if index is None:
                if base_version != 0:
                    raise ValueError(
                        f"no draft page {key} at version {base_version}"
                    )
                pages.append(
                    {
                        "wikiFile": key,
                        "title": _wiki_title(key),
                        "version": 0,
                        "status": "draft",
                        "sections": [],
                        "writtenAt": "",
                        "effectIds": [],
                    }
                )
                index = len(pages) - 1
            page = pages[index]
            current_version = row_version(page)
            if current_version != base_version:
                raise ValueError(
                    f"draft {key} is at version {current_version}; "
                    f"the caller read {base_version}"
                )
            current_sections = [
                dict(section) for section in draft_sections(page)
            ]
            changed: list[Mapping[str, object]] = []
            for raw in sections:
                section_id = str(raw["id"])
                heading = str(raw["heading"])
                body = str(raw.get("body") or "")
                citations: list[dict[str, object]] = []
                for frame_id in raw.get("citations", []):
                    frame_key = str(frame_id).strip()
                    frame = frames.get(frame_key)
                    if frame is None:
                        raise ValueError(f"no such frame: {frame_key}")
                    citations.append(
                        {
                            "frameId": frame_key,
                            "frameVersion": row_version(frame),
                            "blockIds": list(
                                frame.get("blockIds") or []
                            ),
                        }
                    )
                base_section = raw.get("baseVersion")
                if base_section is not None and (
                    isinstance(base_section, bool)
                    or not isinstance(base_section, int)
                    or base_section < 0
                ):
                    raise ValueError(
                        "baseVersion must be a non-negative integer"
                    )
                existing = _section_index(
                    current_sections, section_id
                )
                if existing is not None:
                    old = current_sections[existing]
                    old_version = row_version(old)
                    if (
                        base_section is not None
                        and old_version != base_section
                    ):
                        raise ValueError(
                            f"section {section_id} is at version "
                            f"{old_version}; the caller read "
                            f"{base_section}"
                        )
                    new = {
                        **old,
                        "heading": heading,
                        "body": body,
                        "citations": citations,
                        "version": old_version + 1,
                        "stale": False,
                    }
                    current_sections[existing] = new
                else:
                    if base_section not in (None, 0):
                        raise ValueError(
                            f"new section {section_id} cannot have "
                            f"baseVersion {base_section}"
                        )
                    after = str(raw.get("after") or "").strip()
                    new = {
                        "id": section_id,
                        "heading": heading,
                        "body": body,
                        "citations": citations,
                        "version": 1,
                        "stale": False,
                        "humanEdited": False,
                    }
                    if after:
                        anchor = _section_index(
                            current_sections, after
                        )
                        if anchor is None:
                            raise ValueError(
                                f"no section to insert after: {after}"
                            )
                        current_sections.insert(anchor + 1, new)
                    else:
                        current_sections.append(new)
                changed.append(new)
            page = {
                **page,
                "title": str(
                    page.get("title") or _wiki_title(key)
                ),
                "version": current_version + 1,
                "status": "draft",
                "sections": current_sections,
            }
            pages[index] = page
            next_state = replace(state, draft={"pages": pages})
            try:
                return self.save(next_state), page, tuple(changed)
            except WritingStateConflict:
                if attempt:
                    raise
        raise AssertionError("unreachable")

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
        if WRITING_ID.fullmatch(key) is None:
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
    pages = draft_pages(state.draft)
    sections = sum(len(draft_sections(page)) for page in pages)
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


def _frames(value: object) -> tuple[Mapping[str, object], ...]:
    rows = _items(value, "frames")
    for row in rows:
        frame_id = row.get("id")
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise WritingStateError("a frame needs an id")
        page_id = row.get("pageId")
        if not isinstance(page_id, str) or not page_id.strip():
            raise WritingStateError("a frame needs a pageId")
        block_ids = row.get("blockIds")
        if not isinstance(block_ids, list) or any(
            not isinstance(block, str) for block in block_ids
        ):
            raise WritingStateError(
                "a frame's blockIds must be a list of strings"
            )
        version = row.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 1
        ):
            raise WritingStateError(
                "a frame's version must be a positive integer"
            )
        page_version = row.get("pageVersion")
        if (
            isinstance(page_version, bool)
            or not isinstance(page_version, int)
            or page_version < 0
        ):
            raise WritingStateError(
                "a frame's pageVersion must be a non-negative integer"
            )
        source = row.get("source")
        if source is not None and str(source) not in FRAME_SOURCES:
            raise WritingStateError(f"unknown frame source: {source!r}")
    return rows


def _draft(value: object) -> Mapping[str, object]:
    if value is None:
        return {"pages": []}
    if not isinstance(value, Mapping):
        raise WritingStateError(
            "the writing state's draft must be an object"
        )
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list):
        raise WritingStateError(
            "the writing state's draft pages must be a list"
        )
    pages: list[Mapping[str, object]] = []
    for page in raw_pages:
        if not isinstance(page, Mapping):
            raise WritingStateError("a draft page must be an object")
        wiki_file = page.get("wikiFile")
        if not isinstance(wiki_file, str) or not wiki_file.strip():
            raise WritingStateError("a draft page needs a wikiFile")
        status = str(page.get("status") or "draft")
        if status not in DRAFT_STATUSES:
            raise WritingStateError(
                f"unknown draft page status: {status!r}"
            )
        version = page.get("version", 0)
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version < 0
        ):
            raise WritingStateError(
                "a draft page's version must be a non-negative integer"
            )
        raw_sections = page.get("sections")
        if not isinstance(raw_sections, list):
            raise WritingStateError(
                "a draft page's sections must be a list"
            )
        for section in raw_sections:
            if not isinstance(section, Mapping):
                raise WritingStateError("a draft section must be an object")
            section_id = section.get("id")
            if not isinstance(section_id, str) or not section_id.strip():
                raise WritingStateError("a draft section needs an id")
            heading = section.get("heading")
            body = section.get("body")
            if not isinstance(heading, str) or not heading.strip():
                raise WritingStateError("a draft section needs a heading")
            if not isinstance(body, str):
                raise WritingStateError("a draft section needs a body")
            citations = section.get("citations", [])
            if not isinstance(citations, list):
                raise WritingStateError(
                    "a draft section's citations must be a list"
                )
            for citation in citations:
                if not isinstance(citation, Mapping):
                    raise WritingStateError("a citation must be an object")
                frame_id = citation.get("frameId")
                if not isinstance(frame_id, str) or not frame_id.strip():
                    raise WritingStateError("a citation needs a frameId")
                frame_version = citation.get("frameVersion")
                if (
                    isinstance(frame_version, bool)
                    or not isinstance(frame_version, int)
                    or frame_version < 1
                ):
                    raise WritingStateError(
                        "a citation's frameVersion must be a positive integer"
                    )
                block_ids = citation.get("blockIds")
                if not isinstance(block_ids, list) or any(
                    not isinstance(block, str) for block in block_ids
                ):
                    raise WritingStateError(
                        "a citation's blockIds must be a list of strings"
                    )
            section_version = section.get("version")
            if (
                isinstance(section_version, bool)
                or not isinstance(section_version, int)
                or section_version < 1
            ):
                raise WritingStateError(
                    "a draft section's version must be a positive integer"
                )
            for flag in ("stale", "humanEdited"):
                if not isinstance(section.get(flag, False), bool):
                    raise WritingStateError(
                        f"a draft section's {flag} must be a boolean"
                    )
        pages.append(dict(page))
    return {"pages": pages}


def row_version(row: Mapping[str, object]) -> int:
    """A mapping's version, or 0 when it has none; an invalid value reads as 0."""
    value = row.get("version")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def draft_page_for(
    draft: Mapping[str, object], wiki_file: str
) -> Mapping[str, object] | None:
    """One draft page by its wiki file, if it has been composed."""
    key = wiki_file.strip()
    return next(
        (
            page
            for page in draft_pages(draft)
            if str(page.get("wikiFile")) == key
        ),
        None,
    )


def draft_section_for(
    page: Mapping[str, object], section_id: str
) -> Mapping[str, object] | None:
    """One section by its stable id, if the page has it."""
    key = section_id.strip()
    return next(
        (
            section
            for section in draft_sections(page)
            if str(section.get("id")) == key
        ),
        None,
    )


def _page_index(
    pages: Sequence[Mapping[str, object]], wiki_file: str
) -> int | None:
    key = wiki_file.strip()
    return next(
        (
            index
            for index, page in enumerate(pages)
            if str(page.get("wikiFile")) == key
        ),
        None,
    )


def _section_index(
    sections: Sequence[Mapping[str, object]], section_id: str
) -> int | None:
    key = section_id.strip()
    return next(
        (
            index
            for index, section in enumerate(sections)
            if str(section.get("id")) == key
        ),
        None,
    )


def _wiki_title(wiki_file: str) -> str:
    name = wiki_file.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def draft_pages(draft: Mapping[str, object]) -> list[Mapping[str, object]]:
    """The draft's pages, in the order they were composed."""
    raw = draft.get("pages")
    if not isinstance(raw, list):
        return []
    return [page for page in raw if isinstance(page, Mapping)]


def draft_sections(page: Mapping[str, object]) -> list[Mapping[str, object]]:
    """One draft page's sections, in page order."""
    raw = page.get("sections")
    if not isinstance(raw, list):
        return []
    return [section for section in raw if isinstance(section, Mapping)]


__all__ = [
    "DRAFT_STATUSES",
    "FRAME_SOURCES",
    "PAGE_ERROR_KINDS",
    "PAGE_STATUSES",
    "STATE_SCHEMA_VERSION",
    "STATE_WRITER",
    "WRITING_ID",
    "WritingState",
    "WritingStateConflict",
    "WritingStateError",
    "WritingStateStore",
    "draft_page_for",
    "draft_pages",
    "draft_section_for",
    "draft_sections",
    "row_version",
    "state_summary",
]
