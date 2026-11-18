"""Shared fixtures for the retrieval eval tests."""

from __future__ import annotations

import json
from pathlib import Path

NOTE_ID = "20261101-090000-abcd"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_project(root: Path) -> Path:
    write(
        root / "wiki" / "concepts" / "entropy.md",
        '---\ntitle: "Entropy"\n---\n\n熵衡量不确定性。\n',
    )
    write(
        root / "wiki" / "concepts" / "kl-divergence.md",
        '---\ntitle: "KL 散度"\n---\n\nKL 散度衡量两个概率分布的差异。\n',
    )
    write(
        root / "wiki" / "agent" / "compaction.md",
        '---\ntitle: "Compaction"\n---\n\n对话太长时，较早的消息会被压缩。\n',
    )
    write(
        root / "notes" / "concepts" / "entropy" / f"{NOTE_ID}.md",
        "---\n"
        "page: wiki/concepts/entropy.md\n"
        'created: "2026-11-01T09:00:00.000000"\n'
        'updated: "2026-11-01T09:00:00.000000"\n'
        "---\n"
        "\n复习计划：先读熵，再读交叉熵。\n",
    )
    return root


ROWS = [
    {
        "id": "exact-01",
        "query": "entropy",
        "type": "exact",
        "expected_pages": ["concepts/entropy"],
        "expected_notes": [],
    },
    {
        "id": "para-01",
        "query": "两个概率分布差多少",
        "type": "paraphrase",
        "expected_pages": ["concepts/kl-divergence"],
        "expected_notes": [],
    },
    {
        "id": "cross-01",
        "query": "熵 压缩 对话",
        "type": "cross_page",
        "expected_pages": ["concepts/entropy", "agent/compaction"],
        "expected_notes": [],
    },
    {
        "id": "gap-01",
        "query": "如何烤酸面包",
        "type": "gap",
        "expected_pages": [],
        "expected_notes": [],
    },
    {
        "id": "notes-01",
        "query": "复习计划",
        "type": "notes",
        "expected_pages": [],
        "expected_notes": [NOTE_ID],
    },
]


def full_rows() -> list[dict]:
    """The five rows plus enough gap queries to satisfy the benchmark shape."""
    rows = list(ROWS)
    for index in range(15):
        rows.append(
            {
                "id": f"gap-{index + 2:02d}",
                "query": f"zzzz absent {index}",
                "type": "gap",
                "expected_pages": [],
                "expected_notes": [],
            }
        )
    return rows


def write_dataset(root: Path, rows: list[dict] | None = None) -> Path:
    path = root / "queries.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (ROWS if rows is None else rows)
        )
        + "\n",
        encoding="utf-8",
    )
    return path
