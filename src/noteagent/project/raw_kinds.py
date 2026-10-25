"""What kind of original a stored raw file is, and where that kind sits.

A raw file lives one level under ``raw/``, in the folder named after its kind.
The folder is the storage policy — HTML captured from the web, a PDF, an Office
package, a standalone image — not a subject classification, so it is the only
folder a new project ever creates.

An older project files its originals under ``raw/sources/<kind>/`` and often
below a host folder. Nothing here refuses to read that: the first segment naming
a kind is the kind, wherever it sits in the path.
"""

from __future__ import annotations

from noteagent.project.layout import RAW_DIR

WEB_KIND = "web"
PDF_KIND = "pdf"
OFFICE_KIND = "office"
MEDIA_KIND = "media"

#: The kind folders, in the order the interface lists them.
RAW_KINDS: tuple[str, ...] = (WEB_KIND, PDF_KIND, OFFICE_KIND, MEDIA_KIND)

#: The folder an unmigrated project files originals under.
LEGACY_RAW_SOURCES = f"{RAW_DIR}/sources"


def is_raw_path(relative_path: str) -> bool:
    """Whether a project-relative path names something kept under ``raw/``."""
    text = relative_path.replace("\\", "/").strip().lstrip("./")
    return text.startswith(f"{RAW_DIR}/") and len(text) > len(RAW_DIR) + 1


def raw_kind(relative_path: str) -> str:
    """The kind folder a raw file sits in, or ``""`` when the path names none.

    A legacy file under ``raw/sources/web/<host>/x.html`` is a web original: the
    first segment naming a kind wins, wherever it sits.
    """
    text = relative_path.replace("\\", "/").strip().lstrip("./")
    if not text.startswith(f"{RAW_DIR}/"):
        return ""
    for segment in text[len(RAW_DIR) + 1 :].split("/")[:-1]:
        lowered = segment.lower()
        if lowered in RAW_KINDS:
            return lowered
    return ""


__all__ = [
    "LEGACY_RAW_SOURCES",
    "MEDIA_KIND",
    "OFFICE_KIND",
    "PDF_KIND",
    "RAW_KINDS",
    "WEB_KIND",
    "is_raw_path",
    "raw_kind",
]
