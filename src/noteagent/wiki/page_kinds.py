"""The two kinds of wiki page, told apart by the folder that holds them.

A page is either a note, directly under ``wiki/``, or a source page, under
``wiki/sources/``. That is the whole taxonomy: there is no page type field, and
the retired type folders (``concepts/``, ``entities/`` and the rest) are read as
notes until migration moves them.
"""

from __future__ import annotations

__all__ = [
    "LEGACY_TYPE_DIRS",
    "NOTE_KIND",
    "SOURCE_DIR",
    "SOURCE_KIND",
    "folder_of",
    "is_legacy_type_page",
    "is_source_page",
    "page_kind",
]

SOURCE_DIR = "sources"
SOURCE_KIND = "source"
NOTE_KIND = "note"

#: Folders the old six-way taxonomy used. A page here is a note, not an error.
LEGACY_TYPE_DIRS: frozenset[str] = frozenset(
    {"concepts", "entities", "queries", "comparisons", "synthesis"}
)

_PREFIX = "wiki/"
_SOURCE_PREFIX = f"{_PREFIX}{SOURCE_DIR}/"


def _relative_under_wiki(relative_path: str) -> str:
    return relative_path.replace("\\", "/").strip().lstrip("./")


def folder_of(relative_path: str) -> str:
    """The first folder under ``wiki/`` a page sits in, or ``""`` for a note."""
    normalized = _relative_under_wiki(relative_path)
    if not normalized.lower().startswith(_PREFIX):
        return ""
    rest = normalized[len(_PREFIX) :]
    if "/" not in rest:
        return ""
    return rest.split("/", 1)[0].lower()


def is_source_page(relative_path: str) -> bool:
    """Whether the page is one source's summary, under ``wiki/sources/``."""
    normalized = _relative_under_wiki(relative_path).lower()
    return normalized.startswith(_SOURCE_PREFIX)


def page_kind(relative_path: str) -> str:
    """``source`` for a page under ``wiki/sources/``, ``note`` for everything else."""
    return SOURCE_KIND if is_source_page(relative_path) else NOTE_KIND


def is_legacy_type_page(relative_path: str) -> bool:
    """Whether the page still sits in a folder the retired taxonomy named."""
    return folder_of(relative_path) in LEGACY_TYPE_DIRS
