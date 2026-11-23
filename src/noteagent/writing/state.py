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
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from noteagent.agent.clock import Clock, SystemClock, iso_timestamp
from noteagent.ingest.parse_file_blocks import is_safe_ingest_path
from noteagent.io.atomic import write_bytes_atomic, write_text_atomic
from noteagent.pipeline.lock import ProjectBusy, ProjectLock
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.links import normalize_wikilink_target

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

#: A run's writer lease is presumed dead after this long without a release.
WRITER_TTL_S = 15 * 60

#: The archived state file names this build writes: the archived stateVersion.
_ARCHIVE_ID = re.compile(r"\A[0-9]{6,}\Z")

#: The statuses and failure kinds a selected page may carry. The wire is JSON,
#: so a hand-edited state is checked here rather than trusted.
PAGE_STATUSES = frozenset({"fetching", "ready", "failed"})
#: The statuses that mean a page row is the current one for its URL.
ACTIVE_PAGE_STATUSES = frozenset({"fetching", "ready"})
PAGE_ERROR_KINDS = frozenset({"fetch_failed", "login_wall"})
FRAME_SOURCES = frozenset({"agent", "human"})
DRAFT_STATUSES = frozenset({"draft", "written"})

#: Why a section's pinned source drifted: stale sections still publish (with a
#: warning), while missing sources must refuse the write.
SECTION_STALE_REASONS = frozenset(
    {"frame_changed", "page_changed"}
)
SECTION_MISSING_REASONS = frozenset(
    {"frame_missing", "page_missing", "blocks_missing"}
)

#: The id shape a selected page, a frame and a draft section share.
WRITING_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,80}\Z")
_SOURCE_SUFFIX = re.compile(r"\A\.[A-Za-z0-9]{1,10}\Z")


class WritingStateError(RuntimeError):
    """The writing state cannot be read as the state this build knows."""


class WritingStateConflict(RuntimeError):
    """The caller worked from an older state than the one on disk."""

    def __init__(
        self, message: str, *, current_version: int = 0
    ) -> None:
        super().__init__(message)
        self.current_version = current_version


class WritingStateBusy(RuntimeError):
    """An agent or node run holds the state, so a person cannot save."""


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
        with self._locked(owner):
            current = self.load()
            if state.state_version != current.state_version:
                raise WritingStateConflict(
                    f"the writing state is at version "
                    f"{current.state_version}; the caller read version "
                    f"{state.state_version}",
                    current_version=current.state_version,
                )
            return self._write_locked(current, state)

    def save_human(
        self, base_version: int, changes: Mapping[str, object]
    ) -> WritingState:
        """Apply a person's edits; refuse while a run holds the state."""
        with self._locked(STATE_WRITER):
            current = self.load()
            self._refuse_busy(current)
            if current.state_version != base_version:
                raise WritingStateConflict(
                    f"the writing state is at version "
                    f"{current.state_version}; the caller read version "
                    f"{base_version}",
                    current_version=current.state_version,
                )
            next_state = self.refresh_provenance(
                _human_edit(current, changes, self._clock)
            )
            return self._write_locked(current, next_state)

    def ensure_writer(self, kind: str, ident: str) -> WritingState:
        """Take the lease for a run's write, renewing it while the run lives.

        The renewal is a heartbeat: a long run that keeps writing keeps its
        ``since`` fresh, so only a run that stopped writing for the whole TTL
        can be taken over.
        """
        with self._locked(STATE_WRITER):
            current = self.load()
            writer = current.writer or {}
            if (
                _writer_active(current.writer, self._now())
                and str(writer.get("kind")) == kind
                and str(writer.get("id")) == ident
            ):
                return self._write_locked(
                    current,
                    replace(
                        current,
                        writer={
                            **writer,
                            "since": iso_timestamp(self._clock),
                        },
                    ),
                )
            if _writer_active(current.writer, self._now()):
                raise WritingStateBusy(
                    _busy_message(current.writer)
                )
            next_state = replace(
                current,
                writer={
                    "kind": kind,
                    "id": ident,
                    "since": iso_timestamp(self._clock),
                },
            )
            return self._write_locked(current, next_state)

    def release_writer(self, ident: str) -> WritingState:
        """Release the lease when this run still holds it."""
        current = self.load()
        writer = current.writer
        if not isinstance(writer, Mapping):
            return current
        if str(writer.get("id")) != ident:
            return current
        with self._locked(STATE_WRITER):
            current = self.load()
            writer = current.writer
            if not isinstance(writer, Mapping):
                return current
            if str(writer.get("id")) != ident:
                return current
            return self._write_locked(
                current, replace(current, writer=None)
            )

    def archives(self) -> list[dict[str, object]]:
        """The archived states, by name, with a small summary."""
        folder = self._archive_dir
        if not folder.is_dir():
            return []
        items: list[dict[str, object]] = []
        for path in sorted(folder.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, Mapping):
                continue
            try:
                state = WritingState.from_dict(raw)
            except WritingStateError:
                continue
            items.append(
                {
                    "id": path.stem,
                    "stateVersion": state.state_version,
                    "archivedAt": str(raw.get("archivedAt") or ""),
                    "searches": len(state.searches),
                    "selectedPages": len(state.selected_pages),
                    "frames": len(state.frames),
                    "draftPages": len(draft_pages(state.draft)),
                }
            )
        return items

    def archive(self) -> list[dict[str, object]]:
        """Put the current state away and continue from an empty one."""
        with self._locked(STATE_WRITER):
            current = self.load()
            self._refuse_busy(current)
            if not _is_empty(current):
                self._archive_current(current)
            self._write_locked(current, WritingState())
        return self.archives()

    def activate(self, archive_id: str) -> WritingState:
        """Make an archived state current again, putting the current one away."""
        if _ARCHIVE_ID.fullmatch(archive_id or "") is None:
            raise ValueError(f"no archived state: {archive_id}")
        path = self._archive_dir / f"{archive_id}.json"
        if not path.is_file():
            raise ValueError(f"no archived state: {archive_id}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            raise WritingStateError(
                f"the archived state cannot be read: {err}"
            ) from err
        restored = WritingState.from_dict(raw)
        with self._locked(STATE_WRITER):
            current = self.load()
            self._refuse_busy(current)
            if not _is_empty(current):
                self._archive_current(current)
            return self._write_locked(
                current, replace(restored, writer=None)
            )

    @property
    def _archive_dir(self) -> Path:
        return self._layout.writing / "archive"

    @contextmanager
    def _locked(self, owner: str) -> Iterator[None]:
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
            yield
        finally:
            lock.release(owner)

    def _write_locked(
        self, current: WritingState, state: WritingState
    ) -> WritingState:
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

    def _refuse_busy(self, state: WritingState) -> None:
        if not _writer_active(state.writer, self._now()):
            return
        raise WritingStateBusy(_busy_message(state.writer))

    def _now(self) -> datetime:
        moment = self._clock.now()
        return (
            moment
            if moment.tzinfo is not None
            else moment.replace(tzinfo=timezone.utc)
        )

    def _archive_current(self, state: WritingState) -> None:
        folder = self._archive_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{state.state_version:06d}.json"
        payload = {
            **state.to_dict(),
            "archivedAt": iso_timestamp(self._clock),
        }
        write_text_atomic(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def append_search(self, row: Mapping[str, object]) -> WritingState:
        """Append one search row, retrying once if another writer moved first."""
        return self._append(
            row,
            lambda state, item: replace(
                state, searches=state.searches + (item,)
            ),
        )

    def append_attention(
        self, row: Mapping[str, object]
    ) -> WritingState:
        """Append one attention row, retrying once on a version conflict."""
        return self._append(
            row,
            lambda state, item: replace(
                state, attention=state.attention + (item,)
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
        """Replace one selected page, re-deriving what cites it.

        A page's status or version moving is exactly what makes a section's
        pinned frame drift, so the same write refreshes the provenance flags.
        """
        return self._append(
            row,
            lambda state, item: self.refresh_provenance(
                replace(
                    state,
                    selected_pages=tuple(
                        item
                        if str(page.get("id")) == page_id
                        else page
                        for page in state.selected_pages
                    ),
                )
            ),
        )

    def select_page_row(
        self,
        url: str,
        build: Callable[
            [Mapping[str, object] | None],
            Mapping[str, object] | None,
        ],
    ) -> tuple[WritingState, Mapping[str, object] | None, bool]:
        """Find or write the one page row for *url*.

        ``build`` receives the page already selected for this URL (an active
        page first, else a failed one) and returns the row to write, or
        ``None`` to keep the existing page. Two calls for one URL are resolved
        by the state version check: the loser re-runs ``build`` against the
        winner's row, so a duplicate is never appended; the callback must
        therefore be safe to run twice. The last value says a row was written.
        """
        for attempt in range(2):
            state = self.load()
            existing = _page_for_url(state.selected_pages, url)
            row = build(existing)
            if row is None:
                return state, existing, False
            pages = [dict(item) for item in state.selected_pages]
            index = next(
                (
                    position
                    for position, page in enumerate(pages)
                    if str(page.get("id")) == str(row.get("id"))
                ),
                None,
            )
            if index is None:
                pages.append(dict(row))
            else:
                pages[index] = dict(row)
            next_state = self.refresh_provenance(
                replace(state, selected_pages=tuple(pages))
            )
            try:
                return self.save(next_state), row, True
            except WritingStateConflict:
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def refresh_provenance(self, state: WritingState) -> WritingState:
        """Re-derive every section's stale flag and reason from its sources."""
        frames, pages, blocks_for = self._provenance_lookup(state)
        pages_out: list[Mapping[str, object]] = []
        for page in draft_pages(state.draft):
            sections: list[Mapping[str, object]] = []
            for section in draft_sections(page):
                reason = section_reason(
                    section, frames, pages, blocks_for
                )
                sections.append(
                    {
                        **section,
                        "stale": bool(reason),
                        "staleReason": reason,
                    }
                )
            pages_out.append({**page, "sections": sections})
        return replace(state, draft={"pages": pages_out})

    def _provenance_lookup(
        self, state: WritingState
    ) -> tuple[
        dict[str, Mapping[str, object]],
        dict[str, Mapping[str, object]],
        Callable[[str], set[str] | None],
    ]:
        frames = {
            str(frame.get("id")): frame for frame in state.frames
        }
        pages = {
            str(page.get("id")): page
            for page in state.selected_pages
        }
        cache: dict[str, set[str] | None] = {}

        def blocks_for(page_id: str) -> set[str] | None:
            if page_id not in cache:
                try:
                    cache[page_id] = {
                        str(block.get("id"))
                        for block in self.load_page_blocks(page_id)
                    }
                except (OSError, WritingStateError):
                    cache[page_id] = None
            return cache[page_id]

        return frames, pages, blocks_for

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
        *,
        overwrite: bool = False,
    ) -> tuple[
        WritingState, Mapping[str, object], tuple[Mapping[str, object], ...]
    ]:
        """Merge sections into one draft page, checking its base version.

        An existing section id is replaced where it stands (its ``baseVersion``
        must match); a new id is inserted after its ``after`` anchor or
        appended. A section a person edited is refused unless the caller passes
        ``overwrite`` after re-reading it. Frame ids are resolved here, so the
        citation pins the frame version and block ids the page was written
        from. The page's version moves on by one, and a caller built on an
        older page is refused.
        """
        raw_key = wiki_file.strip()
        if not raw_key:
            raise ValueError("a draft page needs a wiki file")
        key = canonical_wiki_file(raw_key)
        if base_version < 0:
            raise ValueError("baseDraftVersion must be non-negative")
        for attempt in range(2):
            state = self.load()
            frames = {
                str(frame.get("id")): frame for frame in state.frames
            }
            pages = [dict(page) for page in draft_pages(state.draft)]
            index = _page_index_for(pages, raw_key)
            if index is not None and pages[index]["wikiFile"] != key:
                # An older draft kept the caller's spelling; move it onto the
                # canonical key instead of making a second draft.
                pages[index]["wikiFile"] = key
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
                    if old.get("humanEdited") and not overwrite:
                        raise ValueError(
                            f"section {section_id} was edited by a "
                            "person; pass overwrite to replace it"
                        )
                    new = {
                        **old,
                        "heading": heading,
                        "body": body,
                        "citations": citations,
                        "version": old_version + 1,
                        "stale": False,
                        "humanEdited": False,
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
            next_state = self.refresh_provenance(
                replace(state, draft={"pages": pages})
            )
            refreshed = draft_pages(next_state.draft)[index]
            refreshed_by_id = {
                str(item.get("id")): item
                for page_item in draft_pages(next_state.draft)
                for item in draft_sections(page_item)
            }
            changed_rows = tuple(
                refreshed_by_id.get(str(item.get("id")), item)
                for item in changed
            )
            try:
                return (
                    self.save(next_state),
                    refreshed,
                    changed_rows,
                )
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

    def save_page_material(
        self, page_id: str, data: bytes, material: str
    ) -> None:
        """Keep the selected page's original beside its state."""
        path = self._page_material_file(
            page_id, _material_suffix(material)
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(path, data)

    def page_material(
        self, page_id: str, material: str
    ) -> Path | None:
        """The original a selected page was captured as, when still kept."""
        path = self._page_material_file(
            page_id, _material_suffix(material)
        )
        return path if path.is_file() else None

    def remove_page_material(self, page_id: str, material: str) -> None:
        """Drop the state's copy once the original is durable under raw/."""
        path = self._page_material_file(
            page_id, _material_suffix(material)
        )
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def _page_material_file(self, page_id: str, suffix: str) -> Path:
        key = (page_id or "").strip()
        if WRITING_ID.fullmatch(key) is None:
            raise ValueError(f"not a page id: {page_id!r}")
        if _SOURCE_SUFFIX.fullmatch(suffix) is None:
            raise ValueError(f"not an original suffix: {suffix!r}")
        return self._layout.writing_pages / f"{key}.material{suffix}"

    def _page_file(self, page_id: str) -> Path:
        key = (page_id or "").strip()
        if WRITING_ID.fullmatch(key) is None:
            raise ValueError(f"not a page id: {page_id!r}")
        return self._layout.writing_pages / f"{key}.json"

    def checked_draft_page(
        self, wiki_file: str, draft_version: int
    ) -> tuple[WritingState, Mapping[str, object]]:
        """Load the state and one draft page at *draft_version*, or raise."""
        state = self.load()
        return state, _checked_draft_page(
            state, wiki_file, draft_version
        )

    def mark_draft_written(
        self,
        wiki_file: str,
        draft_version: int,
        effect_ids: Sequence[str],
        written_at: str,
    ) -> WritingState:
        """Record a published page; its bytes are already on disk."""

        def build(state: WritingState) -> WritingState:
            page = _checked_draft_page(
                state, wiki_file, draft_version
            )
            pages = [dict(item) for item in draft_pages(state.draft)]
            index = _page_index_for(pages, wiki_file)
            if index is None:
                raise ValueError(f"no draft page: {wiki_file}")
            pages[index] = {
                **page,
                "status": "written",
                "writtenAt": written_at,
                "effectIds": list(effect_ids),
            }
            return replace(state, draft={"pages": pages})

        return self._mutate(build)

    def _mutate(
        self, build: Callable[[WritingState], WritingState]
    ) -> WritingState:
        for attempt in range(2):
            state = self.load()
            next_state = build(state)
            try:
                return self.save(next_state)
            except WritingStateConflict:
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def _append(
        self,
        row: Mapping[str, object],
        build: Callable[
            [WritingState, Mapping[str, object]], WritingState
        ],
    ) -> WritingState:
        return self._mutate(lambda state: build(state, row))


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
    attention.extend(
        {
            "kind": (
                "stale_section"
                if reason in SECTION_STALE_REASONS
                else "missing_target"
            ),
            "reason": reason,
            "target": f"{page.get('wikiFile')}#{section.get('id')}",
            "message": (
                f"section {section.get('id')} in "
                f"{page.get('wikiFile')} is "
                f"{reason.replace('_', ' ')}"
            ),
        }
        for page in pages
        for section in draft_sections(page)
        if (reason := str(section.get("staleReason") or ""))
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
            reason = section.get("staleReason", "")
            if not isinstance(reason, str):
                raise WritingStateError(
                    "a draft section's staleReason must be a string"
                )
            if reason and reason not in (
                SECTION_STALE_REASONS | SECTION_MISSING_REASONS
            ):
                raise WritingStateError(
                    f"unknown draft section stale reason: {reason!r}"
                )
        pages.append(dict(page))
    return {"pages": pages}


def _material_suffix(material: str) -> str:
    suffix = Path(material).suffix
    return suffix if _SOURCE_SUFFIX.fullmatch(suffix) else ".bin"


def _busy_message(writer: Mapping[str, object] | None) -> str:
    record = writer or {}
    return (
        "the writing state is held by "
        f"{record.get('kind') or 'a run'} "
        f"{record.get('id') or ''}".rstrip()
    )


def _writer_active(
    writer: Mapping[str, object] | None, now: datetime
) -> bool:
    """Whether a claimed writer still holds the state, or its lease went stale."""
    if not isinstance(writer, Mapping):
        return False
    since = str(writer.get("since") or "")
    if not since:
        return True
    try:
        moment = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (now - moment).total_seconds() < WRITER_TTL_S


def _is_empty(state: WritingState) -> bool:
    return not (
        state.searches
        or state.selected_pages
        or state.frames
        or draft_pages(state.draft)
    )


def section_reason(
    section: Mapping[str, object],
    frames: Mapping[str, Mapping[str, object]],
    pages: Mapping[str, Mapping[str, object]],
    blocks_for: Callable[[str], set[str] | None],
) -> str:
    """Why a section's citations no longer match, or "" while they do.

    A missing source wins over a version drift: the write refuses the first,
    while the second only marks the section stale for the human or the agent.
    """
    raw = section.get("citations")
    citations = raw if isinstance(raw, list) else []
    drift = ""
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        frame = frames.get(str(citation.get("frameId") or "").strip())
        if frame is None:
            return "frame_missing"
        page = pages.get(str(frame.get("pageId") or ""))
        if page is None or str(page.get("status") or "") != "ready":
            return "page_missing"
        blocks = blocks_for(str(page.get("id") or ""))
        if blocks is None:
            return "blocks_missing"
        pinned = [str(item) for item in citation.get("blockIds") or []]
        if any(item not in blocks for item in pinned):
            return "blocks_missing"
        if not drift:
            version = citation.get("frameVersion")
            current = version if isinstance(version, int) else 0
            if current != row_version(frame):
                drift = "frame_changed"
        if not drift:
            page_version = frame.get("pageVersion")
            pinned_page = (
                page_version if isinstance(page_version, int) else 0
            )
            if pinned_page != row_version(page):
                drift = "page_changed"
    return drift


def _citations_key(section: Mapping[str, object]) -> tuple[object, ...]:
    raw = section.get("citations")
    items = raw if isinstance(raw, list) else []
    keys: list[tuple[object, ...]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        version = item.get("frameVersion")
        keys.append(
            (
                str(item.get("frameId") or ""),
                version if isinstance(version, int) else 0,
                tuple(
                    str(block) for block in item.get("blockIds") or []
                ),
            )
        )
    return tuple(keys)


def _section_changed(
    old: Mapping[str, object], new: Mapping[str, object]
) -> bool:
    return (
        str(old.get("heading") or "") != str(new.get("heading") or "")
        or str(old.get("body") or "") != str(new.get("body") or "")
        or _citations_key(old) != _citations_key(new)
    )


def _row_changed(
    old: Mapping[str, object],
    new: Mapping[str, object],
    fields: Sequence[str],
) -> bool:
    return any(old.get(field) != new.get(field) for field in fields)


def _merge_pages(
    old_rows: Sequence[Mapping[str, object]],
    new_rows: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """A page's version is its capture's; a person's title edit is not a new one."""
    previous = {str(row.get("id")): row for row in old_rows}
    out: list[Mapping[str, object]] = []
    for row in new_rows:
        old = previous.get(str(row.get("id")))
        out.append(
            {
                **row,
                "version": (
                    row_version(old)
                    if old is not None
                    else row_version(row)
                ),
            }
        )
    return tuple(out)


def _bump_frames(
    old_rows: Sequence[Mapping[str, object]],
    new_rows: Sequence[Mapping[str, object]],
    clock: Clock,
) -> tuple[Mapping[str, object], ...]:
    """Frames a person touched become theirs, with a fresh version and stamp."""
    previous = {str(row.get("id")): row for row in old_rows}
    stamp = iso_timestamp(clock)
    out: list[Mapping[str, object]] = []
    for row in new_rows:
        old = previous.get(str(row.get("id")))
        if old is None:
            out.append(
                {
                    **row,
                    "version": max(1, row_version(row)),
                    "source": "human",
                    "updatedAt": stamp,
                }
            )
        elif _row_changed(old, row, ("pageId", "blockIds")):
            out.append(
                {
                    **row,
                    "version": row_version(old) + 1,
                    "source": "human",
                    "updatedAt": stamp,
                }
            )
        else:
            out.append(
                {
                    **row,
                    "version": row_version(old),
                    "source": str(old.get("source") or "agent"),
                    "updatedAt": str(old.get("updatedAt") or ""),
                }
            )
    return tuple(out)


def _human_edit(
    state: WritingState,
    changes: Mapping[str, object],
    clock: Clock,
) -> WritingState:
    """Apply one person's change set, marking what they touched."""
    raw = dict(changes)
    searches = (
        _items(raw["searches"], "searches")
        if "searches" in raw
        else state.searches
    )
    selected = (
        _selected_pages(raw["selectedPages"])
        if "selectedPages" in raw
        else state.selected_pages
    )
    frames = (
        _frames(raw["frames"])
        if "frames" in raw
        else state.frames
    )
    draft = (
        _draft(raw["draft"]) if "draft" in raw else state.draft
    )
    pages: list[Mapping[str, object]] = []
    for page in draft_pages(draft):
        wiki_file = str(page.get("wikiFile") or "")
        old_page = draft_page_for(state.draft, wiki_file)
        old_sections = (
            {
                str(section.get("id")): section
                for section in draft_sections(old_page)
            }
            if old_page is not None
            else {}
        )
        sections: list[Mapping[str, object]] = []
        seen: set[str] = set()
        page_changed = old_page is None
        for section in draft_sections(page):
            item = dict(section)
            ident = str(item.get("id") or "")
            if ident in seen:
                raise ValueError(f"duplicate section id: {ident}")
            seen.add(ident)
            old = old_sections.get(ident)
            if old is None or _section_changed(old, item):
                item["version"] = (
                    max(1, row_version(old) + 1)
                    if old is not None
                    else max(1, row_version(item))
                )
                item["humanEdited"] = True
                page_changed = True
            else:
                item["version"] = row_version(old)
                item["humanEdited"] = bool(
                    old.get("humanEdited", False)
                )
            sections.append(item)
        old_order = (
            [str(item.get("id")) for item in draft_sections(old_page)]
            if old_page is not None
            else []
        )
        new_order = [str(item.get("id")) for item in sections]
        if old_order != new_order:
            page_changed = True
        updated = {**page, "sections": sections}
        if old_page is not None:
            if not str(updated.get("title") or "").strip():
                updated["title"] = old_page.get("title", "")
            # Publication bookkeeping belongs to write_wiki, not to a save.
            updated["writtenAt"] = old_page.get("writtenAt", "")
            updated["effectIds"] = list(
                old_page.get("effectIds") or []
            )
        else:
            updated["writtenAt"] = ""
            updated["effectIds"] = []
        if old_page is None:
            updated["version"] = max(1, row_version(page))
            updated["status"] = "draft"
        elif page_changed:
            updated["version"] = row_version(old_page) + 1
            updated["status"] = "draft"
        else:
            updated["version"] = row_version(old_page)
            updated["status"] = str(
                old_page.get("status") or "draft"
            )
        pages.append(updated)
    return replace(
        state,
        writer=None,
        searches=searches,
        selected_pages=_merge_pages(state.selected_pages, selected),
        frames=_bump_frames(state.frames, frames, clock),
        draft={"pages": pages},
    )


def row_version(row: Mapping[str, object]) -> int:
    """A mapping's version, or 0 when it has none; an invalid value reads as 0."""
    value = row.get("version")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _url_key(url: str) -> str:
    """A URL's identity for dedupe, ignoring cosmetic differences."""
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _page_for_url(
    pages: Sequence[Mapping[str, object]], url: str
) -> Mapping[str, object] | None:
    """The page already selected for a URL: an active one first, else failed."""
    key = _url_key(url)
    active: Mapping[str, object] | None = None
    failed: Mapping[str, object] | None = None
    for page in pages:
        if _url_key(str(page.get("url") or "")) != key:
            continue
        status = str(page.get("status") or "")
        if status in ACTIVE_PAGE_STATUSES:
            active = page
        elif status == "failed":
            failed = page
    return active if active is not None else failed


def canonical_wiki_file(value: str) -> str:
    """The one key for a wiki page: ``wiki/<slug>.md``.

    Accepts a slug (``concepts/熵``), a wiki path (``wiki/concepts/熵.md``)
    or a wikilink (``[[concepts/熵]]``). An input that would not be a safe
    page under ``wiki/`` is refused with the forms that work.
    """
    raw = str(value or "").strip()
    if raw.startswith("[[") and raw.endswith("]]"):
        raw = raw[2:-2].strip()
    raw = raw.replace("\\", "/").strip()
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        raise ValueError(
            f"{value!r} is not a wiki page; use a slug such as "
            "'concepts/信息熵' or a path such as 'wiki/concepts/信息熵.md'"
        )
    slug = normalize_wikilink_target(raw)
    candidate = f"wiki/{slug}.md"
    if not slug or not is_safe_ingest_path(candidate):
        raise ValueError(
            f"{value!r} is not a wiki page; use a slug such as "
            "'concepts/信息熵' or a path such as 'wiki/concepts/信息熵.md'"
        )
    return candidate


def draft_page_for(
    draft: Mapping[str, object], wiki_file: str
) -> Mapping[str, object] | None:
    """One draft page by its wiki file, if it has been composed.

    A legacy draft composed under a non-canonical key still answers, because
    the canonical key is tried first and the raw one second.
    """
    pages = draft_pages(draft)
    index = _page_index_for(pages, wiki_file)
    return None if index is None else pages[index]


def _key_candidates(value: str) -> tuple[str, ...]:
    """The canonical key first, then the caller's own spelling for legacy rows."""
    raw = str(value or "").strip()
    try:
        canonical = canonical_wiki_file(raw)
    except ValueError:
        return (raw,) if raw else ()
    return (canonical,) if canonical == raw else (canonical, raw)


def _checked_draft_page(
    state: WritingState, wiki_file: str, draft_version: int
) -> Mapping[str, object]:
    page = draft_page_for(state.draft, wiki_file)
    if page is None:
        raise ValueError(f"no draft page: {wiki_file}")
    current = row_version(page)
    if current != draft_version:
        raise ValueError(
            f"draft {wiki_file} is at version "
            f"{current}; the caller read {draft_version}"
        )
    return page


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


def _page_index_for(
    pages: Sequence[Mapping[str, object]], wiki_file: str
) -> int | None:
    """The index of the page a canonical or legacy spelling addresses."""
    for key in _key_candidates(wiki_file):
        index = _page_index(pages, key)
        if index is not None:
            return index
    return None


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
    "SECTION_MISSING_REASONS",
    "SECTION_STALE_REASONS",
    "STATE_SCHEMA_VERSION",
    "STATE_WRITER",
    "WRITER_TTL_S",
    "WRITING_ID",
    "WritingState",
    "WritingStateBusy",
    "WritingStateConflict",
    "WritingStateError",
    "WritingStateStore",
    "draft_page_for",
    "draft_pages",
    "draft_section_for",
    "draft_sections",
    "row_version",
    "section_reason",
    "state_summary",
]
