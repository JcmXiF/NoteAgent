"""The name a stored document gives itself.

A stored original is filed under the address it came from plus a code, so its file
name is often an address leaf rather than a name. Most kinds of document carry a
name of their own — a page's ``<title>``, a PDF's metadata, an Office file's core
properties — and reading that is what lets a list show what a file *is* rather than
where it came from.

Nothing here reads a document through: a title lives in a page's head, in a PDF's
metadata dictionary, or in one small part of an Office package, so each reader takes
only what it needs. The file name stays the fallback, and is the caller's business:
this answers with an empty string rather than inventing a name.
"""

from __future__ import annotations

import html
import re
import zipfile
from collections.abc import Callable
from pathlib import Path
from xml.etree import ElementTree

#: How much of a page to read when its head never closes.
HEAD_BYTES = 16_384

_TITLE = re.compile(r"<title\b[^>]*>([\s\S]*?)</title>", re.IGNORECASE)
_HEAD_END = re.compile(r"</head>", re.IGNORECASE)
_URL = re.compile(r"^https?://", re.IGNORECASE)

#: Where the core properties sit inside an Office package.
_CORE_PART = "docprops/core.xml"

_HTML_SUFFIXES = frozenset({".html", ".htm"})
_OFFICE_SUFFIXES = frozenset({".docx", ".pptx"})


def document_title(path: Path, *, on_problem: Callable[[str], None] | None = None) -> str:
    """The title the file carries, or an empty string when it carries none.

    Never raises. A file that cannot be read is a file without a title, and the
    listing this is called from should not fail over one odd document. ``on_problem``
    is told why when a read fails, because a name that quietly disappears is worse
    than one that never worked — a page whose title vanished looks like a page with
    no title.
    """
    suffix = path.suffix.lower()
    for attempt in (1, 2):
        try:
            if suffix in _HTML_SUFFIXES:
                return _html_title(path)
            if suffix == ".pdf":
                return _pdf_title(path)
            if suffix in _OFFICE_SUFFIXES:
                return _office_title(path)
            return ""
        except Exception as err:  # noqa: BLE001 - a title is a nicety; the listing is not
            # Reading a document is a library call, and a library call that fails once
            # can work the next time; a second attempt costs a couple of milliseconds
            # and saves a name that would otherwise be silently replaced by the file's
            # address.
            if attempt == 2:
                if on_problem is not None:
                    on_problem(f"{type(err).__name__}: {err}")
                return ""
    return ""


def _clean(text: str) -> str:
    """A title on one line: a listing has no room for the wrapping."""
    return " ".join(text.split())


def _head(text: str) -> str:
    """The part of a page before ``</head>``, or the whole text when it has none."""
    end = _HEAD_END.search(text)
    return text[: end.start()] if end else text


def _html_title(path: Path) -> str:
    """The ``<title>`` of a page; an address stamped as a title is not a name."""
    with path.open("rb") as handle:
        data = handle.read(HEAD_BYTES)
    match = _TITLE.search(_head(data.decode("utf-8", errors="replace")))
    if not match:
        return ""
    title = _clean(html.unescape(match.group(1)))
    # A page captured without a title is stamped with its own address, and an
    # address is not a name: the file name says more.
    return "" if _URL.match(title) else title


def _pdf_title(path: Path) -> str:
    """The title in a PDF's metadata dictionary."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        return _clean(str(document.get_metadata_value("Title") or ""))
    finally:
        # The handle goes back now: a tree lists many files and holding them all
        # open is how a Windows machine ends up refusing to write them.
        document.close()


def _office_title(path: Path) -> str:
    """The title in an Office package's core properties."""
    with zipfile.ZipFile(path) as package:
        part = next(
            (name for name in package.namelist() if name.lower() == _CORE_PART),
            None,
        )
        if part is None:
            return ""
        xml = package.read(part).decode("utf-8", errors="replace")
    root = ElementTree.fromstring(xml)
    for node in root.iter():
        # The prefix varies (`dc:title`, `dcterms:title`), so the local name is
        # what identifies the element.
        if node.tag.rsplit("}", 1)[-1] != "title":
            continue
        title = _clean(node.text or "")
        if title:
            return title
    return ""
