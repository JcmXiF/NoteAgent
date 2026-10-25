"""A markdown original as numbered blocks, so a passage of it can be picked.

The source reader shows an original as the thing it is, and a passage is chosen by
dragging a box over it. HTML, PDF, Office and image originals therefore carry
``data-na-block`` ids written by their adapters. A plain markdown file had none, so
its text could be read but never selected — which left exactly the sources written
in this product's own format as the ones that could not be compiled from.

This adapter gives a markdown file the same shape as the others: the lines it is
written from, cut into blocks where markdown itself draws lines (headings, fences,
list items, quotes, tables, paragraphs), each with an ``m1``-style id and a reader
document to show them in. What a block reports is the text as written, minus the
markers that are structure rather than content: selecting a passage hands the
compile the words the reader saw, not a second rendering of them.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Iterable

from noteagent.acquire.derive import DerivedBlock
from noteagent.acquire.render import wrap_article_document

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_RULE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_ITEM = re.compile(r"^( {0,3})([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_TABLE_ROW = re.compile(r"^ {0,3}\|")
_TABLE_DELIM = re.compile(
    r"^ {0,3}\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$"
)

#: The document's own spacing. The reader stylesheet sets the body; these are the
#: parts a source written in markdown has and a captured page may not.
_MARKDOWN_CSS = """
h1,h2,h3,h4,h5,h6{line-height:1.35;margin:1.4em 0 .5em;}
h1{font-size:1.5rem;}h2{font-size:1.3rem;}h3{font-size:1.15rem;}
h4,h5,h6{font-size:1rem;}
p,blockquote,table,pre{margin:0 0 1em;}
blockquote{border-left:3px solid #d8d8d8;margin-left:0;padding:.1rem 0 .1rem 1rem;color:#444;}
table td,table th{vertical-align:top;}
"""

_HTML_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "blockquote"})


def markdown_blocks(text: str) -> list[DerivedBlock]:
    """The file's blocks, in document order, with ``m1`` ids.

    Only blocks with text are reported: a rule or an empty heading is structure
    rather than something a reader could select, and a selection of nothing would
    only make a compile quieter than it should be.
    """
    lines = _body_lines(text)
    blocks: list[DerivedBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines):
                close = _FENCE.match(lines[index])
                if close is not None and _closes_fence(close, marker):
                    index += 1
                    break
                body.append(lines[index])
                index += 1
            _push(blocks, "pre", "\n".join(body))
            continue

        heading = _HEADING.match(line)
        if heading is not None:
            _push(blocks, f"h{len(heading.group(1))}", heading.group(2))
            index += 1
            continue

        if _RULE.match(line):
            index += 1
            continue

        if _QUOTE.match(line):
            quoted: list[str] = []
            while index < len(lines):
                inner = _QUOTE.match(lines[index])
                if inner is None:
                    break
                quoted.append(inner.group(1))
                index += 1
            _push(blocks, "blockquote", _joined(quoted))
            continue

        item = _ITEM.match(line)
        if item is not None:
            body = [item.group(3)]
            index += 1
            while index < len(lines) and _continues_item(lines, index):
                body.append(lines[index])
                index += 1
            _push(blocks, "li", _joined(body))
            continue

        if (
            _TABLE_ROW.match(line)
            and index + 1 < len(lines)
            and _TABLE_DELIM.match(lines[index + 1])
        ):
            rows = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                rows.append(lines[index])
                index += 1
            _push(blocks, "table", _table_text(rows))
            continue

        body = []
        while index < len(lines) and lines[index].strip():
            if body and _starts_block(lines, index):
                break
            body.append(lines[index])
            index += 1
        _push(blocks, "p", _joined(body))

    return blocks


def markdown_document(
    text: str,
    *,
    title: str = "",
    source_url: str = "",
) -> str:
    """A markdown original as a reader document whose blocks can be dragged over.

    One element per block, the same marking the other adapters write. What is shown
    is the file's own text: the structure markdown gives it (a heading is a heading,
    a fence is a block of code, a table is a table) rather than a second rendering
    of the prose, so the passage picked here is the passage compiled.
    """
    body = "\n".join(_block_html(block) for block in markdown_blocks(text))
    return wrap_article_document(
        title=title,
        source_url=source_url,
        capture_method="markdown",
        fidelity="ok",
        body_html=body,
        extra_css=_MARKDOWN_CSS,
    )


def _block_html(block: DerivedBlock) -> str:
    bid = html_lib.escape(block.id, quote=True)
    if block.tag == "table":
        rows = []
        for row in block.text.splitlines():
            cells = [cell.strip() for cell in row.split(" | ")]
            if not any(cells):
                continue
            tds = "".join(f"<td>{html_lib.escape(cell)}</td>" for cell in cells)
            rows.append(f"<tr>{tds}</tr>")
        return f'<table id="na-{bid}" data-na-block="{bid}">{"".join(rows)}</table>'
    tag = block.tag if block.tag in _HTML_TAGS else "p"
    return (
        f'<{tag} id="na-{bid}" data-na-block="{bid}">'
        f"{html_lib.escape(block.text)}</{tag}>"
    )


def _body_lines(text: str) -> list[str]:
    """The file's lines, without the frontmatter a source may open with.

    Frontmatter is the project's record about the file rather than the file's own
    text, so it is not shown and cannot be selected.
    """
    folded = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = folded.split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start < len(lines) and lines[start].strip() == "---":
        for end in range(start + 1, len(lines)):
            if lines[end].strip() in {"---", "..."}:
                return lines[end + 1 :]
    return lines


def _closes_fence(close: re.Match[str], marker: str) -> bool:
    found = close.group(1)
    return (
        found[0] == marker[0]
        and len(found) >= len(marker)
        and not close.group(2).strip()
    )


def _starts_block(lines: list[str], index: int) -> bool:
    """Whether this line begins a block of its own rather than continuing prose."""
    line = lines[index]
    if _FENCE.match(line) or _HEADING.match(line) or _RULE.match(line):
        return True
    if _QUOTE.match(line) or _ITEM.match(line):
        return True
    return bool(
        _TABLE_ROW.match(line)
        and index + 1 < len(lines)
        and _TABLE_DELIM.match(lines[index + 1])
    )


def _continues_item(lines: list[str], index: int) -> bool:
    """A list item runs over its own wrapped and nested lines, not the next item.

    A blank line inside an item belongs to it only when what follows is still
    indented: that is a loose item, while a blank line followed by unindented text
    ends the list.
    """
    line = lines[index]
    if not line.strip():
        return index + 1 < len(lines) and _indented(lines[index + 1])
    return _indented(line)


def _indented(line: str) -> bool:
    return line.startswith("  ") or line.startswith("\t")


def _joined(parts: Iterable[str]) -> str:
    """Markdown's soft line breaks are spaces; a hard break stays a line."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            out.append(" ".join(run))
            run.clear()

    for raw in parts:
        text = raw.strip()
        if not text:
            continue
        run.append(text)
        if raw.endswith("  ") or raw.endswith("\\"):
            flush()
            out.append("\n")
    flush()
    return "".join(out).strip()


def _table_text(rows: list[str]) -> str:
    lines: list[str] = []
    for row in rows:
        if _TABLE_DELIM.match(row):
            continue
        text = row.strip()
        if text.startswith("|"):
            text = text[1:]
        if text.endswith("|"):
            text = text[:-1]
        cells = [" ".join(cell.split()) for cell in text.split("|")]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _push(blocks: list[DerivedBlock], tag: str, text: str) -> None:
    body = (text or "").strip()
    if not body:
        return
    blocks.append(DerivedBlock(id=f"m{len(blocks) + 1}", tag=tag, text=body))


__all__ = ["markdown_blocks", "markdown_document"]
