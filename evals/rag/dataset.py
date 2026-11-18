"""Load the Stage 0 retrieval eval set and check it against its corpus.

An eval set is a measurement input, not a gate: a query naming a page that no
longer exists would score zero and read like a retrieval regression. So the set
is validated twice: each line against its own contract, then every expected
page and note against the corpus it will run on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noteagent.ingest.update_index import iter_wiki_content_pages
from noteagent.notes.store import list_notes
from noteagent.project.layout import ProjectLayout
from noteagent.wiki.links import wiki_slug_for_path

__all__ = [
    "Dataset",
    "DatasetError",
    "MIN_QUERIES",
    "QUERY_TYPES",
    "RagQuery",
    "check_corpus",
    "corpus_sha256",
    "load_dataset",
    "note_ref",
    "page_ref",
]

#: The five strata the set must cover. ``exact`` also covers slug lookups;
#: ``gap`` is a query the corpus cannot answer.
QUERY_TYPES = ("exact", "paraphrase", "cross_page", "gap", "notes")

#: A set smaller than this cannot give a stable per-type picture.
MIN_QUERIES = 20

_REQUIRED = ("query", "type", "expected_pages", "expected_notes")
_ALLOWED = frozenset((*_REQUIRED, "id", "rationale"))


class DatasetError(ValueError):
    """The eval set or its corpus does not match the contract."""


def page_ref(slug: str) -> str:
    """A page as the ranked id a report and an expectation share."""
    return f"page:{slug}"


def note_ref(note_id: str) -> str:
    """A note as the ranked id a report and an expectation share."""
    return f"note:{note_id}"


@dataclass(frozen=True, slots=True)
class RagQuery:
    """One annotated query: what is asked, and what should come back."""

    id: str
    query: str
    type: str
    expected_pages: tuple[str, ...]
    expected_notes: tuple[str, ...]
    rationale: str = ""

    @property
    def expected(self) -> tuple[str, ...]:
        """The expected results as namespaced ids, the way a rank is reported."""
        return tuple(
            page_ref(slug) for slug in self.expected_pages
        ) + tuple(note_ref(note_id) for note_id in self.expected_notes)

    @property
    def is_gap(self) -> bool:
        return self.type == "gap"


@dataclass(frozen=True, slots=True)
class Dataset:
    """A loaded eval set: its queries, where they came from, and its fingerprint."""

    path: Path
    queries: tuple[RagQuery, ...]
    sha256: str

    @property
    def counts(self) -> dict[str, int]:
        counts = {name: 0 for name in QUERY_TYPES}
        for query in self.queries:
            counts[query.type] += 1
        return counts


def load_dataset(
    path: Path | str, *, require_full: bool = True
) -> Dataset:
    """Read a JSONL eval set, refusing an incomplete or contradictory one.

    ``require_full`` enforces the benchmark's shape - at least
    :data:`MIN_QUERIES` queries covering every type. Tests and small probes turn
    it off; the command line leaves it on.
    """
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as err:
        raise DatasetError(f"{source}: cannot read the eval set: {err}") from err
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise DatasetError(f"{source}: the eval set is not UTF-8: {err}") from err

    queries: list[RagQuery] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as err:
            raise DatasetError(
                f"{source}:{number}: not JSON: {err.msg}"
            ) from err
        query = _parse_query(payload, number, source)
        if query.id in seen:
            raise DatasetError(
                f"{source}:{number}: duplicate id {query.id!r}"
            )
        seen.add(query.id)
        queries.append(query)

    if not queries:
        raise DatasetError(f"{source}: the eval set is empty")
    if require_full:
        _require_full(queries, source)

    return Dataset(
        path=source,
        queries=tuple(queries),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def check_corpus(dataset: Dataset, corpus_root: Path | str) -> None:
    """Refuse a set whose expected pages or notes the corpus does not hold."""
    root = Path(corpus_root)
    layout = ProjectLayout(root=root.resolve())
    if not layout.wiki.is_dir():
        raise DatasetError(f"{root}: the corpus has no wiki/ directory")

    slugs: set[str] = set()
    for relative, _text in iter_wiki_content_pages(layout):
        try:
            slugs.add(wiki_slug_for_path(relative))
        except ValueError:
            continue
    note_ids = {note.id for note in list_notes(layout)}

    problems: list[str] = []
    for query in dataset.queries:
        for slug in query.expected_pages:
            if slug not in slugs:
                problems.append(
                    f"{query.id}: the corpus has no wiki page {slug}"
                )
        for note_id in query.expected_notes:
            if note_id not in note_ids:
                problems.append(
                    f"{query.id}: the corpus has no note {note_id}"
                )
    if problems:
        raise DatasetError("\n".join(problems))


def corpus_sha256(corpus_root: Path | str) -> str:
    """A deterministic fingerprint of the corpus's Markdown files."""
    root = Path(corpus_root)
    digest = hashlib.sha256()
    for kind in ("wiki", "notes"):
        base = root / kind
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
            digest.update(b"\x00")
    return digest.hexdigest()


def _parse_query(
    payload: Any, number: int, source: Path
) -> RagQuery:
    where = f"{source}:{number}"
    if not isinstance(payload, dict):
        raise DatasetError(f"{where}: a query must be a JSON object")

    unknown = sorted(set(payload) - _ALLOWED)
    if unknown:
        raise DatasetError(
            f"{where}: unknown field(s): {', '.join(unknown)}"
        )
    missing = [key for key in _REQUIRED if key not in payload]
    if missing:
        raise DatasetError(
            f"{where}: missing field(s): {', '.join(missing)}"
        )

    query_id = (
        _text(payload, "id", where)
        if "id" in payload
        else f"q{number:02d}"
    )
    question = _text(payload, "query", where)
    query_type = _text(payload, "type", where)
    if query_type not in QUERY_TYPES:
        raise DatasetError(
            f"{where}: unknown type {query_type!r}; "
            f"expected one of {', '.join(QUERY_TYPES)}"
        )
    pages = _strings(payload, "expected_pages", where)
    notes = _strings(payload, "expected_notes", where)
    rationale = payload.get("rationale", "")
    if not isinstance(rationale, str):
        raise DatasetError(f"{where}: rationale must be a string")

    if query_type == "gap":
        if pages or notes:
            raise DatasetError(
                f"{where}: a gap query cannot expect results"
            )
    elif query_type == "notes":
        if not notes:
            raise DatasetError(
                f"{where}: a notes query must name expected_notes"
            )
        if pages:
            raise DatasetError(
                f"{where}: a notes query is scored on notes only; "
                "expected_pages must be empty"
            )
    else:
        if notes:
            raise DatasetError(
                f"{where}: only a notes query may name expected_notes"
            )
        if not pages:
            raise DatasetError(
                f"{where}: a {query_type} query must name an expected page"
            )

    return RagQuery(
        id=query_id,
        query=question,
        type=query_type,
        expected_pages=pages,
        expected_notes=notes,
        rationale=rationale,
    )


def _text(payload: dict[str, Any], key: str, where: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise DatasetError(f"{where}: {key} must be a non-empty string")
    return value.strip()


def _strings(
    payload: dict[str, Any], key: str, where: str
) -> tuple[str, ...]:
    value = payload[key]
    if not isinstance(value, list):
        raise DatasetError(f"{where}: {key} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise DatasetError(
                f"{where}: {key} must hold non-empty strings"
            )
        text = item.strip()
        if text in out:
            raise DatasetError(f"{where}: {key} repeats {text}")
        out.append(text)
    return tuple(out)


def _require_full(queries: list[RagQuery], source: Path) -> None:
    if len(queries) < MIN_QUERIES:
        raise DatasetError(
            f"{source}: the eval set has {len(queries)} queries; "
            f"at least {MIN_QUERIES} are required"
        )
    present = {query.type for query in queries}
    missing = [name for name in QUERY_TYPES if name not in present]
    if missing:
        raise DatasetError(
            f"{source}: the eval set does not cover: {', '.join(missing)}"
        )
