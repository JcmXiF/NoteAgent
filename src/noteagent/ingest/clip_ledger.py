"""The ``wiki/sources/*-clips.md`` ledger: one shape, rendered and merged here.

A ledger is a source list, not prose: one row per clip collection and one row per
block locator. Two rounds on the same topic therefore do not need a model to
combine their ledgers — the rows union, the core renders the result, and the
page keeps the same shape however many rounds wrote it.

A page whose whole job is bookkeeping is the last place to ask a model for a
judgement call: a model asked to combine two ledgers tends to rewrite them as a
comparison of "the two versions", which is neither a source list nor a wiki page.

This module also owns the locator/collection row parsing, because the reader and
the renderer have to agree on one shape for the write path to be stable.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date

from noteagent.ingest.clips import RAW_PREFIX, ClipCollection, collection_headline
from noteagent.ingest.frontmatter import (
    format_inline_list,
    format_scalar,
    parse_inline_list,
    parse_page,
    replace_page_body,
    set_frontmatter_scalar,
    unquote_scalar,
)
from noteagent.ingest.page_merge import (
    PageMergeResult,
    default_today,
    merged_frontmatter,
)

#: Ledger file names live in ``wiki/sources`` and end with this suffix.
LEDGER_SUFFIX = "-clips.md"
LEDGER_TITLE_SUFFIX = " clips"
LEDGER_FALLBACK_TITLE = "clip session"
LEDGER_NOTE = "本页是本主题的 clip 清单，不是第二份 wiki 正文。"

_WIKI_SOURCES_PREFIX = "wiki/sources/"

#: Collection ids come from a clip session (a UUID) or a deep-search round
#: (``ds-<hex>-<n>``); a ledger row has to be readable for either.
_COLLECTION_ID = (
    r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|ds-[0-9a-fA-F]{4,}-\d{1,6})"
)
_ID_TICK = re.compile(rf"`({_COLLECTION_ID})`")
_RAW_TICK = re.compile(rf"`({re.escape(RAW_PREFIX)}[^`]+)`")
_BLOCK_RANGE = re.compile(r"\b([bBpP])(\d+)\s*[–\-]\s*[bBpP](\d+)\b")
_BLOCK_TOKEN = re.compile(r"\b([bBpP])(\d+)\b")
#: `- <headline> (`id`): `raw/...` (b1, b2)` — the row this module renders.
_BULLET_COLLECTION = re.compile(
    rf"^- (.+?) \(`({_COLLECTION_ID})`\): `({re.escape(RAW_PREFIX)}[^`]+)` \(([^)]*)\)\s*$"
)
#: ``- `raw/...#bN` `` — a locator row, anywhere in the body.
_BULLET_LOCATOR = re.compile(r"^-\s+`([^`]+)`\s*$")


@dataclass(frozen=True, slots=True)
class ClipLedger:
    """What a ``*-clips.md`` page says: its collections and its locators."""

    collections: tuple[ClipCollection, ...]
    locators: tuple[str, ...]


def is_clip_ledger_path(relative: str) -> bool:
    """True when *relative* is a clip ledger (``wiki/sources/*-clips.md``)."""
    path = relative.replace("\\", "/").lstrip("./")
    return path.startswith(_WIKI_SOURCES_PREFIX) and path.lower().endswith(
        LEDGER_SUFFIX
    )


def split_source_locator(raw: str) -> tuple[str, str | None]:
    """Split ``raw/...#bN`` into ``(path, block_id)``."""
    text = raw.replace("\\", "/").strip()
    if not text:
        return "", None
    hash_at = text.rfind("#")
    if hash_at <= 0:
        return text, None
    return text[:hash_at].strip(), text[hash_at + 1 :].strip() or None


def raw_source_path(path: str) -> str | None:
    """The ``raw/...`` file a locator or path points at, else ``None``."""
    clean = path.replace("\\", "/").lstrip("./")
    hash_at = clean.find("#")
    if hash_at >= 0:
        clean = clean[:hash_at].rstrip()
    if not clean.startswith(RAW_PREFIX):
        return None
    return clean


def unique_ids(ids: Sequence[str]) -> tuple[str, ...]:
    """First-seen order, stripped, empties dropped."""
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def unique_locators(locators: Sequence[str]) -> tuple[str, ...]:
    """Like :func:`unique_ids`, but collapses case variants of one path."""
    seen: set[str] = set()
    out: list[str] = []
    for item in locators:
        text = item.strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def parse_block_ids(text: str) -> tuple[str, ...]:
    """Block ids named in a collection row: ``b4``, ``p1``, ``b35–b65``."""
    ids: list[str] = []
    for letter, start, end in _BLOCK_RANGE.findall(text):
        lo = int(start)
        hi = int(end)
        if lo > hi:
            lo, hi = hi, lo
        if hi - lo > 80:
            continue
        ids.extend(f"{letter.lower()}{n}" for n in range(lo, hi + 1))
    ids.extend(
        f"{letter.lower()}{number}" for letter, number in _BLOCK_TOKEN.findall(text)
    )
    return unique_ids(ids)


def parse_clip_summary_collections(text: str) -> tuple[ClipCollection, ...]:
    """
    Read collection rows from a ``wiki/sources/*-clips.md`` body.

    Two row shapes are accepted: the bullet this module renders, and any line
    that names exactly one collection id and one raw path — a table row or a
    plain bullet. Older ledgers therefore read as the same collections.
    """
    found: dict[str, ClipCollection] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        bullet = _BULLET_COLLECTION.match(stripped)
        if bullet:
            title = (bullet.group(1) or "").strip()
            ident = (bullet.group(2) or "").strip()
            source = raw_source_path(bullet.group(3) or "")
            ids = parse_block_ids(bullet.group(4) or "")
            if ident and source:
                found[ident] = ClipCollection(
                    id=ident,
                    source_key=source,
                    source_label=title or source.rsplit("/", 1)[-1],
                    block_ids=ids,
                    blocks=(),
                )
            continue
        uuids = _ID_TICK.findall(stripped)
        raws = [path for path in _RAW_TICK.findall(stripped) if raw_source_path(path)]
        if len(uuids) != 1 or len(raws) != 1:
            continue
        source = raw_source_path(raws[0])
        if source is None:
            continue
        found[uuids[0]] = ClipCollection(
            id=uuids[0],
            source_key=source,
            source_label=source.rsplit("/", 1)[-1],
            block_ids=parse_block_ids(stripped),
            blocks=(),
        )
    return tuple(found.values())


def _frontmatter_locators(text: str) -> tuple[str, ...]:
    page = parse_page(text)
    if not page.has_frontmatter:
        return ()
    return tuple(
        item
        for item in parse_inline_list(page.fields.get("sources", "[]"))
        if raw_source_path(item)
    )


def _bullet_locators(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        match = _BULLET_LOCATOR.match(line.strip())
        if match is None:
            continue
        locator = match.group(1).strip()
        if raw_source_path(locator):
            out.append(locator)
    return tuple(out)


def read_clip_ledger(text: str) -> ClipLedger:
    """Parse a ledger page into its collections and locators."""
    frontmatter = _frontmatter_locators(text)
    bullets = _bullet_locators(text)
    return ClipLedger(
        collections=parse_clip_summary_collections(text),
        locators=unique_locators((*frontmatter, *bullets)),
    )


def ledger_title(value: str) -> str:
    """The page title without the ``clips`` marker the frontmatter carries."""
    text = unquote_scalar(value).strip()
    if text.casefold().endswith(LEDGER_TITLE_SUFFIX):
        text = text[: -len(LEDGER_TITLE_SUFFIX)].rstrip()
    return text or LEDGER_FALLBACK_TITLE


def _collection_row(collection: ClipCollection) -> str:
    label = collection_headline(collection) or LEDGER_FALLBACK_TITLE
    ids = ", ".join(collection.block_ids)
    return f"- {label} (`{collection.id}`): `{collection.source_key}` ({ids})"


def render_clip_ledger_body(
    *,
    title: str,
    collections: Sequence[ClipCollection],
    locators: Sequence[str],
) -> str:
    """The ledger body: title, the promise it makes, collections, locators."""
    lines = [f"# {title}", "", LEDGER_NOTE, "", "## Collections", ""]
    if collections:
        lines.extend(_collection_row(item) for item in collections)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Block locators", ""])
    if locators:
        lines.extend(f"- `{item}`" for item in locators)
    else:
        lines.append("- (none)")
    return "\n".join(lines) + "\n"


def render_clip_ledger_page(
    *,
    title: str,
    collections: Sequence[ClipCollection],
    locators: Sequence[str],
    today: date | None = None,
) -> str:
    """A whole ledger page (frontmatter + body), ready to write."""
    page_title = ledger_title(title)
    wiki_date = (today or date.today()).isoformat()
    frontmatter = "\n".join(
        [
            "---",
            f"title: {format_scalar(page_title + LEDGER_TITLE_SUFFIX)}",
            f"created: {wiki_date}",
            f"updated: {wiki_date}",
            f"sources: {format_inline_list(list(locators))}",
            "---",
        ]
    )
    body = render_clip_ledger_body(
        title=page_title,
        collections=collections,
        locators=locators,
    )
    return f"{frontmatter}\n\n{body}"


def union_collections(
    existing: Sequence[ClipCollection],
    incoming: Sequence[ClipCollection],
) -> tuple[ClipCollection, ...]:
    """First-seen order; one id keeps one row, with both sides' block ids."""
    by_id: dict[str, ClipCollection] = {}
    order: list[str] = []
    for item in (*existing, *incoming):
        previous = by_id.get(item.id)
        if previous is None:
            by_id[item.id] = item
            order.append(item.id)
            continue
        ids = unique_ids((*previous.block_ids, *item.block_ids))
        if ids != previous.block_ids:
            by_id[item.id] = replace(previous, block_ids=ids)
    return tuple(by_id[ident] for ident in order)


def merge_clip_ledger(
    existing: str,
    incoming: str,
    *,
    today: Callable[[], str] | None = None,
) -> PageMergeResult:
    """
    Union two ledgers deterministically — no model, no prose.

    The frontmatter keeps the values the page on disk owns and the fields that
    accumulate (``merged_frontmatter``); the body is re-rendered from the unioned
    rows, so however the two ledgers were written, the result is the one shape
    this module renders.
    """
    merged = merged_frontmatter(existing, incoming)
    old = read_clip_ledger(existing)
    new = read_clip_ledger(incoming)
    collections = union_collections(old.collections, new.collections)
    locators = unique_locators((*old.locators, *new.locators))
    merged = set_frontmatter_scalar(
        merged, "sources", format_inline_list(list(locators))
    )
    merged = set_frontmatter_scalar(merged, "updated", (today or default_today)())
    title = ledger_title(parse_page(merged).fields.get("title", ""))
    body = render_clip_ledger_body(
        title=title,
        collections=collections,
        locators=locators,
    )
    return PageMergeResult(content=replace_page_body(merged, body), mode="ledger-merge")
