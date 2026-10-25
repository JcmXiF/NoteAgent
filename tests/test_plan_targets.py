"""A pass is told which pages it is about.

The pages a search proposes before reading anything, and the pages a plan names from
what it read, are two model calls that cannot see each other. The list therefore
travels with the request, and a page the list does not have is kept and marked
instead of being dropped.
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
from noteagent.ingest.confirmed import confirmed_from_dict, ingest_confirmed
from noteagent.ingest.topic_map import (
    PlanTarget,
    map_clips,
    parse_topic_map,
    resolve_map_to_targets,
    target_for_name,
    target_index,
)
from noteagent.prompts.topic_map import build_topic_map_prompt
from noteagent.io.read_source import SourceDocument
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal
from noteagent.wiki.deep_search import act_session, load_session, proposals_map, round_targets

PLAN = {
    "primary": {
        "name": "互信息",
        "slug": "mutual-information",
        "wikiPath": "wiki/concepts/mutual-information.md",
        "collectionIds": ["c1"],
        "blockIds": ["b1"],
        "evidence": "集合「互信息」的 [b1] 给出定义。",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"], "role": "definition"}],
    },
    "alsoInSources": [
        {
            "name": "信息论与其他学科",
            "slug": "information-theory-and-other-disciplines",
            "wikiPath": "wiki/concepts/information-theory-and-other-disciplines.md",
            "relationToPrimary": "cooccurs",
            "collectionIds": ["c1"],
            "blockIds": ["b1"],
            "evidence": "集合「互信息」的 [b1] 提到跨学科。",
            "outline": [],
        }
    ],
    "edges": [
        {
            "from": "mutual-information",
            "to": "information-theory-and-other-disciplines",
            "relation": "cooccurs",
            "collectionId": "c1",
            "blockIds": ["b1"],
            "evidence": "[b1]",
        }
    ],
    "notes": "",
}


def _parse(payload: dict, collections: tuple[ClipCollection, ...]):
    return parse_topic_map(json.dumps(payload), intent="", collections=collections)


def _collection(ident: str = "c1", round_id: str = "") -> ClipCollection:
    return ClipCollection(
        id=ident,
        source_key="raw/web/sample.html",
        source_label="sample",
        block_ids=("b1",),
        blocks=(DerivedBlock(id="b1", tag="p", text="互信息定义", preview="互信息"),),
        round_id=round_id,
    )


class ResolveMapToTargetsTests(unittest.TestCase):
    def test_exact_name_is_bound_and_left_alone(self) -> None:
        m = _parse(PLAN, (_collection(),))
        bound = resolve_map_to_targets(
            m, (PlanTarget(slug="mutual-information", title="互信息"),)
        )
        node = bound.node_for_slug("mutual-information")
        assert node is not None
        self.assertFalse(node.unlisted)

    def test_a_shorter_name_is_bound_to_the_callers_page(self) -> None:
        # The plan says `mutual-information`; the list calls the page
        # `concepts/mutual-information`. The list is what the round refers to.
        m = _parse(PLAN, (_collection(),))
        bound = resolve_map_to_targets(
            m, (PlanTarget(slug="concepts/mutual-information", title="互信息"),)
        )
        self.assertIsNotNone(bound.node_for_slug("concepts/mutual-information"))
        self.assertIsNone(bound.node_for_slug("mutual-information"))

    def test_a_page_the_list_does_not_have_is_kept_and_marked(self) -> None:
        m = _parse(PLAN, (_collection(),))
        bound = resolve_map_to_targets(m, (PlanTarget(slug="mutual-information"),))
        extra = bound.node_for_slug("information-theory-and-other-disciplines")
        assert extra is not None
        self.assertTrue(extra.unlisted)
        self.assertFalse(bound.node_for_slug("mutual-information").unlisted)

    def test_a_name_that_fits_two_pages_is_not_bound(self) -> None:
        # The path is somewhere the caller's list does not have, so the only thing
        # left to go on is a bare name that fits two of their pages. A guess either
        # way would be wrong half the time, so it is left for the reader.
        payload = {
            "primary": {
                "name": "互信息",
                "slug": "mutual-information",
                "wikiPath": "wiki/misc/mutual-information.md",
                "collectionIds": ["c1"],
                "blockIds": ["b1"],
                "evidence": "[b1]",
                "outline": [],
            },
            "alsoInSources": [],
            "edges": [],
            "notes": "",
        }
        targets = (
            PlanTarget(slug="concepts/mutual-information"),
            PlanTarget(slug="entities/mutual-information"),
        )
        m = _parse(payload, (_collection(),))
        self.assertIsNone(target_for_name("mutual-information", *self._index(targets)))
        bound = resolve_map_to_targets(m, targets)
        self.assertTrue(bound.node_for_slug("mutual-information").unlisted)

    def test_edges_follow_a_renamed_page_and_dangling_ones_go(self) -> None:
        payload = dict(PLAN)
        payload["edges"] = [
            *PLAN["edges"],
            {
                "from": "information-theory-and-other-disciplines",
                "to": "nowhere",
                "relation": "cooccurs",
                "evidence": "[b1]",
            },
        ]
        m = _parse(payload, (_collection(),))
        bound = resolve_map_to_targets(
            m, (PlanTarget(slug="concepts/mutual-information"),)
        )
        self.assertEqual(
            [(edge.source, edge.target) for edge in bound.edges],
            [("concepts/mutual-information", "information-theory-and-other-disciplines")],
        )

    def test_the_primary_is_a_page_that_will_be_written(self) -> None:
        m = _parse(PLAN, (_collection(),))
        bound = resolve_map_to_targets(m, (PlanTarget(slug="concepts/mutual-information"),))
        self.assertEqual(bound.primary.slug, "concepts/mutual-information")

    def test_an_unlisted_page_keeps_its_mark_when_read_back(self) -> None:
        # The plan is stored and re-read to compile, so the mark has to survive.
        payload = dict(PLAN)
        payload["alsoInSources"] = [
            {**PLAN["alsoInSources"][0], "unlisted": True},
        ]
        stored = json.loads(json.dumps(payload))
        m = parse_topic_map(json.dumps(stored), intent="", collections=(_collection(),))
        self.assertTrue(m.node_for_slug("information-theory-and-other-disciplines").unlisted)

    def test_a_confirmed_page_is_found_by_the_path_it_carries(self) -> None:
        # What the panel sends back: a slug whose folders were stripped on the way
        # through the plan, plus the page's path. It has to land on the plan's page
        # rather than beside it, or the page would be written twice under two names.
        m = _parse(PLAN, (_collection(),))
        page = confirmed_from_dict(
            {
                "name": "互信息",
                "slug": "mutualinformation",
                "wikiPath": "wiki/concepts/mutual-information.md",
                "action": "write",
                "sectionIds": [],
            },
            m,
        )
        self.assertEqual(page.slug, "mutual-information")
        self.assertEqual(page.wiki_path, "wiki/concepts/mutual-information.md")

    def test_no_targets_leaves_the_plan_as_it_is(self) -> None:
        m = _parse(PLAN, (_collection(),))
        self.assertIs(resolve_map_to_targets(m, ()), m)

    def test_a_page_is_found_by_its_path_when_its_name_lost_its_folders(self) -> None:
        # Normalising a slug strips the folders, so `concepts/joint-entropy` comes
        # back as `conceptsjoint-entropy`. The path is where the real name survives.
        payload = {
            "primary": {
                "name": "联合熵",
                "slug": "conceptsjoint-entropy",
                "wikiPath": "wiki/concepts/joint-entropy.md",
                "collectionIds": ["c1"],
                "blockIds": ["b1"],
                "evidence": "[b1]",
                "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
            },
            "alsoInSources": [],
            "edges": [],
            "notes": "",
        }
        m = _parse(payload, (_collection(),))
        bound = resolve_map_to_targets(
            m, (PlanTarget(slug="concepts/joint-entropy", title="联合熵"),)
        )
        self.assertIsNotNone(bound.node_for_slug("concepts/joint-entropy"))
        self.assertFalse(bound.node_for_slug("concepts/joint-entropy").unlisted)

    @staticmethod
    def _index(targets: tuple[PlanTarget, ...]):
        return target_index(targets)


class MapClipsWithTargetsTests(unittest.TestCase):
    def test_the_answer_is_bound_to_the_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            Path(layout.root / "raw/web").mkdir(parents=True, exist_ok=True)
            (layout.root / "raw/web/sample.html").write_text(
                '<p id="na-b1" data-na-block="b1">互信息定义</p>\n', encoding="utf-8"
            )
            save_clip_store(
                layout, ClipStore(intent="", collections=(_collection(),), topic_map=None)
            )
            mapped = map_clips(
                layout,
                map_text=json.dumps(PLAN),
                targets=(PlanTarget(slug="concepts/mutual-information", title="互信息"),),
            )
            self.assertIsNotNone(mapped.node_for_slug("concepts/mutual-information"))
            self.assertTrue(
                mapped.node_for_slug("information-theory-and-other-disciplines").unlisted
            )

    def test_the_list_reaches_the_prompt(self) -> None:
        doc = SourceDocument(
            project_root=Path("."), identity="x", path=Path("x.md"), content="正文"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            prompt = build_topic_map_prompt(
                doc,
                layout,
                intent="",
                collection_catalog="- c1 互信息",
                targets=(
                    PlanTarget(
                        slug="concepts/mutual-information",
                        title="互信息",
                        action="revise",
                    ),
                ),
            )
        self.assertIn("concepts/mutual-information — 互信息 (revise)", prompt.user)
        self.assertIn("Pages this pass may write", prompt.user)
        self.assertIn("MUST be one of those", prompt.system)

    def test_without_a_list_the_prompt_does_not_claim_one(self) -> None:
        doc = SourceDocument(
            project_root=Path("."), identity="x", path=Path("x.md"), content="正文"
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            prompt = build_topic_map_prompt(
                doc, layout, intent="", collection_catalog="- c1 互信息"
            )
        self.assertNotIn("Pages this pass may write", prompt.user)
        self.assertNotIn("MUST be one of those", prompt.system)


class RoundTargetsTests(unittest.TestCase):
    def _explore_to_plan(self, layout) -> None:
        raw = "raw/web/sample.html"
        path = layout.root / raw
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
                        "action": "revise",
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
                collections=(_collection("round-1", round_id=session.round_id),),
                topic_map=None,
            ),
        )
        act_session(layout, {"action": "enterPlan"}, use_llm=False)

    def test_a_search_that_is_planning_has_a_list_with_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._explore_to_plan(layout)
            targets = round_targets(layout)
            self.assertEqual([item.slug for item in targets], ["concepts/mutual-information"])
            self.assertEqual(targets[0].title, "互信息")
            self.assertEqual(targets[0].action, "revise")

    def test_chew_and_everyday_planning_have_no_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertIsNone(round_targets(layout))
            # Chew needs something left to pick up, so give it a stored page.
            path = layout.root / "raw/web/sample.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<p id="na-b1" data-na-block="b1">互信息定义</p>\n', encoding="utf-8")
            act_session(layout, {"action": "start", "mode": "chew"}, use_llm=False)
            self.assertIsNone(round_targets(layout))


class PlanAgainstOwnListTests(unittest.TestCase):
    """A search's own list is what its plan is checked against."""

    def _round_ready_to_plan(self, layout) -> str:
        raw = "raw/web/sample.html"
        path = layout.root / raw
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
                        "action": "revise",
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
                collections=(_collection("round-1", round_id=session.round_id),),
                topic_map=None,
            ),
        )
        act_session(layout, {"action": "enterPlan"}, use_llm=False)
        return session.round_id

    def test_the_plan_is_kept_and_its_own_pages_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_ready_to_plan(layout)
            payload = run_topic_map_job(layout, map_text=json.dumps(PLAN))
            nodes = {item["slug"]: item for item in payload["candidates"]}
            # Named exactly like the list, and named loosely, both bind to the list.
            self.assertIn("concepts/mutual-information", nodes)
            self.assertFalse(nodes["concepts/mutual-information"]["unlisted"])
            # A page the list could not have known about is kept, and marked.
            extra = nodes["information-theory-and-other-disciplines"]
            self.assertTrue(extra["unlisted"])
            # And it is stored where the round keeps its pages, so the compile step
            # and the panel read the same list.
            stored = load_session(layout).whitelist
            self.assertEqual(
                {item.slug for item in stored},
                {"concepts/mutual-information", "information-theory-and-other-disciplines"},
            )
            # Nothing is written beside it: a search has one list of pages.
            self.assertIsNone(load_clip_store(layout).topic_map)

    def test_a_page_outside_the_list_can_be_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_ready_to_plan(layout)
            payload = run_topic_map_job(layout, map_text=json.dumps(PLAN))
            nodes = {item["slug"]: item for item in payload["candidates"]}
            # Compiling a search reads the list the search itself keeps.
            parsed = proposals_map(layout)
            self.assertIsNotNone(parsed)
            page = confirmed_from_dict(
                {
                    "name": nodes["information-theory-and-other-disciplines"]["title"],
                    "slug": "information-theory-and-other-disciplines",
                    "action": "write",
                    "sectionIds": [],
                },
                parsed,
            )
            self.assertEqual(page.slug, "information-theory-and-other-disciplines")

    def test_a_page_that_is_not_in_the_plan_at_all_is_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self._round_ready_to_plan(layout)
            run_topic_map_job(layout, map_text=json.dumps(PLAN))
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
                    dry_run=True,
                )
            self.assertEqual(caught.exception.code, "compile.notInPlan")


if __name__ == "__main__":
    unittest.main()
