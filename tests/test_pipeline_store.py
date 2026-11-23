"""Runs on disk: one file each, read back, and closed when a core restarted."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.pipeline.run import PipelineRun
from noteagent.pipeline.run import run_to_dict
from noteagent.pipeline.store import PipelineRunStore
from noteagent.project.init import init_project


def _run(**overrides: object) -> PipelineRun:
    values: dict[str, object] = {
        "id": "pr-1",
        "intent": "写一篇熵的笔记",
        "started_by": "human",
        "started_at": "2026-11-10T09:00:00Z",
        "updated_at": "2026-11-10T09:00:00Z",
    }
    values.update(overrides)
    return PipelineRun(**values)  # type: ignore[arg-type]


def _write_run(layout, run: PipelineRun) -> None:
    """Write an old run file directly: the store itself is read-only now."""
    import json

    path = layout.pipeline_runs / f"{run.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(run_to_dict(run), ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )


class PipelineRunStoreTests(unittest.TestCase):
    def test_a_run_round_trips_through_its_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            run = _run()

            _write_run(layout, run)

            self.assertEqual(store.get("pr-1"), run)
            self.assertEqual([item.id for item in store.list()], ["pr-1"])

    def test_a_second_run_reads_back_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            store = PipelineRunStore(layout)
            _write_run(layout, _run(id="pr-1", started_at="2026-11-10T09:00:00Z"))
            _write_run(layout, _run(id="pr-2", started_at="2026-11-10T09:01:00Z"))

            self.assertEqual([item.id for item in store.list()], ["pr-2", "pr-1"])


if __name__ == "__main__":
    unittest.main()
