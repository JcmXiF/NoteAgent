"""The project's writing state: one truth for search, pages, frames and draft.

The state is a single versioned document per project. Every writer goes through
:class:`WritingStateStore`, which takes the project's lock, writes atomically,
and bumps ``stateVersion``. A missing file is an empty state; a damaged one is an
error, never a silent reset.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from noteagent.agent.clock import Clock, SystemClock
from noteagent.io.atomic import write_text_atomic
from noteagent.pipeline.lock import ProjectLock
from noteagent.project.layout import ProjectLayout

#: The schema version written by this module. A state from another schema is not
#: read as if it were this one.
STATE_SCHEMA_VERSION = 1

#: The owner label the store uses while it holds the project lock. The state's
#: own ``writer`` field is set by the run holding the lock, not by this store.
STATE_WRITER = "writing-state"


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
            selected_pages=_items(raw.get("selectedPages"), "selectedPages"),
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
        lock.acquire(owner)
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


def state_summary(state: WritingState) -> dict[str, object]:
    """The bounded answer ``get_state()`` gives: counts, writer, attention."""
    pages = _draft_pages(state.draft)
    sections = sum(len(_draft_sections(page)) for page in pages)
    return {
        "stateVersion": state.state_version,
        "writer": dict(state.writer) if state.writer is not None else None,
        "areas": {
            "searches": {"count": len(state.searches)},
            "selectedPages": {"count": len(state.selected_pages)},
            "frames": {"count": len(state.frames)},
            "draft": {"pages": len(pages), "sections": sections},
        },
        "attention": [dict(item) for item in state.attention],
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


def _draft_pages(draft: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = draft.get("pages")
    if not isinstance(raw, list):
        return []
    return [page for page in raw if isinstance(page, Mapping)]


def _draft_sections(page: Mapping[str, object]) -> list[object]:
    raw = page.get("sections")
    return list(raw) if isinstance(raw, list) else []


__all__ = [
    "STATE_SCHEMA_VERSION",
    "STATE_WRITER",
    "WritingState",
    "WritingStateConflict",
    "WritingStateError",
    "WritingStateStore",
    "state_summary",
]
