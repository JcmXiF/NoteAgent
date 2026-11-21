"""Deterministic frames: headings first, the block cap last."""

from __future__ import annotations

import unittest

from noteagent.ingest.clips import MAX_BLOCKS_PER_COLLECTION
from noteagent.pipeline.framing import frame_blocks

MAX = MAX_BLOCKS_PER_COLLECTION


def _blocks(tags: list[str]) -> list[dict[str, str]]:
    return [
        {"id": f"b{index}", "tag": tag, "text": f"text {index}", "preview": ""}
        for index, tag in enumerate(tags)
    ]



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
