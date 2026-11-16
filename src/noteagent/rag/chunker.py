"""Cut a wiki page or a note into the pieces a retrieval index embeds.

The page is the citation unit; a chunk is only the unit of embedding and
retrieval. A wiki page is cut at its ``##`` / ``###`` / ``####`` headings, so a
chunk's heading path says where in the page it came from. A note is never cut:
it is already short, and its anchor and quote are the context that makes it
worth finding.

Positions are stable, not content-stable: ``wiki:<slug>#<index>`` numbers the
chunks in page order, so changing one character of a page produces the same
kinds of ids and the old rows can be replaced as a set. This is the llm_wiki
rule, chosen because it keeps deletion simple; the cost is that a changed page
is embedded again in full.

Offsets are relative to the body after the frontmatter, with ``\\r\\n`` folded
to ``\\n``, so a chunk's ``body[start:end]`` is its text. A chunk below the
minimum is merged into a neighbour, across headings when that is what it takes;
the merged chunk keeps the heading path of the section it starts in. Overlap is
applied only where a long section is cut, because a heading already marks a
boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.notes.store import Note
from noteagent.wiki.link_syntax import code_spans

__all__ = [
    "Chunk",
    "ChunkLimits",
    "KIND_NOTE",
    "KIND_WIKI",
    "chunk_note",
    "chunk_wiki",
]

#: What a chunk came from. The index stores this as ``kind``.
KIND_WIKI = "wiki"
KIND_NOTE = "note"

#: A fenced block: three or more backticks or tildes, possibly indented.
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
#: The delimiter row that turns the line above it into a GFM table header.
_TABLE_DELIMITER = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_HEADING = re.compile(r"^(#{2,4})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SENTENCE_END = frozenset("。！？!?；;")
_WORD = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class ChunkLimits:
    """How large one embedded piece may be, in characters, not tokens."""

    target: int = 1000
    max_chars: int = 1500
    overlap: int = 200
    min_chars: int = 200


@dataclass(frozen=True, slots=True)
class Chunk:
    """One piece of a page or a note, with what the index needs to store it."""

    chunk_id: str
    kind: str
    page_id: str
    chunk_index: int
    chunk_text: str
    heading_path: str
    embed_text: str
    start: int
    end: int
    anchor: str = ""
    quote: str = ""


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    end: int
    path: str


@dataclass(frozen=True, slots=True)
class _Piece:
    start: int
    end: int
    atomic: bool = False


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A chunk before its id and text are assigned; the path of its first section."""

    start: int
    end: int
    path: str


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    start: int
    end: int


def chunk_wiki(
    text: str,
    *,
    slug: str,
    limits: ChunkLimits | None = None,
) -> tuple[Chunk, ...]:
    """Cut one wiki page, frontmatter included, into stable chunks."""
    bounds = limits or ChunkLimits()
    parsed = parse_page(text)
    body = parsed.body.replace("\r\n", "\n").replace("\r", "\n")
    title = _title(parsed.fields, slug)
    code = code_spans(body)
    fenced = _fenced_spans(body, code)
    candidates: list[_Candidate] = []
    for section in _sections(body, code):
        pieces = _pieces(
            body, section.start, section.end, bounds, fenced
        )
        if not pieces:
            continue
        atomic = [
            (piece.start, piece.end)
            for piece in pieces
            if piece.atomic
        ]
        spans = _apply_overlap(
            body,
            _merge_tail(body, _pack_spans(pieces, bounds), bounds),
            bounds,
            atomic,
        )
        for start, end in spans:
            chunk_text, start, end = _trim(body, start, end)
            if chunk_text:
                candidates.append(_Candidate(start, end, section.path))

    chunks: list[Chunk] = []
    for index, candidate in enumerate(
        _merge_small(body, candidates, bounds)
    ):
        chunk_text, start, end = _trim(
            body, candidate.start, candidate.end
        )
        chunks.append(
            Chunk(
                chunk_id=_wiki_chunk_id(slug, index),
                kind=KIND_WIKI,
                page_id=slug,
                chunk_index=index,
                chunk_text=chunk_text,
                heading_path=candidate.path,
                embed_text=_embed_text(
                    title, candidate.path, chunk_text
                ),
                start=start,
                end=end,
            )
        )
    return tuple(chunks)


def chunk_note(note: Note, *, page_title: str = "") -> Chunk:
    """A note is one chunk; its page, anchor and quote travel with it."""
    page_id = _page_slug(note.page)
    title = (page_title or "").strip() or page_id.rsplit("/", 1)[-1]
    raw_body = note.body or ""
    body, start, end = _trim(raw_body, 0, len(raw_body))
    return Chunk(
        chunk_id=_note_chunk_id(note.id),
        kind=KIND_NOTE,
        page_id=page_id,
        chunk_index=0,
        chunk_text=body,
        heading_path="",
        embed_text=_embed_text(
            title, note.anchor.strip(), note.quote.strip(), body
        ),
        start=start,
        end=end,
        anchor=note.anchor,
        quote=note.quote,
    )


def _title(fields: dict[str, str], slug: str) -> str:
    raw = fields.get("title")
    if isinstance(raw, str):
        got = unquote_scalar(raw).strip()
        if got:
            return got
    return slug.rsplit("/", 1)[-1]


def _wiki_chunk_id(slug: str, index: int) -> str:
    return f"{KIND_WIKI}:{slug}#{index}"


def _note_chunk_id(note_id: str) -> str:
    return f"{KIND_NOTE}:{note_id}#0"


def _embed_text(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


def _fenced_spans(
    body: str, code: tuple[tuple[int, int], ...]
) -> dict[int, int]:
    """The code spans that open with a fence, keyed by their first offset."""
    fenced: dict[int, int] = {}
    for start, end in code:
        line_end = body.find("\n", start)
        first = body[start : line_end if line_end >= 0 else len(body)]
        if _FENCE_OPEN.match(first):
            fenced[start] = end
    return fenced


def _inside(offset: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= offset < end for start, end in spans)


def _page_slug(page: str) -> str:
    text = (page or "").replace("\\", "/").strip()
    if text.startswith("wiki/"):
        text = text[5:]
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text.strip("/")


def _sections(
    body: str, code: tuple[tuple[int, int], ...]
) -> tuple[_Section, ...]:
    """The intro and every ``##`` / ``###`` / ``####`` section, in order."""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    current_start = 0
    current_path = ""
    for line in _lines(body):
        if _inside(line.start, code):
            continue
        heading = _HEADING.match(line.text)
        if heading is None:
            continue
        title = heading.group(2).strip()
        if not title:
            continue
        sections.append(_Section(current_start, line.start, current_path))
        level = len(heading.group(1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        current_path = " > ".join(item[1] for item in stack)
        current_start = line.end
    sections.append(_Section(current_start, len(body), current_path))
    return tuple(sections)


def _lines(body: str, start: int = 0, end: int | None = None) -> list[_Line]:
    limit = len(body) if end is None else end
    lines: list[_Line] = []
    index = start
    while index < limit:
        newline = body.find("\n", index, limit)
        if newline < 0:
            lines.append(_Line(body[index:limit], index, limit))
            break
        lines.append(_Line(body[index:newline], index, newline + 1))
        index = newline + 1
    return lines


def _is_table_start(lines: list[_Line], index: int) -> bool:
    """A header line followed by a GFM delimiter row makes a table.

    The header must hold a ``|``; without one, ``---`` is a setext heading or a
    thematic break, not a table. Both pipe-started and pipe-separated tables
    are recognised.
    """
    if index + 1 >= len(lines):
        return False
    header = lines[index].text
    delimiter = lines[index + 1].text
    return "|" in header and _TABLE_DELIMITER.match(delimiter) is not None


def _pieces(
    body: str,
    start: int,
    end: int,
    limits: ChunkLimits,
    fenced: dict[int, int],
) -> list[_Piece]:
    """Paragraph, code and table blocks, with long prose split further."""
    pieces: list[_Piece] = []
    for block in _blocks(body, start, end, fenced):
        text, block_start, block_end = _trim(body, block.start, block.end)
        if not text:
            continue
        if block.atomic or block_end - block_start <= limits.max_chars:
            pieces.append(_Piece(block_start, block_end, block.atomic))
        else:
            pieces.extend(
                _split_oversize(
                    body, _Piece(block_start, block_end), limits.max_chars
                )
            )
    return pieces


def _blocks(
    body: str, start: int, end: int, fenced: dict[int, int]
) -> list[_Piece]:
    blocks: list[_Piece] = []
    pending_start: int | None = None
    pending_end = 0
    lines = _lines(body, start, end)
    index = 0

    def flush() -> None:
        nonlocal pending_start
        if pending_start is not None:
            blocks.append(_Piece(pending_start, pending_end))
            pending_start = None

    while index < len(lines):
        line = lines[index]
        stripped = line.text.strip()
        fenced_end = fenced.get(line.start)
        if fenced_end is not None:
            flush()
            blocks.append(_Piece(line.start, fenced_end, atomic=True))
            while index < len(lines) and lines[index].start < fenced_end:
                index += 1
            continue
        if _is_table_start(lines, index):
            flush()
            cursor = index + 2
            while cursor < len(lines) and "|" in lines[cursor].text:
                cursor += 1
            blocks.append(
                _Piece(line.start, lines[cursor - 1].end, atomic=True)
            )
            index = cursor
            continue
        if not stripped:
            flush()
            index += 1
            continue
        if pending_start is None:
            pending_start = line.start
        pending_end = line.end
        index += 1
    flush()
    return blocks


def _split_oversize(
    body: str, piece: _Piece, max_chars: int
) -> list[_Piece]:
    if piece.end - piece.start <= max_chars:
        return [piece]
    for splitter in (_split_lines, _split_sentences, _split_words):
        parts = splitter(body, piece)
        if len(parts) <= 1:
            continue
        out: list[_Piece] = []
        for part in parts:
            text, start, end = _trim(body, part.start, part.end)
            if not text:
                continue
            out.extend(
                _split_oversize(body, _Piece(start, end), max_chars)
            )
        if out:
            return out
    return _hard_split(body, piece, max_chars)


def _split_lines(body: str, piece: _Piece) -> list[_Piece]:
    lines = _lines(body, piece.start, piece.end)
    if len(lines) <= 1:
        return [piece]
    return [_Piece(line.start, line.end) for line in lines]


def _split_sentences(body: str, piece: _Piece) -> list[_Piece]:
    text = body[piece.start : piece.end]
    spans: list[_Piece] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char not in _SENTENCE_END and not (
            char == "." and _ends_period(text, index)
        ):
            index += 1
            continue
        end = index + 1
        while end < len(text) and text[end] in " \t":
            end += 1
        spans.append(_Piece(piece.start + start, piece.start + end))
        start = end
        index = end
    if start < len(text):
        spans.append(_Piece(piece.start + start, piece.end))
    return [span for span in spans if span.end > span.start]


def _ends_period(text: str, index: int) -> bool:
    """Whether a ``.`` ends an English sentence rather than an abbreviation.

    A period inside a word is left alone (``3.14``, ``e.g.``); so is one whose
    next non-space character is not a capital, which keeps most abbreviations
    intact. The cost is that a sentence starting in lowercase is not split at
    its period, but the whitespace step still bounds it.
    """
    after = text[index + 1 :]
    if not after:
        return True
    if after[0] not in " \t":
        return False
    rest = after.lstrip(" \t")
    if not rest:
        return True
    if rest[0] in "\"'“‘(":
        rest = rest[1:].lstrip()
    return bool(rest) and rest[0].isupper()


def _split_words(body: str, piece: _Piece) -> list[_Piece]:
    text = body[piece.start : piece.end]
    matches = list(_WORD.finditer(text))
    if len(matches) <= 1:
        return [piece]
    return [
        _Piece(piece.start + match.start(), piece.start + match.end())
        for match in matches
    ]


def _hard_split(
    body: str, piece: _Piece, max_chars: int
) -> list[_Piece]:
    pieces: list[_Piece] = []
    start = piece.start
    while start < piece.end:
        end = min(start + max_chars, piece.end)
        text, trimmed_start, trimmed_end = _trim(body, start, end)
        if text:
            pieces.append(_Piece(trimmed_start, trimmed_end))
        start = end
    return pieces or [piece]


def _pack_spans(pieces: list[_Piece], limits: ChunkLimits) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    for piece in pieces:
        length = piece.end - piece.start
        if start is None:
            start, end = piece.start, piece.end
            continue
        current = end - start
        if piece.atomic and length > limits.max_chars:
            spans.append((start, end))
            start, end = piece.start, piece.end
            continue
        if current < limits.target and piece.end - start <= limits.max_chars:
            end = piece.end
            continue
        spans.append((start, end))
        start, end = piece.start, piece.end
    if start is not None:
        spans.append((start, end))
    return spans


def _merge_tail(
    body: str, spans: list[tuple[int, int]], limits: ChunkLimits
) -> list[tuple[int, int]]:
    """Merge a tiny last piece into its section neighbour before overlap."""
    if len(spans) < 2:
        return spans
    text, _start, _end = _trim(body, *spans[-1])
    if len(text) >= limits.min_chars:
        return spans
    merged_text, merged_start, merged_end = _trim(
        body, spans[-2][0], spans[-1][1]
    )
    if len(merged_text) <= limits.max_chars:
        return [*spans[:-2], (merged_start, merged_end)]
    return spans


def _merge_small(
    body: str,
    candidates: list[_Candidate],
    limits: ChunkLimits,
) -> list[_Candidate]:
    """Merge a chunk below the minimum into a neighbour, across headings too.

    A page of many short sections would otherwise become a crowd of tiny
    chunks; the merged chunk keeps the heading path of the section it starts
    in, which is the section its first words came from.
    """
    if limits.min_chars <= 0 or len(candidates) < 2:
        return candidates
    out: list[_Candidate] = []
    index = 0
    while index < len(candidates):
        current = candidates[index]
        text, _start, _end = _trim(body, current.start, current.end)
        if len(text) >= limits.min_chars:
            out.append(current)
            index += 1
            continue
        if out and _merge_fits(body, out[-1], current, limits):
            out[-1] = _combine(out[-1], current)
            index += 1
            continue
        merged = current
        cursor = index + 1
        while cursor < len(candidates) and _merge_fits(
            body, merged, candidates[cursor], limits
        ):
            merged = _combine(merged, candidates[cursor])
            cursor += 1
            merged_text, _start, _end = _trim(
                body, merged.start, merged.end
            )
            if len(merged_text) >= limits.min_chars:
                break
        if cursor > index + 1:
            out.append(merged)
            index = cursor
            continue
        out.append(current)
        index += 1
    return out


def _merge_fits(
    body: str, first: _Candidate, second: _Candidate, limits: ChunkLimits
) -> bool:
    text, _start, _end = _trim(body, first.start, second.end)
    return len(text) <= limits.max_chars


def _combine(first: _Candidate, second: _Candidate) -> _Candidate:
    return _Candidate(first.start, second.end, first.path)


def _apply_overlap(
    body: str,
    spans: list[tuple[int, int]],
    limits: ChunkLimits,
    atomic: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Add context from the previous chunk, shortened to respect the bounds.

    The overlap yields to the hard maximum and never starts inside a code block
    or table: a chunk that began with half a fence would be worse than one with
    less shared context. Its size is therefore a ceiling, not a guarantee.
    """
    if limits.overlap <= 0 or len(spans) < 2:
        return spans
    out = [spans[0]]
    for start, end in spans[1:]:
        previous_start, previous_end = out[-1]
        if end - start >= limits.max_chars:
            out.append((start, end))
            continue
        candidate = max(
            previous_start, previous_end - limits.overlap
        )
        candidate = _next_boundary(body, candidate, previous_end)
        if end - candidate > limits.max_chars:
            candidate = _next_boundary(
                body, end - limits.max_chars, end
            )
        if _overlaps_atomic(candidate, previous_end, atomic):
            out.append((start, end))
            continue
        out.append(
            (candidate, end) if candidate < start else (start, end)
        )
    return out


def _overlaps_atomic(
    start: int, end: int, atomic: list[tuple[int, int]]
) -> bool:
    return any(
        block_start < end and block_end > start
        for block_start, block_end in atomic
    )


def _next_boundary(body: str, start: int, limit: int) -> int:
    for index in range(start, limit):
        if body[index] in " \t\n":
            return index + 1
    return start


def _trim(body: str, start: int, end: int) -> tuple[str, int, int]:
    raw = body[start:end]
    text = raw.strip()
    if not text:
        return "", start, start
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    return text, start + leading, end - trailing
