"""Where a wiki page a writer was handed actually lands.

A page is named relative to the project root, and that name arrives from outside
the kernel: a FILE block in a generation, a path in an API request. Resolving it
checks twice — the name itself, then the resolved path against ``wiki/`` — so a
name that walks out of the wiki (``../``, an absolute path, a symlink) is refused
before anything is read or written.

Kept apart from any one writer because everything that writes a page needs it:
the pipeline's page writer, the hand-edit path, and the page history that keeps
the version a write is about to replace.
"""

from __future__ import annotations

from pathlib import Path

from noteagent.ingest.parse_file_blocks import is_safe_ingest_path
from noteagent.project.layout import ProjectLayout

__all__ = ["resolve_wiki_write_path"]


def resolve_wiki_write_path(
    layout: ProjectLayout,
    relative_path: str,
) -> Path:
    """
    Resolve a FILE block path under the project root and re-check containment.
    Raises ValueError if the path is unsafe or escapes ``wiki/`` after resolve.
    """

    if not is_safe_ingest_path(relative_path):
        raise ValueError(f'unsafe ingest path: "{relative_path}"')

    normalized = relative_path.replace("\\", "/")
    candidate = (layout.root / normalized).resolve()
    wiki_root = layout.wiki.resolve()

    try:
        candidate.relative_to(wiki_root)
    except ValueError as err:
        raise ValueError(
            f'resolved path escapes wiki/: "{relative_path}" -> {candidate}'
        ) from err

    return candidate
