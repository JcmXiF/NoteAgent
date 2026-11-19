"""Materials: staged from a capture, logged by transition, kept for a while."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.acquire.adapters.pdf_source import load_derived_payload
from noteagent.acquire.types import Capture
from noteagent.pipeline.materials import (
    DEFAULT_KEEP_RUNS,
    MaterialLog,
    MaterialTransition,
    Materials,
    PromotionError,
    preview_text,
)
from noteagent.pipeline.run import PipelineRun
from noteagent.project.init import init_project
from noteagent.task.model import Task
from noteagent.task.store import TaskStore

try:
    from pdf_fixtures import HELLO_PDF
except ImportError:  # python -m unittest tests.test_pipeline_materials
    from tests.pdf_fixtures import HELLO_PDF

_LONG = "熵是不确定性的度量，它衡量一个随机变量的平均信息量。"


def _clock(hour: int = 9) -> FixedClock:
    return FixedClock(datetime(2026, 11, 10, hour, tzinfo=timezone.utc))


def _capture(**overrides: object) -> Capture:
    html = (
        "<html><head><title>熵</title></head><body><article>"
        f'<p data-na-block="b1">{_LONG}</p>'
        '<p data-na-block="b2">香农把它引入信息论。</p>'
        "</article></body></html>"
    )
    values: dict[str, object] = {
        "url": "https://example.com/entropy",
        "title": "熵",
        "markdown": _LONG,
        "html": html,
        "capture_method": "test",
        "fidelity": "ok",
        "reasons": (),
    }
    values.update(overrides)
    return Capture(**values)  # type: ignore[arg-type]


def _pdf_capture() -> Capture:
    return Capture(
        url="https://example.com/paper.pdf",
        title="Paper",
        markdown="[p1] Hello PDF",
        html="",
        capture_method="pdf-http",
        fidelity="ok",
        reasons=(),
        kind="pdf",
        pdf_bytes=HELLO_PDF,
        page_count=1,
        char_count=9,
    )


def _run(run_id: str, hour: int = 9) -> PipelineRun:
    stamped = f"2026-11-10T{hour:02d}:00:00Z"
    return PipelineRun(
        id=run_id,
        intent="写一篇熵的笔记",
        started_by="human",
        started_at=stamped,
        updated_at=stamped,
    )


class StagingTests(unittest.TestCase):
    def test_a_capture_becomes_a_staged_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())

            state = materials.stage(_capture(), run_id="pr-1")

            self.assertEqual(state.state, "staged")
            self.assertTrue(state.material.startswith("raw/web/"))
            directory = layout.staging / "pr-1" / state.material.replace("/", "__")
            self.assertTrue((directory / "manifest.json").is_file())
            self.assertTrue((directory / "source.html").is_file())
            self.assertTrue((directory / "blocks.json").is_file())

            manifest = json.loads(
                (directory / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["material"], state.material)
            self.assertEqual(manifest["blockCount"], 2)
            self.assertEqual(manifest["blocks"][0]["id"], "b1")
            self.assertEqual(manifest["blocks"][0]["preview"], preview_text(_LONG))
            self.assertEqual(len(manifest["blocks"][0]["preview"]), 20)

            blocks = json.loads(
                (directory / "blocks.json").read_text(encoding="utf-8")
            )
            self.assertEqual(blocks[0]["text"], _LONG)

    def test_a_run_id_that_escapes_staging_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())

            with self.assertRaises(ValueError):
                materials.stage(_capture(), run_id="../../escape")

            self.assertFalse(layout.staging.exists())

    def test_the_manifest_can_be_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")

            manifest = materials.manifest(state.material, run_id="pr-1")

            self.assertIsNotNone(manifest)
            self.assertEqual(manifest["blockCount"], 2)
            self.assertEqual(len(str(manifest["sourceDigest"])), 64)
            self.assertEqual(len(str(manifest["blocksDigest"])), 64)

    def test_a_failed_capture_is_not_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())

            with self.assertRaises(ValueError):
                materials.stage(_capture(fidelity="failed", reasons=("empty",)), run_id="pr-1")

            self.assertEqual(materials.for_run("pr-1"), [])
            self.assertFalse(layout.staging.exists())


class MaterialLogTests(unittest.TestCase):
    def _log(self, layout) -> MaterialLog:
        return MaterialLog(layout.materials_log)

    def test_the_latest_transition_is_the_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            log = self._log(layout)
            log.append(
                MaterialTransition(
                    at="2026-11-10T09:00:00Z",
                    material="raw/web/entropy-a1.html",
                    state="staged",
                    run_id="pr-1",
                    detail={"title": "熵"},
                )
            )
            log.append(
                MaterialTransition(
                    at="2026-11-10T09:05:00Z",
                    material="raw/web/entropy-a1.html",
                    state="promoted",
                    run_id="pr-1",
                )
            )

            state = log.state_for("raw/web/entropy-a1.html")

            self.assertEqual(state.state, "promoted")
            self.assertEqual(state.updated_at, "2026-11-10T09:05:00Z")
            self.assertEqual([item.state for item in log.for_run("pr-1")], ["promoted"])

    def test_a_malformed_line_does_not_lose_the_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            log = self._log(layout)
            log.append(
                MaterialTransition(
                    at="2026-11-10T09:00:00Z",
                    material="raw/web/entropy-a1.html",
                    state="staged",
                    run_id="pr-1",
                )
            )
            layout.materials_log.write_text(
                layout.materials_log.read_text(encoding="utf-8") + "{not json}\n",
                encoding="utf-8",
            )

            self.assertIsNotNone(log.state_for("raw/web/entropy-a1.html"))


class BackfillTests(unittest.TestCase):
    def test_raw_files_get_one_promoted_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            raw = layout.raw / "web" / "entropy-a1.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("<html></html>", encoding="utf-8")
            materials = Materials(layout, clock=_clock())

            self.assertEqual(materials.backfill(), 1)
            self.assertEqual(materials.backfill(), 0)

            state = materials.states()["raw/web/entropy-a1.html"]
            self.assertEqual(state.state, "promoted")
            self.assertTrue(state.detail.get("backfilled"))

    def test_a_later_promotion_does_not_take_a_material_out_of_its_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")
            MaterialLog(layout.materials_log).append(
                MaterialTransition(
                    at="2026-11-10T09:05:00Z",
                    material=state.material,
                    state="promoted",
                    run_id="pr-2",
                )
            )

            listed = materials.for_run("pr-1")

            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].state, "promoted")
            self.assertEqual(listed[0].title, "熵")
            self.assertEqual(materials.for_run("pr-2"), [])

    def test_re_staging_moves_a_material_to_the_later_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            first = materials.stage(_capture(), run_id="pr-1")
            materials.stage(_capture(), run_id="pr-2")

            self.assertEqual(materials.for_run("pr-1"), [])
            listed = materials.for_run("pr-2")
            self.assertEqual([state.material for state in listed], [first.material])

    def test_a_staged_material_whose_file_is_raw_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")
            raw = layout.root / state.material
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("<html></html>", encoding="utf-8")

            self.assertEqual(materials.backfill(), 1)
            self.assertEqual(materials.states()[state.material].state, "promoted")


class PromotionTests(unittest.TestCase):
    def test_a_staged_original_is_copied_under_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")
            staged = (
                layout.staging
                / "pr-1"
                / state.material.replace("/", "__")
                / "source.html"
            )
            raw = layout.root / state.material
            self.assertFalse(raw.exists())

            promoted = materials.promote(state.material, run_id="pr-1")

            self.assertEqual(promoted.state, "promoted")
            self.assertEqual(raw.read_bytes(), staged.read_bytes())
            promoted_rows = [
                item
                for item in MaterialLog(layout.materials_log).transitions()
                if item.state == "promoted"
            ]
            self.assertEqual(len(promoted_rows), 1)
            self.assertEqual(promoted_rows[0].run_id, "pr-1")

    def test_promoting_twice_writes_one_file_and_one_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")

            materials.promote(state.material, run_id="pr-1")
            materials.promote(state.material, run_id="pr-1")

            raw_files = [path for path in layout.raw.rglob("*") if path.is_file()]
            self.assertEqual([path.name for path in raw_files], [Path(state.material).name])
            promoted_rows = [
                item
                for item in MaterialLog(layout.materials_log).transitions()
                if item.state == "promoted"
            ]
            self.assertEqual(len(promoted_rows), 1)

    def test_a_binary_original_keeps_its_block_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_pdf_capture(), run_id="pr-1")

            materials.promote(state.material, run_id="pr-1")

            self.assertEqual((layout.root / state.material).read_bytes(), HELLO_PDF)
            sidecar = load_derived_payload(layout, state.material)
            self.assertIsNotNone(sidecar)
            self.assertEqual(sidecar["blocks"][0]["id"], "p1")
            self.assertIn("Hello PDF", sidecar["blocks"][0]["text"])

    def test_a_material_with_no_staged_source_and_no_raw_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            state = materials.stage(_capture(), run_id="pr-1")
            directory = layout.staging / "pr-1" / state.material.replace("/", "__")
            for path in directory.glob("source.*"):
                path.unlink()

            with self.assertRaises(PromotionError):
                materials.promote(state.material, run_id="pr-1")

    def test_unused_material_stays_staged_until_retention_drops_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            used = materials.stage(_capture(), run_id="pr-1")
            unused = materials.stage(
                _capture(url="https://example.com/other"), run_id="pr-1"
            )

            materials.promote(used.material, run_id="pr-1")

            states = materials.states()
            self.assertEqual(states[used.material].state, "promoted")
            self.assertEqual(states[unused.material].state, "staged")
            self.assertTrue((layout.staging / "pr-1").is_dir())

            materials.clean([_run("pr-1")], keep=0)

            states = materials.states()
            self.assertEqual(states[used.material].state, "promoted")
            self.assertEqual(states[unused.material].state, "dropped")


class RetentionTests(unittest.TestCase):
    def test_the_newest_three_runs_keep_their_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            for hour in (9, 10, 11, 12):
                materials.stage(
                    _capture(url=f"https://example.com/{hour}"),
                    run_id=f"pr-{hour}",
                )

            removed = materials.clean(
                [_run(f"pr-{hour}", hour) for hour in (9, 10, 11, 12)]
            )

            self.assertEqual(removed, 1)
            self.assertEqual(DEFAULT_KEEP_RUNS, 3)
            for hour in (10, 11, 12):
                self.assertTrue((layout.staging / f"pr-{hour}").is_dir())
            self.assertFalse((layout.staging / "pr-9").exists())
            # What was pruned says so; it must not keep claiming blocks whose
            # cache is gone.
            pruned = [
                state
                for state in materials.states().values()
                if state.run_id == "pr-9"
            ]
            self.assertEqual([state.state for state in pruned], ["dropped"])


class TaskRetentionTests(unittest.TestCase):
    def test_the_active_tasks_staging_survives_a_pipeline_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            TaskStore(layout).save(
                Task(
                    id="task-1",
                    created_at="2026-11-10T09:00:00Z",
                    updated_at="2026-11-10T09:00:00Z",
                )
            )
            materials = Materials(layout, clock=_clock())
            kept = materials.stage(_capture(), run_id="task-1")
            # The task staged its material before the runs; without protection
            # retention would see it as the oldest folder and drop it.
            old = datetime(2026, 11, 10, 8, tzinfo=timezone.utc).timestamp()
            os.utime(layout.staging / "task-1", (old, old))
            for hour in (10, 11, 12, 13):
                materials.stage(
                    _capture(url=f"https://example.com/{hour}"),
                    run_id=f"pr-{hour}",
                )

            removed = materials.clean(
                [_run(f"pr-{hour}", hour) for hour in (10, 11, 12, 13)],
                keep=1,
            )

            # The three old runs go; the newest run and the task's staging stay.
            self.assertEqual(removed, 3)
            self.assertTrue((layout.staging / "task-1").is_dir())
            self.assertTrue((layout.staging / "pr-13").is_dir())
            for hour in (10, 11, 12):
                self.assertFalse((layout.staging / f"pr-{hour}").exists())
            # The material is still staged, not dropped with a pipeline run.
            self.assertEqual(materials.states()[kept.material].state, "staged")

    def test_without_a_task_old_staging_is_still_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = Materials(layout, clock=_clock())
            dropped = materials.stage(
                _capture(url="https://example.com/old"), run_id="pr-9"
            )
            materials.stage(
                _capture(url="https://example.com/new"), run_id="pr-13"
            )

            removed = materials.clean(
                [_run("pr-9", 9), _run("pr-13", 13)], keep=1
            )

            self.assertEqual(removed, 1)
            self.assertFalse((layout.staging / "pr-9").exists())
            self.assertEqual(materials.states()[dropped.material].state, "dropped")


if __name__ == "__main__":
    unittest.main()
