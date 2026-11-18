"""Append a fixed number of effects to one project's journal, then exit.

B6 spawns two of these as separate processes; the eval then reads the journal
back. This module is eval plumbing, not production code: it exists so the
concurrency claim is measured with real processes rather than threads.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from noteagent.effects.journal import COMMITTED, Effect, EffectJournal
from noteagent.project.layout import default_layout

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.recovery.journal_child")
    parser.add_argument("--project", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args(argv)

    journal = EffectJournal(default_layout(Path(args.project)))
    for index in range(args.count):
        journal.append(
            Effect(
                kind="wiki_page_write",
                target=f"wiki/eval/{args.actor}-{index:02d}.md",
                run_id=f"eval-{args.actor}",
                actor=args.actor,
                reversible="snapshot",
                undo="page_history",
                provider="page_history",
                status=COMMITTED,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
