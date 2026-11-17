"""Deterministic frames: headings first, the block cap last."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from agent_fakes import FixedClock
from noteagent.acquire.types import Capture
from noteagent.ingest.clips import (
    MAX_BLOCKS_PER_COLLECTION,
    load_clip_store,
    save_clip_store,
)
from noteagent.pipeline.framing import Framing, frame_blocks
from noteagent.pipeline.materials import Materials
from noteagent.project.init import init_project

MAX = MAX_BLOCKS_PER_COLLECTION


def _blocks(tags: list[str]) -> list[dict[str, str]]:
    return [
        {"id": f"b{index}", "tag": tag, "text": f"text {index}", "preview": ""}
        for index, tag in enumerate(tags)
    ]


def _capture(url: str, body: str) -> Capture:
    html = f"<html><body><article>{body}</article></body></html>"
    return Capture(
        url=url,
        title=url.rsplit("/", 1)[-1],
        markdown="",
        html=html,
        capture_method="test",
        fidelity="ok",
        reasons=(),
    )


class FrameBlocksTests(unittest.TestCase):
    def test_a_heading_opens_a_frame(self) -> None:
        frames = frame_blocks(
            _blocks(["h2", "p", "p", "h2", "p"])
        )

        self.assertEqual(
            frames, [["b0", "b1", "b2"], ["b3", "b4"]]
        )

    def test_blocks_before_the_first_heading_form_a_frame(self) -> None:
        frames = frame_blocks(_blocks(["p", "h2", "p"]))

        self.assertEqual(frames, [["b0"], ["b1", "b2"]])

    def test_a_long_headingless_document_is_cut_at_the_block_cap(self) -> None:
        # Prose without headings has no page structure to use.
        frames = frame_blocks(_blocks(["p"] * 450))

        self.assertEqual([len(frame) for frame in frames], [MAX, MAX, 50])

    def test_each_page_is_a_frame_when_there_are_no_headings(self) -> None:
        frames = frame_blocks(_blocks(["page"] * 5))

        self.assertEqual([len(frame) for frame in frames], [1, 1, 1, 1, 1])

    def test_many_pages_are_gathered_into_eight_frames(self) -> None:
        frames = frame_blocks(_blocks(["page"] * 50))

        self.assertEqual(len(frames), 8)
        self.assertEqual(sum(len(frame) for frame in frames), 50)
        self.assertTrue(all(len(frame) <= MAX for frame in frames))

    def test_the_collection_cap_wins_over_the_eight_frame_target(self) -> None:
        # Ten sections of 250 blocks: eight frames cannot hold all of it under
        # the 200-block collection cap, so the document gets more than eight.
        blocks: list[dict[str, str]] = []
        for section in range(10):
            blocks.append({"id": f"h{section}", "tag": "h2", "text": "", "preview": ""})
            blocks.extend(
                {
                    "id": f"s{section}-{index}",
                    "tag": "p",
                    "text": "",
                    "preview": "",
                }
                for index in range(249)
            )
        frames = frame_blocks(blocks)

        self.assertGreater(len(frames), 8)
        self.assertTrue(all(len(frame) <= MAX for frame in frames))
        self.assertEqual(sum(len(frame) for frame in frames), 2500)

    def test_a_long_section_is_cut_at_the_block_cap(self) -> None:
        frames = frame_blocks(_blocks(["h2"] + ["p"] * 449))

        self.assertEqual([len(frame) for frame in frames], [MAX, MAX, 50])
        self.assertEqual(frames[0][0], "b0")

    def test_many_headings_are_gathered_into_at_most_eight_frames(self) -> None:
        frames = frame_blocks(_blocks(["h2", "p"] * 10))

        self.assertEqual(len(frames), 8)
        self.assertTrue(all(len(frame) <= MAX for frame in frames))
        self.assertEqual(sum(len(frame) for frame in frames), 20)

    def test_an_empty_document_has_no_frames(self) -> None:
        self.assertEqual(frame_blocks([]), [])


class FramingTests(unittest.TestCase):
    def _materials(self, layout) -> Materials:
        return Materials(
            layout,
            clock=FixedClock(
                datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
            ),
        )

    def test_a_material_becomes_frames_of_block_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = self._materials(layout)
            state = materials.stage(
                _capture(
                    "https://example.com/entropy",
                    '<h2 data-na-block="h1">定义</h2>'
                    '<p data-na-block="p1">熵是不确定性的度量。</p>'
                    '<h2 data-na-block="h2">性质</h2>'
                    '<p data-na-block="p2">它是可加的。</p>',
                ),
                run_id="pr-1",
            )
            framing = Framing(layout, materials=materials)

            framed = framing.frame_run("pr-1")

            self.assertEqual(framed, 1)
            store = load_clip_store(layout)
            frames = [
                collection
                for collection in store.collections
                if collection.source_key == state.material
            ]
            self.assertEqual(len(frames), 2)
            self.assertEqual(frames[0].block_ids, ("h1", "p1"))
            self.assertEqual(frames[1].block_ids, ("h2", "p2"))
            self.assertEqual(frames[0].blocks, ())
            self.assertEqual(
                materials.states()[state.material].state, "framed"
            )

    def test_new_frames_clear_the_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "projects" / "demo")
            materials = self._materials(layout)
            url = "https://example.com/entropy"
            materials.stage(
                _capture(
                    url,
                    '<h2 data-na-block="h1">定义</h2>'
                    '<p data-na-block="p1">熵是不确定性的度量。</p>',
                ),
                run_id="pr-1",
            )
            framing = Framing(layout, materials=materials)
            framing.frame_run("pr-1")
            store = load_clip_store(layout)
            save_clip_store(
                layout, replace(store, topic_map={"intent": "旧计划"})
            )

            materials.stage(
                _capture(
                    url,
                    '<h2 data-na-block="h1">定义</h2>'
                    '<p data-na-block="p1">熵是不确定性的度量。</p>'
                    '<p data-na-block="p2">香农把它引入信息论。</p>',
                ),
                run_id="pr-1",
            )
            framing.frame_run("pr-1")

            self.assertIsNone(load_clip_store(layout).topic_map)


if __name__ == "__main__":
    unittest.main()
