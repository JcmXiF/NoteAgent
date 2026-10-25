"""Where a round's pages live, whichever way the round came by them.

A round that proposes pages before reading and a round whose plan names them are the
same pipeline: one list per round, kept with the round. That is the difference the
two modes are allowed to have — whether the pages were named before or after reading
— and nothing else about where they are kept.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from noteagent.api.topic_gate import run_topic_map_job
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.confirmed import ingest_confirmed
from noteagent.ingest.deep_search_store import (
    DeepSearchSession,
    WhitelistPage,
    save_session,
)
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal
from noteagent.wiki.deep_search import (
    act_session,
    load_session,
    proposals_map,
    round_is_planning,
)

RAW = "raw/web/sample.html"
PLAN = {
    "primary": {
        "title": "联合熵",
        "slug": "concepts/joint-entropy",
        "wikiPath": "wiki/concepts/joint-entropy.md",
        "collectionIds": ["round-1"],
        "blockIds": ["b1"],
        "evidence": "集合的 [b1] 给出定义。",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"], "role": "definition"}],
    },
    "alsoInSources": [
        {
            "title": "信息熵",
            "slug": "concepts/information-entropy",
            "wikiPath": "wiki/concepts/information-entropy.md",
            "relationToPrimary": "prerequisite",
            "collectionIds": ["round-1"],
            "blockIds": ["b1"],
            "evidence": "[b1]",
            "outline": [],
        }
    ],
    "edges": [],
    "notes": "",
}


def _chew_round_at_step4(layout) -> str:
    """A round that proposes no pages: its material is what it picked up."""
    path = layout.root / RAW
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<p id="na-b1" data-na-block="b1">联合熵 定义</p>\n', encoding="utf-8")
    act_session(layout, {"action": "start", "mode": "chew"})
    act_session(
        layout,
        {"action": "proposeBlocks"},
        script={
            "blocks": [
                {"pageSlug": "联合熵", "locator": f"{RAW}#b1", "preview": "联合熵 定义"}
            ]
        },
    )
    act_session(layout, {"action": "selectAllBlocks", "accepted": True})
    act_session(layout, {"action": "acceptBlocks"})
    session = load_session(layout)
    return session.round_id


class OpenRoundTests(unittest.TestCase):
    def test_a_round_with_no_proposed_pages_is_still_a_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_round_at_step4(layout)
            session = load_session(layout)
            self.assertEqual(session.whitelist, ())
            self.assertTrue(round_is_planning(layout))
            self.assertIsNone(proposals_map(layout))

    def test_its_plan_becomes_its_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_round_at_step4(layout)
            payload = run_topic_map_job(layout, map_text=json.dumps(PLAN))
            session = load_session(layout)
            self.assertEqual(
                [item.slug for item in session.whitelist],
                ["concepts/joint-entropy", "concepts/information-entropy"],
            )
            # And the plan is readable back, so the panel and the compile agree.
            mapped = proposals_map(layout)
            self.assertIsNotNone(mapped)
            self.assertEqual(mapped.primary.slug, "concepts/joint-entropy")
            self.assertEqual(payload["primary"]["slug"], "concepts/joint-entropy")

    def test_nothing_is_written_beside_it(self) -> None:
        # The project's own plan is for everyday planning; a round does not touch it.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(),
                    topic_map={"primary": {"title": "日常篇", "slug": "daily-note"}},
                ),
            )
            _chew_round_at_step4(layout)
            run_topic_map_job(layout, map_text=json.dumps(PLAN))
            store = load_clip_store(layout)
            self.assertEqual(store.topic_map["primary"]["slug"], "daily-note")
            self.assertEqual(store.intent, "日常")

    def test_a_round_does_not_write_its_own_intent_into_the_project(self) -> None:
        # The round's intent is the round's; the project's document keeps the
        # project's, so an everyday plan is not quietly reworded by a search.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            save_clip_store(
                layout,
                ClipStore(intent="平时的目的", collections=(), topic_map=None),
            )
            path = layout.root / RAW
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<p id="na-b1" data-na-block="b1">联合熵</p>\n', encoding="utf-8")
            act_session(layout, {"action": "start", "mode": "chew", "intent": "这一轮的目的"})
            act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {"pageSlug": "联合熵", "locator": f"{RAW}#b1", "preview": "联合熵"}
                    ]
                },
            )
            act_session(layout, {"action": "selectAllBlocks", "accepted": True})
            act_session(layout, {"action": "acceptBlocks"})
            store = load_clip_store(layout)
            self.assertEqual(store.intent, "平时的目的")
            # The round kept its own.
            self.assertEqual(load_session(layout).intent, "这一轮的目的")

    def test_the_reader_still_owns_the_pages_a_search_proposed(self) -> None:
        # The other way round: a plan that refines an existing list keeps what the
        # reader said about each page.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            path = layout.root / RAW
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<p id="na-b1" data-na-block="b1">x</p>\n', encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(
                        ClipCollection(
                            id="round-1",
                            source_key=RAW,
                            source_label="sample",
                            block_ids=("b1",),
                            blocks=(DerivedBlock(id="b1", tag="p", text="x", preview="x"),),
                            round_id="r1",
                        ),
                    ),
                    topic_map=None,
                ),
            )
            save_session(
                layout,
                DeepSearchSession(
                    round_id="r1",
                    mode="explore",
                    step=4,
                    whitelist=(
                        WhitelistPage(slug="concepts/joint-entropy", title="联合熵", accepted=False),
                        WhitelistPage(slug="unrelated/page", title="别的页"),
                    ),
                ),
            )
            run_topic_map_job(layout, map_text=json.dumps(PLAN))
            session = load_session(layout)
            by_slug = {item.slug: item for item in session.whitelist}
            # Refined by the plan, still the reader's answer.
            self.assertEqual(by_slug["concepts/joint-entropy"].coverage, "write")
            self.assertFalse(by_slug["concepts/joint-entropy"].accepted)
            # A page the plan did not mention stays on the list, marked as not written.
            self.assertEqual(by_slug["unrelated/page"].coverage, "skip")


class CompileFromTheRoundTests(unittest.TestCase):
    def test_a_chew_round_compiles_from_its_own_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_round_at_step4(layout)
            run_topic_map_job(layout, map_text=json.dumps(PLAN))
            # A page on the round's list compiles; one that is not on it is refused.
            # Both come from the same list, which is the round's own.
            ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "联合熵",
                        "slug": "concepts/joint-entropy",
                        "action": "write",
                        "sectionIds": ["s1"],
                    }
                ],
                generation_text="# 联合熵\n\n[[信息熵]]\n",
                dry_run=True,
            )
            with self.assertRaises(Refusal) as caught:
                ingest_confirmed(
                    layout,
                    confirmed=[
                        {
                            "name": "无中生有",
                            "slug": "nowhere",
                            "action": "write",
                            "sectionIds": [],
                        }
                    ],
                    generation_text="# x\n",
                    dry_run=True,
                )
            self.assertEqual(caught.exception.code, "compile.notInPlan")


if __name__ == "__main__":
    unittest.main()
