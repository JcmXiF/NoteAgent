"""A selection can say which page it is for.

Saying so is how a reader takes charge of the page: it is a target of its own, so it
reaches planning as one — including a page the round's list does not have, and
including material outside any round. It deliberately does *not* filter the round's
list: a filtered list that comes out empty reads downstream as "no list at all",
which lets the plan name pages of its own and silently ignores what was asked for.
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
    save_clip_store,
)
from noteagent.project.init import init_project
from noteagent.wiki.deep_search import (
    act_session,
    load_session,
    plan_targets,
    round_targets,
)

PLAN = {
    "primary": {
        "name": "互信息",
        "slug": "concepts/mutual-information",
        "wikiPath": "wiki/concepts/mutual-information.md",
        "collectionIds": ["round-1"],
        "blockIds": ["b1"],
        "evidence": "集合「互信息」的 [b1] 给出定义。",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"], "role": "definition"}],
    },
    "alsoInSources": [],
    "edges": [],
    "notes": "",
}
RAW = "raw/web/sample.html"


def _collection(ident: str, round_id: str = "", target: str = "") -> ClipCollection:
    return ClipCollection(
        id=ident,
        source_key=RAW,
        source_label="sample",
        block_ids=("b1",),
        blocks=(DerivedBlock(id="b1", tag="p", text="互信息定义", preview="互信息"),),
        round_id=round_id,
        target_slug=target,
    )


class PlanTargetsTests(unittest.TestCase):
    def test_nothing_decided_means_the_pass_names_its_own_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.root / "raw/web").mkdir(parents=True, exist_ok=True)
            (layout.root / RAW).write_text("<p>x</p>\n", encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(_collection("daily-1"),),
                    topic_map=None,
                ),
            )
            self.assertIsNone(plan_targets(layout))

    def test_a_selection_can_name_the_page_it_is_for(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.root / "raw/web").mkdir(parents=True, exist_ok=True)
            (layout.root / RAW).write_text("<p>x</p>\n", encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(_collection("daily-1", target="concepts/entropy"),),
                    topic_map=None,
                ),
            )
            targets = plan_targets(layout)
            self.assertEqual([item.slug for item in targets], ["concepts/entropy"])

    def test_a_binding_works_without_any_round(self) -> None:
        # Everyday planning is the same pipeline: naming a page is what turns an open
        # pass into one that works on that page.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            (layout.root / "raw/web").mkdir(parents=True, exist_ok=True)
            (layout.root / RAW).write_text("<p>x</p>\n", encoding="utf-8")
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(_collection("daily-1", target="concepts/entropy"),),
                    topic_map=None,
                ),
            )
            targets = plan_targets(layout)
            self.assertEqual([item.slug for item in targets], ["concepts/entropy"])
            # The round-specific reader agrees: there is no list from a round here.
            self.assertIsNone(round_targets(layout))


class RoundBindingTests(unittest.TestCase):
    """A search that is planning: what a binding does to its list, and to targets."""

    def _round_at_step4(self, layout, *, binding: str = "") -> str:
        path = layout.root / RAW
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<p id="na-b1" data-na-block="b1">互信息定义</p>\n', encoding="utf-8")
        act_session(layout, {"action": "start", "mode": "explore"})
        act_session(
            layout,
            {"action": "proposeWhitelist"},
            script={
                "pages": [
                    {
                        "slug": "concepts/mutual-information",
                        "title": "互信息",
                        "action": "add",
                        "query": "互信息",
                    }
                ]
            },
            use_llm=False,
        )
        act_session(layout, {"action": "acceptWhitelist"}, use_llm=False)
        session = load_session(layout)
        save_clip_store(
            layout,
            ClipStore(
                intent="",
                collections=(
                    _collection("round-1", round_id=session.round_id, target=binding),
                ),
                topic_map=None,
            ),
        )
        act_session(layout, {"action": "enterPlan"}, use_llm=False)
        return session.round_id

    def test_the_rounds_list_survives_a_binding_to_a_page_outside_it(self) -> None:
        # The list is not narrowed by the binding: an empty list would read
        # downstream as "no list at all".
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_at_step4(layout, binding="concepts/something-new")
            session = load_session(layout)
            self.assertEqual(
                [item.slug for item in session.whitelist],
                ["concepts/mutual-information"],
            )
            targets = plan_targets(layout)
            self.assertEqual(
                [item.slug for item in targets],
                ["concepts/something-new", "concepts/mutual-information"],
            )

    def test_a_binding_to_a_page_the_list_has_is_not_listed_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_at_step4(layout, binding="concepts/mutual-information")
            targets = plan_targets(layout)
            self.assertEqual([item.slug for item in targets], ["concepts/mutual-information"])

    def test_without_a_binding_the_list_is_the_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_at_step4(layout)
            targets = plan_targets(layout)
            self.assertEqual([item.slug for item in targets], ["concepts/mutual-information"])
            self.assertEqual(round_targets(layout), targets)

    def test_planning_writes_the_page_that_was_asked_for(self) -> None:
        # End to end through the planning call. Targets are an allowance rather than
        # a requirement — the plan still chooses — so a plan that names the page the
        # reader asked for is bound to it, instead of being flagged as a surprise.
        asked = dict(PLAN)
        asked["primary"] = {**PLAN["primary"], "slug": "concepts/something-new", "name": "新页"}
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_at_step4(layout, binding="concepts/something-new")
            payload = run_topic_map_job(layout, map_text=json.dumps(asked))
            nodes = {item["slug"]: item for item in payload["candidates"]}
            self.assertIn("concepts/something-new", nodes)
            self.assertFalse(nodes["concepts/something-new"]["unlisted"])

    def test_a_plan_that_ignores_the_binding_still_has_the_list_to_work_from(self) -> None:
        # Targets are an allowance: a plan that names a listed page is still accepted,
        # and the page the reader asked for simply is not in it.
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_at_step4(layout, binding="concepts/something-new")
            payload = run_topic_map_job(layout, map_text=json.dumps(PLAN))
            nodes = {item["slug"]: item for item in payload["candidates"]}
            self.assertIn("concepts/mutual-information", nodes)
            self.assertFalse(nodes["concepts/mutual-information"]["unlisted"])


if __name__ == "__main__":
    unittest.main()
