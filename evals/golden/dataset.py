"""Load, validate and refresh the v2 golden set.

The golden data is local: a corpus snapshot, twenty single-turn queries with
ground truth, and a manifest carrying the hashes and counts. This module is the
only reader; the runner and the judge depend on the dataclasses here so a
malformed case fails before a provider is called.
"""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from noteagent.ingest.frontmatter import parse_page, unquote_scalar

TYPES = ("fact", "synthesis", "gap", "task")
DIFFICULTIES = ("easy", "medium", "hard")
WAIT_STATES = ("absent", "proposed")

#: The job/interface pairs the old proposals used. Kept so the recorded dataset
#: still parses; new runs write no waits.
JOB_SOURCES = {
    "plan": ("source", "plan_board"),
    "compile": ("plan_board", "compile_board"),
}
DEFAULT_GOLDEN = Path(".scratch/agent-eval/golden")

_QUERY_KEYS = (
    "id",
    "type",
    "difficulty",
    "question",
    "corpus_pages",
    "reference_answer",
    "key_points",
    "expected_trace",
    "forbidden",
    "raw_sources",
    "notes",
)
_TRACE_KEYS = (
    "tools_any",
    "wait",
    "propose_valid",
    "propose_job",
    "propose_interface",
    "max_tool_rounds",
    "no_error",
)
_TOTAL = 24
_TYPE_COUNTS = {
    "fact": 5,
    "synthesis": 5,
    "gap": 5,
    "task": 9,
}
_DIFFICULTY_COUNTS = {"easy": 5, "medium": 10, "hard": 9}
_TYPE_MATRIX = {
    "fact": {"easy": 1, "medium": 2, "hard": 2},
    "synthesis": {"easy": 1, "medium": 2, "hard": 2},
    "gap": {"easy": 1, "medium": 2, "hard": 2},
    "task": {"easy": 2, "medium": 4, "hard": 3},
}


class GoldenError(ValueError):
    """The local golden set does not match its contract."""


@dataclass(frozen=True, slots=True)
class ExpectedTrace:
    """The trace properties a case expects, beside the judge's text score."""

    tools_any: tuple[str, ...]
    wait: str
    max_tool_rounds: int
    no_error: bool
    propose_valid: bool | None = None
    propose_job: str | None = None
    propose_interface: str | None = None


@dataclass(frozen=True, slots=True)
class GoldenQuery:
    """One single-turn evaluation question with its ground truth."""

    id: str
    type: str
    difficulty: str
    question: str
    corpus_pages: tuple[str, ...]
    reference_answer: str
    key_points: tuple[str, ...]
    expected_trace: ExpectedTrace
    forbidden: tuple[str, ...]
    raw_sources: tuple[str, ...]
    notes: str


@dataclass(frozen=True, slots=True)
class CorpusPage:
    """One page of the local corpus snapshot."""

    slug: str
    title: str
    body: str
    path: Path


@dataclass(frozen=True, slots=True)
class GoldenCorpus:
    """The snapshot a golden set runs against."""

    root: Path
    pages: tuple[CorpusPage, ...]
    purpose: str
    schema: str

    def page(self, slug: str) -> CorpusPage:
        for page in self.pages:
            if page.slug == slug:
                return page
        raise GoldenError(f"corpus page {slug!r} is missing")


@dataclass(frozen=True, slots=True)
class GoldenSet:
    """One validated golden set, ready for the runner and the judge."""

    root: Path
    corpus: GoldenCorpus
    queries: tuple[GoldenQuery, ...]
    manifest: Mapping[str, Any]
    counts: Mapping[str, Any]
    corpus_sha256: str
    queries_sha256: str


def load_golden(
    root: Path,
    *,
    check_hashes: bool = True,
    require_counts: bool = True,
) -> GoldenSet:
    """Load and validate a golden set; the flags are for small test fixtures."""
    root = Path(root)
    queries_path = root / "queries.yaml"
    manifest_path = root / "manifest.yaml"
    corpus_root = root / "corpus"
    corpus_manifest_path = corpus_root / "manifest.yaml"
    for path, what in (
        (queries_path, "queries.yaml"),
        (manifest_path, "manifest.yaml"),
        (corpus_manifest_path, "corpus/manifest.yaml"),
    ):
        if not path.is_file():
            raise GoldenError(f"{path}: {what} is missing")

    queries = _load_queries(queries_path)
    corpus = _load_corpus(corpus_root)
    for query in queries:
        for slug in query.corpus_pages:
            corpus.page(slug)

    counts = _counts(queries)
    manifest = _mapping(_load_yaml(manifest_path), manifest_path)
    corpus_manifest = _mapping(
        _load_yaml(corpus_manifest_path), corpus_manifest_path
    )
    corpus_sha256 = _corpus_sha(corpus_root)
    queries_sha256 = _sha(queries_path.read_bytes())

    if check_hashes:
        _hash_matches(
            manifest, "queries_sha256", queries_sha256, manifest_path
        )
        _hash_matches(
            manifest, "corpus_sha256", corpus_sha256, manifest_path
        )
        _hash_matches(
            corpus_manifest,
            "corpus_sha256",
            corpus_sha256,
            corpus_manifest_path,
        )
    if require_counts:
        _validate_counts(counts)
        if manifest.get("counts") != counts:
            raise GoldenError(
                f"{manifest_path}: counts are stale; run --write-manifest"
            )

    return GoldenSet(
        root=root,
        corpus=corpus,
        queries=queries,
        manifest=manifest,
        counts=counts,
        corpus_sha256=corpus_sha256,
        queries_sha256=queries_sha256,
    )


def write_manifest(root: Path) -> tuple[Path, Path]:
    """Recompute the counts and hashes after the golden data was edited."""
    root = Path(root)
    corpus_root = root / "corpus"
    queries_path = root / "queries.yaml"
    queries = _load_queries(queries_path)
    _load_corpus(corpus_root)
    counts = _counts(queries)
    corpus_sha256 = _corpus_sha(corpus_root)
    queries_sha256 = _sha(queries_path.read_bytes())

    golden_path = root / "manifest.yaml"
    existing = _existing_mapping(golden_path)
    created = str(
        existing.get("created")
        or datetime.now(timezone.utc).date().isoformat()
    )
    manifest = {
        "version": 1,
        "created": created,
        "corpus_sha256": corpus_sha256,
        "queries_sha256": queries_sha256,
        "counts": counts,
    }
    golden_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    corpus_path = corpus_root / "manifest.yaml"
    existing_corpus = _existing_mapping(corpus_path)
    corpus_manifest = {
        "version": 1,
        "snapshot": str(existing_corpus.get("snapshot") or created),
        "source": str(existing_corpus.get("source") or ""),
        "files": _corpus_files(corpus_root),
        "excluded": list(existing_corpus.get("excluded") or []),
        "raw": existing_corpus.get(
            "raw", {"copied": False, "note": ""}
        ),
        "corpus_sha256": corpus_sha256,
    }
    corpus_path.write_text(
        yaml.safe_dump(corpus_manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    return golden_path, corpus_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.golden.dataset",
        description="Validate the local v2 golden set.",
    )
    parser.add_argument("command", nargs="?", default="check")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="recompute the counts and hashes before checking",
    )
    args = parser.parse_args(argv)

    root = Path(args.golden)
    try:
        if args.write_manifest:
            write_manifest(root)
        golden = load_golden(root)
    except GoldenError as err:
        print(f"golden: {err}")
        return 2

    print(f"golden: {len(golden.queries)} queries, OK")
    print(f"  by type: {dict(golden.counts['by_type'])}")
    print(f"  by difficulty: {dict(golden.counts['by_difficulty'])}")
    print(f"  corpus sha256: {golden.corpus_sha256}")
    print(f"  queries sha256: {golden.queries_sha256}")
    return 0


def _load_queries(path: Path) -> tuple[GoldenQuery, ...]:
    raw = _load_yaml(path)
    if not isinstance(raw, list) or not raw:
        raise GoldenError(f"{path}: queries must be a non-empty list")
    queries: list[GoldenQuery] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        query = _query(item, index, path)
        if query.id in seen:
            raise GoldenError(f"{path}: duplicate id {query.id!r}")
        seen.add(query.id)
        queries.append(query)
    return tuple(queries)


def _query(item: Any, index: int, path: Path) -> GoldenQuery:
    where = f"{path}: query {index}"
    if not isinstance(item, Mapping):
        raise GoldenError(f"{where} must be a mapping")
    _reject_unknown(item, _QUERY_KEYS, where)

    qid = _text(item.get("id"), f"{where} id")
    qtype = _choice(item.get("type"), TYPES, f"{where} type")
    difficulty = _choice(
        item.get("difficulty"), DIFFICULTIES, f"{where} difficulty"
    )
    question = _text(item.get("question"), f"{where} question")
    pages = _strings(item.get("corpus_pages"), f"{where} corpus_pages")
    reference = _text(
        item.get("reference_answer"), f"{where} reference_answer"
    )
    key_points = _strings(item.get("key_points"), f"{where} key_points")
    if not 2 <= len(key_points) <= 5:
        raise GoldenError(f"{where} key_points must be 2-5")
    trace = _trace(
        item.get("expected_trace"), qtype, qid, f"{where} expected_trace"
    )
    forbidden = _strings(item.get("forbidden"), f"{where} forbidden")
    raw_sources = _strings(
        item.get("raw_sources", []),
        f"{where} raw_sources",
        allow_empty=True,
    )
    notes = _text(item.get("notes"), f"{where} notes")
    return GoldenQuery(
        id=qid,
        type=qtype,
        difficulty=difficulty,
        question=question,
        corpus_pages=pages,
        reference_answer=reference,
        key_points=key_points,
        expected_trace=trace,
        forbidden=forbidden,
        raw_sources=raw_sources,
        notes=notes,
    )


def _trace(
    value: Any, qtype: str, qid: str, where: str
) -> ExpectedTrace:
    if not isinstance(value, Mapping):
        raise GoldenError(f"{where} must be a mapping")
    _reject_unknown(value, _TRACE_KEYS, where)
    tools = _strings(value.get("tools_any"), f"{where} tools_any")
    wait = value.get("wait")
    if not isinstance(wait, str) or wait not in WAIT_STATES:
        raise GoldenError(
            f"{where} wait must be one of {', '.join(WAIT_STATES)}"
        )
    rounds = value.get("max_tool_rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        raise GoldenError(
            f"{where} max_tool_rounds must be a non-negative integer"
        )
    no_error = value.get("no_error")
    if not isinstance(no_error, bool):
        raise GoldenError(f"{where} no_error must be true or false")
    propose = value.get("propose_valid")
    if propose is not None and not isinstance(propose, bool):
        raise GoldenError(
            f"{where} propose_valid must be true or false"
        )
    propose_job = value.get("propose_job")
    propose_interface = value.get("propose_interface")
    if (propose_job is None) != (propose_interface is None):
        raise GoldenError(
            f"{where}: propose_job and propose_interface go together"
        )
    if propose_job is not None:
        if not isinstance(propose_job, str) or propose_job not in JOB_SOURCES:
            raise GoldenError(
                f"{where} propose_job must be one of "
                f"{', '.join(JOB_SOURCES)}"
            )
        allowed = JOB_SOURCES[propose_job]
        if (
            not isinstance(propose_interface, str)
            or propose_interface not in allowed
        ):
            raise GoldenError(
                f"{where} propose_interface must be one of "
                f"{', '.join(allowed)} for {propose_job}"
            )
    if qtype == "task":
        if wait != "proposed":
            raise GoldenError(f"{where}: a task must expect wait proposed")
        if propose is not True:
            raise GoldenError(
                f"{where}: a task must expect propose_valid true"
            )
    else:
        if wait != "absent":
            raise GoldenError(
                f"{where}: a non-task must expect wait absent"
            )
        if propose is not None:
            raise GoldenError(
                f"{where}: propose_valid is only for tasks"
            )
        if propose_job is not None:
            raise GoldenError(
                f"{where}: propose_job/interface are only for tasks"
            )
    return ExpectedTrace(
        tools_any=tools,
        wait=wait,
        max_tool_rounds=rounds,
        no_error=no_error,
        propose_valid=propose,
        propose_job=propose_job,
        propose_interface=propose_interface,
    )


def _load_corpus(root: Path) -> GoldenCorpus:
    wiki = root / "wiki"
    if not wiki.is_dir():
        raise GoldenError(f"{wiki}: the corpus wiki directory is missing")
    pages: list[CorpusPage] = []
    for path in sorted(wiki.rglob("*.md")):
        parsed = parse_page(_read(path))
        slug = path.relative_to(wiki).with_suffix("").as_posix()
        title = (
            unquote_scalar(parsed.fields.get("title", "")).strip()
            or slug.rsplit("/", 1)[-1]
        )
        body = parsed.body.strip()
        if not body:
            raise GoldenError(f"{path}: the page body is empty")
        pages.append(CorpusPage(slug=slug, title=title, body=body, path=path))
    if not pages:
        raise GoldenError(f"{wiki}: at least one page is required")
    purpose = _read(root / "purpose.md").strip()
    schema = _read(root / "schema.md").strip()
    if not purpose:
        raise GoldenError(f"{root / 'purpose.md'}: the purpose is empty")
    if not schema:
        raise GoldenError(f"{root / 'schema.md'}: the schema is empty")
    return GoldenCorpus(
        root=root, pages=tuple(pages), purpose=purpose, schema=schema
    )


def _counts(queries: tuple[GoldenQuery, ...]) -> dict[str, Any]:
    by_type = Counter(query.type for query in queries)
    by_difficulty = Counter(query.difficulty for query in queries)
    matrix = {
        name: {difficulty: 0 for difficulty in DIFFICULTIES}
        for name in TYPES
    }
    for query in queries:
        matrix[query.type][query.difficulty] += 1
    return {
        "total": len(queries),
        "by_type": {name: by_type.get(name, 0) for name in TYPES},
        "by_difficulty": {
            name: by_difficulty.get(name, 0) for name in DIFFICULTIES
        },
        "matrix": matrix,
    }


def _validate_counts(counts: Mapping[str, Any]) -> None:
    if counts["total"] != _TOTAL:
        raise GoldenError(
            f"expected {_TOTAL} golden queries, got {counts['total']}"
        )
    for name in TYPES:
        if counts["by_type"][name] != _TYPE_COUNTS[name]:
            raise GoldenError(
                f"expected {_TYPE_COUNTS[name]} {name} queries, "
                f"got {counts['by_type'][name]}"
            )
        for difficulty in DIFFICULTIES:
            expected = _TYPE_MATRIX[name][difficulty]
            actual = counts["matrix"][name][difficulty]
            if actual != expected:
                raise GoldenError(
                    f"expected {expected} {name}/{difficulty} queries, "
                    f"got {actual}"
                )
    if dict(counts["by_difficulty"]) != _DIFFICULTY_COUNTS:
        raise GoldenError(
            "expected difficulty counts "
            f"{_DIFFICULTY_COUNTS}, got {dict(counts['by_difficulty'])}"
        )


def _corpus_sha(root: Path) -> str:
    files = sorted(
        path
        for path in root.rglob("*.md")
        if path.is_file() and path.name != "manifest.yaml"
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _corpus_files(root: Path) -> dict[str, int]:
    files: dict[str, int] = {}
    for path in sorted((root / "wiki").rglob("*.md")):
        folder = path.relative_to(root).parent.as_posix()
        files[folder] = files.get(folder, 0) + 1
    for name in ("purpose.md", "schema.md"):
        if (root / name).is_file():
            files[name] = 1
    return dict(sorted(files.items()))


def _hash_matches(
    manifest: Mapping[str, Any],
    key: str,
    actual: str,
    path: Path,
) -> None:
    stored = manifest.get(key)
    if not isinstance(stored, str) or stored != actual:
        raise GoldenError(f"{path}: {key} is stale; run --write-manifest")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise GoldenError(f"{path}: invalid YAML: {err}") from err
    except OSError as err:
        raise GoldenError(f"{path}: cannot be read: {err}") from err


def _existing_mapping(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        return {}
    value = _load_yaml(path)
    return value if isinstance(value, Mapping) else {}


def _mapping(value: Any, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GoldenError(f"{path}: must be a mapping")
    return value


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as err:
        raise GoldenError(f"{path}: cannot be read: {err}") from err


def _reject_unknown(
    raw: Mapping[Any, Any], allowed: tuple[str, ...], where: str
) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise GoldenError(
            f"{where}: unknown key(s): {', '.join(unknown)}"
        )


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GoldenError(f"{where} must be a non-empty string")
    return value.strip()


def _choice(value: Any, allowed: tuple[str, ...], where: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise GoldenError(
            f"{where} must be one of {', '.join(allowed)}"
        )
    return value


def _strings(
    value: Any, where: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GoldenError(f"{where} must be a list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GoldenError(
                f"{where} entries must be non-empty strings"
            )
        out.append(item.strip())
    if not out and not allow_empty:
        raise GoldenError(f"{where} must not be empty")
    return tuple(out)


if __name__ == "__main__":
    raise SystemExit(main())
