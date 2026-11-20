"""HTTP helpers for changing a page and for finding one.

A request names what to change, never where it goes: the path is a page's own
address inside the project, and every rule about what may be edited or moved
belongs to :mod:`noteagent.wiki.editing`.
"""

from __future__ import annotations

from typing import Any

from noteagent.project.layout import ProjectLayout
from noteagent.task.writing import PageBusy, PageLocks
from noteagent.util.refusal import Refusal
from noteagent.wiki.editing import (
    page_backlinks,
    page_edit_view,
    page_versions,
    read_version,
    rename_page,
    restore_version,
    save_page,
)
from noteagent.wiki.local_search import search_project


def page_view_payload(layout: ProjectLayout, path: str) -> dict[str, Any]:
    """What an editor opens: the prose, the frontmatter, and the revision."""
    return page_edit_view(layout, path)


def page_save_payload(
    layout: ProjectLayout,
    body: dict[str, Any],
    *,
    page_locks: PageLocks | None = None,
) -> dict[str, Any]:
    """Save a page's prose, or preview the change when ``preview`` is set.

    A real save shares the page lock with the task's writing executor: the
    later writer is told the page is busy instead of interleaving with the one
    already writing it. A preview writes nothing and needs no lock. The editor's
    own base-revision check still covers a page written since it was opened.
    """
    path = str(body.get("path") or "")
    preview = bool(body.get("preview"))
    if preview or page_locks is None:
        return save_page(
            layout,
            path,
            str(body.get("body") or ""),
            base_revision=str(body.get("baseRevision") or ""),
            preview=preview,
        )
    try:
        with page_locks.holding(str(layout.root.resolve()), path):
            return save_page(
                layout,
                path,
                str(body.get("body") or ""),
                base_revision=str(body.get("baseRevision") or ""),
            )
    except PageBusy as err:
        raise Refusal(
            "page.busy",
            str(err),
            params={"path": path},
            status=409,
        ) from err


def page_rename_payload(layout: ProjectLayout, body: dict[str, Any]) -> dict[str, Any]:
    return rename_page(layout, str(body.get("path") or ""), str(body.get("to") or ""))


def page_backlinks_payload(layout: ProjectLayout, path: str) -> dict[str, Any]:
    return page_backlinks(layout, path)


def page_history_payload(layout: ProjectLayout, path: str) -> dict[str, Any]:
    return page_versions(layout, path)


def page_version_payload(
    layout: ProjectLayout, path: str, version_id: str
) -> dict[str, Any]:
    return read_version(layout, path, version_id)


def page_restore_payload(layout: ProjectLayout, body: dict[str, Any]) -> dict[str, Any]:
    return restore_version(
        layout,
        str(body.get("path") or ""),
        str(body.get("id") or ""),
        base_revision=str(body.get("baseRevision") or ""),
    )


def project_search_payload(
    layout: ProjectLayout,
    query: str,
    *,
    per_kind: int = 8,
) -> dict[str, Any]:
    return search_project(layout, query, per_kind=per_kind)


__all__ = [
    "page_backlinks_payload",
    "page_history_payload",
    "page_rename_payload",
    "page_restore_payload",
    "page_save_payload",
    "page_version_payload",
    "page_view_payload",
    "project_search_payload",
]
