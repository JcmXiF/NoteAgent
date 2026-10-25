from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    save_clip_store,
)
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.ingest_recovery import (
    discard_recovery_job,
    recovery_file_path,
    recovery_payload,
    remember_confirmed_job,
)
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled

IDENTITY = "raw/web/example.com/kl.html"
STAMPED = (
    '<p data-na-block="b1">KL 是相对熵。</p>\n'
    '<p data-na-block="b2">信息熵。</p>\n'
)
MAP = {
    "primary": {
        "name": "KL散度",
        "slug": "kl-divergence",
        "wikiPath": "wiki/kl-divergence.md",
        "collectionIds": ["c1"],
        "blockIds": ["b1"],
        "evidence": "[b1]",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
    },
    "alsoInSources": [],
}
GENERATION = """---FILE: wiki/kl-divergence.md---
---
title: KL散度
created: 2026-09-04
updated: 2026-09-04
sources: []
---

# KL散度

相对熵。
---END FILE---
"""
CONFIRMED = [
    {
        "name": "KL散度",
        "slug": "kl-divergence",
        "wikiPath": "wiki/kl-divergence.md",
        "action": "write",
        "sectionIds": ["s1"],
    }
]


def _store() -> ClipStore:
    return ClipStore(
        intent="KL散度",
        collections=(
            ClipCollection(
                id="c1",
                source_key=IDENTITY,
                source_label="KL",
                block_ids=("b1", "b2"),
                blocks=(
                    DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                    DerivedBlock(id="b2", tag="p", text="信息熵。", preview="熵"),
                ),
            ),
        ),
        topic_map=MAP,
    )


def _seed(layout):
    path = layout.raw / "web" / "example.com" / "kl.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STAMPED, encoding="utf-8")
    store = _store()
    save_clip_store(layout, store)
    return store


class IngestRecoveryTests(unittest.TestCase):
    def test_payload_does_not_start_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            remember_confirmed_job(
                layout,
                intent="KL散度",
                confirmed=CONFIRMED,
                write_paths=["wiki/kl-divergence.md"],
            )
            with (
                patch("noteagent.ingest.confirmed.ingest_confirmed") as ingest,
                patch("noteagent.api.topic_gate.run_topic_map_job") as mapped,
            ):
                payload = recovery_payload(layout)
            ingest.assert_not_called()
            mapped.assert_not_called()
            self.assertEqual(payload["openCount"], 1)
            self.assertEqual(payload["items"][0]["confirmed"][0]["slug"], "kl-divergence")
            self.assertTrue(recovery_file_path(layout).is_file())

    def test_a_finished_round_is_kept_and_its_job_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed(layout)
            result = ingest_confirmed(
                layout,
                confirmed=CONFIRMED,
                generation_text=GENERATION,
            )
            payload = recovery_payload(layout)
            # The job was for whoever was watching it finish; the round is what
            # the project keeps, so what it wrote is still readable later.
            self.assertEqual(payload["openCount"], 0)
            self.assertEqual(payload["items"], [])
            round_ = payload["last"]
            self.assertIsNotNone(round_)
            self.assertEqual(round_["intent"], "KL散度")
            self.assertTrue(round_["finishedAt"])
            written = [item["path"] for item in round_["written"]]
            self.assertEqual(
                written,
                [item.relative_path for item in result.write.written],
            )
            self.assertIn("wiki/kl-divergence.md", written)
            self.assertTrue(all(item["mode"] for item in round_["written"]))
            # The check report travels with it, or the panel would have to say
            # "no problems" about a round it knows nothing about.
            self.assertIsNotNone(round_["doctor"])
            self.assertEqual(round_["doctor"]["findingCount"], 2)
            self.assertIn("findings", round_["doctor"])

    def test_a_pending_job_does_not_drop_the_round_that_finished(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed(layout)
            ingest_confirmed(layout, confirmed=CONFIRMED, generation_text=GENERATION)
            remember_confirmed_job(
                layout,
                intent="KL散度",
                confirmed=CONFIRMED,
                write_paths=["wiki/kl-divergence.md"],
            )
            payload = recovery_payload(layout)
            # One file, two readers: the job is for resuming, the round is for
            # reporting, and neither may eat the other.
            self.assertEqual(payload["openCount"], 1)
            self.assertIsNotNone(payload["last"])

    def test_cancel_keeps_recovery_and_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed(layout)
            cancel = threading.Event()

            def on_status(_message: str, **_kwargs) -> None:
                cancel.set()

            with self.assertRaises(JobCancelled):
                ingest_confirmed(
                    layout,
                    confirmed=CONFIRMED,
                    generation_text=GENERATION,
                    cancel=cancel,
                    on_status=on_status,
                )
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())
            payload = recovery_payload(layout)
            self.assertEqual(payload["openCount"], 1)
            self.assertEqual(
                payload["items"][0]["writePaths"],
                ["wiki/kl-divergence.md"],
            )
            # Nothing finished, so there is no round to report.
            self.assertIsNone(payload["last"])

    def test_dry_run_does_not_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed(layout)
            ingest_confirmed(
                layout,
                confirmed=CONFIRMED,
                generation_text=GENERATION,
                dry_run=True,
            )
            self.assertFalse(recovery_file_path(layout).is_file())

    def test_discard_does_not_remove_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed(layout)
            ingest_confirmed(
                layout,
                confirmed=CONFIRMED,
                generation_text=GENERATION,
            )
            page = layout.wiki / "kl-divergence.md"
            self.assertTrue(page.is_file())
            job_id = remember_confirmed_job(
                layout,
                intent="KL散度",
                confirmed=CONFIRMED,
                write_paths=["wiki/kl-divergence.md"],
            )
            closed = discard_recovery_job(layout, job_id)
            self.assertEqual(closed["openCount"], 0)
            self.assertTrue(page.is_file())
            # Discarding the job is not deleting the round that finished earlier.
            self.assertIsNotNone(closed["last"])
            self.assertTrue(recovery_file_path(layout).is_file())

    def test_corrupt_file_is_empty_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = recovery_file_path(layout)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            payload = recovery_payload(layout)
            self.assertEqual(payload["openCount"], 0)
            self.assertEqual(payload["items"], [])


if __name__ == "__main__":
    unittest.main()
