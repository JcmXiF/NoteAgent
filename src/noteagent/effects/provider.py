"""Where an effect's before/after refs live, and how one is put back.

The effect journal records what changed; a snapshot provider owns the bytes
that let a change be inspected or undone. ``page_history`` is the first provider:
it already keeps every wiki page version under its own content digest. The
provider seam is deliberately small, so a raw-file or shell provider can arrive
without teaching the journal about any of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from noteagent.ingest.page_history import blob_digest, blob_path, keep_before
from noteagent.project.layout import ProjectLayout
from noteagent.util.refusal import Refusal
from noteagent.wiki.editing import restore_version
from noteagent.wiki.links import is_wiki_content_page, normalize_wiki_rel


@runtime_checkable
class SnapshotProvider(Protocol):
    """The four calls a restore needs from a version store."""

    def put(self, path: str, text: str) -> str:
        """Keep *text* for *path* and answer the ref it is kept under."""
        ...

    def get(self, ref: str) -> str:
        """The text *ref* names."""
        ...

    def current(self, path: str) -> str:
        """The ref for what *path* says now, or an empty string if it is gone."""
        ...

    def restore(
        self,
        path: str,
        ref: str,
        *,
        base: str = "",
        force: bool = False,
    ) -> str:
        """Put *ref* back at *path*; answer the new current ref.

        ``base`` is the revision the caller last saw. When it no longer matches,
        the restore is refused unless ``force`` is set.
        """
        ...


class PageHistoryProvider:
    """The wiki page history, behind the provider seam."""

    def __init__(self, layout: ProjectLayout) -> None:
        self._layout = layout

    def put(self, path: str, text: str) -> str:
        """Keep a version of a page before it is written.

        The path is checked as a wiki page even though it is not part of the
        blob's name: identical text is one blob however many pages held it. The
        page name lives in the history row, written by the writer.
        """
        _page_rel(path)
        return keep_before(self._layout, text)

    def get(self, ref: str) -> str:
        """Read one kept version; a ref that names nothing is a refusal."""
        try:
            path = blob_path(self._layout, ref)
        except ValueError as err:
            raise Refusal(
                "snapshot.missing",
                f"no such snapshot: {ref}",
                status=404,
            ) from err
        if not path.is_file():
            raise Refusal(
                "snapshot.missing",
                f"no such snapshot: {ref}",
                status=404,
            )
        return path.read_text(encoding="utf-8")

    def current(self, path: str) -> str:
        """The digest of a wiki page as it is now; empty when it is gone."""
        target = _target(self._layout, path)
        if not target.is_file():
            return ""
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        return blob_digest(text)

    def restore(
        self,
        path: str,
        ref: str,
        *,
        base: str = "",
        force: bool = False,
    ) -> str:
        """Put a kept version back through the page editor's restore path.

        ``force`` is how a person who has seen a conflicting edit says "put it
        back anyway"; without it, the editor's ``page.changed`` refusal stands.
        """
        result = restore_version(
            self._layout,
            path,
            ref,
            base_revision="" if force else base,
        )
        return str(result["revision"])


def _page_rel(relative: str) -> str:
    rel = normalize_wiki_rel(relative)
    if not is_wiki_content_page(rel):
        raise Refusal(
            "page.notAPage",
            f"not a wiki page: {relative}",
            params={"path": relative},
            status=400,
        )
    return rel


def _target(layout: ProjectLayout, relative: str) -> Path:
    rel = _page_rel(relative)
    target = layout.root.joinpath(*rel.split("/"))
    try:
        target.resolve().relative_to(layout.root.resolve())
    except ValueError as err:
        raise Refusal(
            "page.notAPage",
            f"path escapes the project: {relative}",
            params={"path": relative},
            status=400,
        ) from err
    return target


__all__ = ["PageHistoryProvider", "SnapshotProvider"]
