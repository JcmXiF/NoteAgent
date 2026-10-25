"""Looking through a project: its pages, the remarks about them, its sources.

This is the search a reader reaches for when they half-remember a word: not the
web, not a model, but the three things a project is made of. It is deliberately
the same ranking the conversation's ``search`` tool uses — slug and title first,
then the body — so the answer a person gets and the answer the model gets do not
disagree about which page a word belongs to.

Nothing here is an index. A project is small enough to read, and a search that
cannot go stale is worth more than one that answers a millisecond sooner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from noteagent.ingest.frontmatter import parse_page, unquote_scalar
from noteagent.ingest.update_index import iter_wiki_content_pages
from noteagent.notes.store import list_notes
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.links import wiki_slug_for_path

__all__ = [
    "SearchHit",
    "body_excerpt",
    "plain_body",
    "query_needles",
    "rank_text",
    "search_project",
]

#: How many results of one kind are handed back. A palette is read at a glance;
#: the count of what was left out is said instead of the rest of the list.
DEFAULT_PER_KIND = 8

#: How large a source is read for a body match. A stored PDF has no text on disk,
#: and reading a whole book to answer a keystroke is not what search is for.
MAX_SOURCE_BYTES = 512 * 1024

_SOURCE_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".html", ".htm"})

_SNIPPET_RADIUS = 48
_SNIPPET_MAX = 140
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One thing found, and enough about it to be shown without another read."""

    kind: str
    path: str
    title: str
    snippet: str = ""
    slug: str = ""
    note_id: str = ""
    page: str = ""
    score: int = 0

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "path": self.path,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
        }
        if self.slug:
            payload["slug"] = self.slug
        if self.note_id:
            payload["id"] = self.note_id
        if self.page:
            payload["page"] = self.page
        return payload


def query_needles(query: str) -> tuple[str, ...]:
    """The whole query and its words, folded, without repeats."""
    folded = (query or "").strip().casefold()
    if not folded:
        return ()
    out: list[str] = [folded]
    for part in folded.replace("/", " ").split():
        if len(part) >= 2 and part not in out:
            out.append(part)
    return tuple(out)


def plain_body(body: str) -> str:
    """A page body as a person reads it: no comments, one space between words."""
    text = _HTML_COMMENT.sub(" ", body or "")
    return _WS.sub(" ", text).strip()


def body_excerpt(plain: str, needles: tuple[str, ...]) -> str:
    """The first place in *plain* any needle appears, with a little around it."""
    if not plain:
        return ""
    folded = plain.casefold()
    for needle in needles:
        if len(needle) < 2:
            continue
        index = folded.find(needle)
        if index < 0:
            continue
        return _window(plain, folded, index, len(needle))
    return ""


def rank_text(
    needles: tuple[str, ...], slug: str, title: str, body: str
) -> tuple[int, str, str]:
    """How well a text answers *needles*: a score, what matched, and an excerpt.

    The score is the whole ordering. 4 is the slug or the title exactly, 3 either
    of them containing the query, 2 every word of the query in one of them, 1 the
    body alone, 0 no match. Ties are broken by name, by the caller.
    """
    if not needles:
        return 0, "", ""
    slug_cf = (slug or "").casefold()
    bare_cf = slug_cf.rsplit("/", 1)[-1]
    title_cf = (title or "").casefold()
    primary = needles[0]
    haystacks = (slug_cf, bare_cf, title_cf)
    plain = plain_body(body)
    excerpt = body_excerpt(plain, needles)

    if primary in haystacks:
        kind = "title" if primary == title_cf else "slug"
        return 4, kind, excerpt
    if any(primary in item for item in haystacks):
        kind = "title" if primary in title_cf else "slug"
        return 3, kind, excerpt
    extras = needles[1:]
    if extras and all(any(token in item for item in haystacks) for token in extras):
        kind = "title" if any(token in title_cf for token in extras) else "slug"
        return 2, kind, excerpt
    if excerpt:
        return 1, "body", excerpt
    return 0, "", ""


def search_project(
    layout: ProjectLayout,
    query: str,
    *,
    per_kind: int = DEFAULT_PER_KIND,
) -> dict[str, object]:
    """Pages, remarks and sources matching *query*, most useful first."""
    needles = query_needles(query)
    limit = max(0, per_kind)
    if not needles:
        return {
            "query": query,
            "pages": [],
            "notes": [],
            "sources": [],
            "counts": {"pages": 0, "notes": 0, "sources": 0},
        }

    pages, page_total = _search_pages(layout, needles, limit)
    notes, note_total = _search_notes(layout, needles, limit)
    sources, source_total = _search_sources(layout, needles, limit)
    return {
        "query": query,
        "pages": [hit.to_dict() for hit in pages],
        "notes": [hit.to_dict() for hit in notes],
        "sources": [hit.to_dict() for hit in sources],
        "counts": {
            "pages": page_total,
            "notes": note_total,
            "sources": source_total,
        },
    }


def _search_pages(
    layout: ProjectLayout, needles: tuple[str, ...], limit: int
) -> tuple[list[SearchHit], int]:
    hits: list[SearchHit] = []
    for rel, text in iter_wiki_content_pages(layout):
        try:
            slug = wiki_slug_for_path(rel)
        except ValueError:
            continue
        page = parse_page(text)
        title = slug.rsplit("/", 1)[-1]
        if "title" in page.fields:
            got = unquote_scalar(page.fields["title"]).strip()
            if got:
                title = got
        score, _match, excerpt = rank_text(needles, slug, title, page.body)
        if score <= 0:
            continue
        hits.append(
            SearchHit(
                kind="page",
                path=rel,
                title=title,
                snippet=excerpt,
                slug=slug,
                score=score,
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.slug.casefold()))
    return hits[:limit], len(hits)


def _search_notes(
    layout: ProjectLayout, needles: tuple[str, ...], limit: int
) -> tuple[list[SearchHit], int]:
    hits: list[SearchHit] = []
    for note in list_notes(layout):
        slug = _page_slug(note.page)
        # What a note says is both its body and the passage it was written about;
        # quoting the reader's own words back to them is half of its use.
        score, _match, excerpt = rank_text(
            needles, slug, slug.rsplit("/", 1)[-1], f"{note.quote}\n{note.body}"
        )
        if score <= 0:
            continue
        if not excerpt:
            excerpt = _first_line(note.body)
        hits.append(
            SearchHit(
                kind="note",
                path=note.page,
                title=slug.rsplit("/", 1)[-1],
                snippet=excerpt,
                slug=slug,
                note_id=note.id,
                page=note.page,
                score=score,
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.note_id))
    return hits[:limit], len(hits)


def _search_sources(
    layout: ProjectLayout, needles: tuple[str, ...], limit: int
) -> tuple[list[SearchHit], int]:
    hits: list[SearchHit] = []
    raw = layout.raw
    if not raw.is_dir():
        return hits, 0
    root = layout.root.resolve()
    for path in raw.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        title = path.stem
        stemmed = rel[: -len(path.suffix)] if path.suffix else rel
        score, _match, excerpt = rank_text(needles, stemmed, title, "")
        if score <= 0 and path.suffix.lower() in _SOURCE_TEXT_SUFFIXES:
            text = _read_small(path)
            if text:
                score, _match, excerpt = rank_text(needles, stemmed, title, text)
        if score <= 0:
            continue
        hits.append(
            SearchHit(
                kind="source",
                path=rel,
                title=title,
                snippet=excerpt,
                score=score,
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.path.casefold()))
    return hits[:limit], len(hits)


def _page_slug(page: str) -> str:
    """A note's page as a slug, for ranking and for saying where it belongs."""
    text = (page or "").replace("\\", "/").strip()
    if text.startswith("wiki/"):
        text = text[5:]
    if text.lower().endswith(".md"):
        text = text[:-3]
    return text.strip("/")


def _read_small(path: Path) -> str:
    """A source's text, when it is small enough to be worth reading."""
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_SNIPPET_MAX]
    return ""


def _window(plain: str, folded: str, index: int, needle_len: int) -> str:
    source = plain if len(folded) == len(plain) else folded
    start = max(0, index - _SNIPPET_RADIUS)
    end = min(len(source), index + needle_len + _SNIPPET_RADIUS)
    chunk = source[start:end].strip()
    if len(chunk) > _SNIPPET_MAX:
        chunk = chunk[:_SNIPPET_MAX].rstrip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(source) else ""
    return f"{prefix}{chunk}{suffix}"
