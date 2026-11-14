"""The written record of one golden run: what it used and what it scored.

A run leaves a manifest beside its artifacts and one row in the history CSV.
The manifest is self-contained: a reader can tell which golden set, prompts,
model and commit produced the numbers without opening a session. The key is
never part of either file.
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA = 1
HISTORY_NAME = "history.csv"
DIMENSIONS = ("grounding", "coverage", "uncertainty")

HISTORY_FIELDS = (
    "timestamp",
    "run_id",
    "pair_id",
    "variant",
    "agent_model",
    "agent_temperature",
    "corpus_sha256",
    "queries_sha256",
    "prompt_sha256",
    "judge_prompt_sha256",
    "query_count",
    "trace_passed",
    "trace_failed",
    "judge_errors",
    "grounding_mean",
    "coverage_mean",
    "uncertainty_mean",
    "input_tokens",
    "output_tokens",
    "avg_latency_s",
    "duration_s",
)


@dataclass(frozen=True, slots=True)
class ScoreRecord:
    """One dimension's discrete score and its probability-weighted score."""

    dimension: str
    score: int
    soft: float


@dataclass(frozen=True, slots=True)
class QueryRecord:
    """One query's result, in the shape a manifest and the history row need."""

    id: str
    type: str
    difficulty: str
    passed: bool
    trace_passed: bool
    judge_ok: bool
    scores: tuple[ScoreRecord, ...] = ()
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Everything one variant run reports about itself."""

    run_id: str
    started_at: str
    variant: str
    pair_id: str
    agent_model: str
    agent_base_url: str
    agent_temperature: float
    judge_model: str
    judge_temperature: float
    corpus_sha256: str
    queries_sha256: str
    prompt_sha256: str
    judge_prompt_sha256: str
    git_commit: str
    duration_s: float
    queries: tuple[QueryRecord, ...] = ()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(moment: datetime) -> str:
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def run_id_for(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def public_base_url(value: str) -> str:
    """The base URL without userinfo, query or fragment."""
    parts = urlsplit(value.strip())
    if not parts.hostname:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = parts.hostname if port is None else f"{parts.hostname}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def git_commit(root: Path | None = None) -> str:
    """The commit the run started from, or ``""`` outside a checkout."""
    base = root or Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def means_by_dimension(
    queries: Sequence[QueryRecord],
) -> dict[str, float | None]:
    """The mean soft score per dimension, or ``None`` when it was not scored."""
    out: dict[str, float | None] = {}
    for dimension in DIMENSIONS:
        values = [
            score.soft
            for query in queries
            for score in query.scores
            if score.dimension == dimension
        ]
        out[dimension] = (
            round(sum(values) / len(values), 4) if values else None
        )
    return out


def distribution_by_dimension(
    queries: Sequence[QueryRecord],
) -> dict[str, dict[str, int]]:
    """How many 1–5 scores each dimension got."""
    out: dict[str, dict[str, int]] = {}
    for dimension in DIMENSIONS:
        counts = Counter(
            score.score
            for query in queries
            for score in query.scores
            if score.dimension == dimension
        )
        out[dimension] = {
            str(value): counts.get(value, 0) for value in range(1, 6)
        }
    return out


def counts(queries: Sequence[QueryRecord]) -> dict[str, int]:
    """The run-level totals the summary line and the history row show."""
    return {
        "queries": len(queries),
        "passed": sum(1 for query in queries if query.passed),
        "tracePassed": sum(1 for query in queries if query.trace_passed),
        "traceFailed": sum(1 for query in queries if not query.trace_passed),
        "judgeErrors": sum(1 for query in queries if not query.judge_ok),
    }


def manifest_payload(record: RunRecord) -> dict[str, object]:
    """One run as JSON, with no key and no per-query transcript."""
    totals = counts(record.queries)
    return {
        "schema": SCHEMA,
        "runId": record.run_id,
        "startedAt": record.started_at,
        "durationSeconds": round(record.duration_s, 3),
        "variant": record.variant,
        "pairId": record.pair_id,
        "agent": {
            "model": record.agent_model,
            "baseUrl": public_base_url(record.agent_base_url),
            "temperature": record.agent_temperature,
        },
        "judge": {
            "model": record.judge_model,
            "temperature": record.judge_temperature,
            "promptSha256": record.judge_prompt_sha256,
        },
        "golden": {
            "corpusSha256": record.corpus_sha256,
            "queriesSha256": record.queries_sha256,
            "queryCount": len(record.queries),
        },
        "promptSha256": record.prompt_sha256,
        "gitCommit": record.git_commit,
        "counts": totals,
        "means": means_by_dimension(record.queries),
        "distribution": distribution_by_dimension(record.queries),
        "queries": [
            {
                "id": query.id,
                "type": query.type,
                "difficulty": query.difficulty,
                "passed": query.passed,
                "tracePassed": query.trace_passed,
                "judgeOk": query.judge_ok,
                "scores": [
                    {
                        "dimension": score.dimension,
                        "score": score.score,
                        "soft": round(score.soft, 4),
                    }
                    for score in query.scores
                ],
                "latencySeconds": round(query.latency_s, 3),
                "inputTokens": query.input_tokens,
                "outputTokens": query.output_tokens,
                "error": query.error,
            }
            for query in record.queries
        ],
    }


def write_manifest(path: Path, record: RunRecord) -> Path:
    """Write one run's manifest, creating its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            manifest_payload(record), ensure_ascii=False, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def ab_manifest_payload(
    records: Sequence[RunRecord], *, pair_id: str, started_at: str
) -> dict[str, object]:
    """The manifest of a paired run: both variants, one shared identity."""
    if not records:
        raise ValueError("an A/B manifest needs at least one variant")
    first = records[0]
    return {
        "schema": SCHEMA,
        "kind": "ab",
        "pairId": pair_id,
        "startedAt": started_at,
        "agent": {
            "model": first.agent_model,
            "baseUrl": public_base_url(first.agent_base_url),
            "temperature": first.agent_temperature,
        },
        "judge": {
            "model": first.judge_model,
            "temperature": first.judge_temperature,
            "promptSha256": first.judge_prompt_sha256,
        },
        "golden": {
            "corpusSha256": first.corpus_sha256,
            "queriesSha256": first.queries_sha256,
            "queryCount": len(first.queries),
        },
        "variants": [manifest_payload(record) for record in records],
    }


def write_ab_manifest(
    path: Path,
    records: Sequence[RunRecord],
    *,
    pair_id: str,
    started_at: str,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            ab_manifest_payload(
                records, pair_id=pair_id, started_at=started_at
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def history_row(record: RunRecord) -> dict[str, object]:
    """One run's row in ``history.csv``."""
    means = means_by_dimension(record.queries)
    totals = counts(record.queries)
    query_count = len(record.queries)
    latencies = [query.latency_s for query in record.queries]
    return {
        "timestamp": record.started_at,
        "run_id": record.run_id,
        "pair_id": record.pair_id,
        "variant": record.variant,
        "agent_model": record.agent_model,
        "agent_temperature": record.agent_temperature,
        "corpus_sha256": record.corpus_sha256,
        "queries_sha256": record.queries_sha256,
        "prompt_sha256": record.prompt_sha256,
        "judge_prompt_sha256": record.judge_prompt_sha256,
        "query_count": query_count,
        "trace_passed": totals["tracePassed"],
        "trace_failed": totals["traceFailed"],
        "judge_errors": totals["judgeErrors"],
        "grounding_mean": _csv_float(means["grounding"]),
        "coverage_mean": _csv_float(means["coverage"]),
        "uncertainty_mean": _csv_float(means["uncertainty"]),
        "input_tokens": sum(query.input_tokens for query in record.queries),
        "output_tokens": sum(query.output_tokens for query in record.queries),
        "avg_latency_s": (
            round(sum(latencies) / len(latencies), 3) if latencies else ""
        ),
        "duration_s": round(record.duration_s, 3),
    }


def append_history(path: Path, record: RunRecord) -> Path:
    """Append one run to the history CSV, writing its header first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    has_rows = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS)
        if not has_rows:
            writer.writeheader()
        writer.writerow(history_row(record))
    return path


def read_history(path: Path) -> list[dict[str, str]]:
    """Every history row, oldest first; a missing file is an empty history."""
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _csv_float(value: float | None) -> object:
    return "" if value is None else round(value, 4)


__all__ = [
    "DIMENSIONS",
    "HISTORY_FIELDS",
    "HISTORY_NAME",
    "SCHEMA",
    "QueryRecord",
    "RunRecord",
    "ScoreRecord",
    "ab_manifest_payload",
    "append_history",
    "counts",
    "distribution_by_dimension",
    "git_commit",
    "history_row",
    "iso_timestamp",
    "manifest_payload",
    "means_by_dimension",
    "public_base_url",
    "read_history",
    "run_id_for",
    "utc_now",
    "write_ab_manifest",
    "write_manifest",
]
