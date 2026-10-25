"""One type for a page proposal, and one word for each question.

A page is proposed before anything is read and refined once material is in hand.
Those are two stages of one proposal, so there is one type — and the two questions a
proposal answers are kept apart: whether the page exists (`action`), and how much of
it to write (`coverage`). Neither stands in for the other.
"""

from __future__ import annotations

import json
import unittest

from noteagent.ingest.clips import ClipCollection, DerivedBlock
from noteagent.ingest.deep_search_store import (
    WhitelistPage,
    page_from_dict,
    page_to_dict,
)
from noteagent.ingest.proposal import COVERAGES, PAGE_ACTIONS, PageProposal, PageSection
from noteagent.ingest.topic_map import TopicNode, parse_topic_map


def _collection() -> ClipCollection:
    return ClipCollection(
        id="c1",
        source_key="raw/web/sample.html",
        source_label="sample",
        block_ids=("b1",),
        blocks=(DerivedBlock(id="b1", tag="p", text="正文", preview="正文"),),
    )


class OneTypeTests(unittest.TestCase):
    def test_both_sides_are_the_same_type(self) -> None:
        # Not "two types with the same fields" — the same type, so they cannot drift.
        self.assertIs(WhitelistPage, PageProposal)
        self.assertIs(TopicNode, PageProposal)

    def test_a_proposal_can_answer_both_questions_at_once(self) -> None:
        proposal = PageProposal(
            slug="concepts/entropy",
            title="熵",
            action="revise",
            coverage="write",
        )
        self.assertEqual(proposal.action, "revise")
        self.assertEqual(proposal.coverage, "write")

    def test_the_two_vocabularies_are_distinct_and_named(self) -> None:
        # A page can be new and still only worth a mention, or existing and worth a
        # full page: the questions are independent, so the words are too.
        self.assertEqual(set(PAGE_ACTIONS), {"add", "revise"})
        self.assertEqual(set(COVERAGES), {"write", "mention", "skip"})
        self.assertFalse(set(PAGE_ACTIONS) & set(COVERAGES))

    def test_a_fresh_proposal_writes_nothing_until_a_plan_says_so(self) -> None:
        proposal = PageProposal(slug="concepts/entropy")
        self.assertEqual(proposal.coverage, "skip")
        self.assertTrue(proposal.accepted)


class WireShapeTests(unittest.TestCase):
    """The two containers keep their own JSON, and both hold the same type."""

    def test_a_round_s_proposal_round_trips_through_its_wire(self) -> None:
        proposal = PageProposal(
            slug="concepts/entropy",
            title="熵",
            action="add",
            plan="图里缺这一页",
            query="熵 定义",
            section="定义",
            accepted=False,
        )
        raw = page_to_dict(proposal)
        # One shape for both stages of a proposal: the fields a draft leaves empty
        # are simply empty, and the plan's fields are there when it refines it.
        self.assertEqual(
            set(raw),
            {
                "title",
                "slug",
                "wikiPath",
                "action",
                "coverage",
                "accepted",
                "query",
                "section",
                "plan",
                "evidence",
                "reason",
                "reasonArgs",
                "relationToPrimary",
                "collectionIds",
                "collectionRoles",
                "blockIds",
                "outline",
                "unlisted",
            },
        )
        back = page_from_dict(json.loads(json.dumps(raw)))
        for name in ("slug", "title", "action", "plan", "query", "section", "accepted"):
            self.assertEqual(getattr(back, name), getattr(proposal, name), name)

    def test_a_plan_s_proposal_round_trips_through_its_wire(self) -> None:
        payload = {
            "primary": {
                "name": "熵",
                "slug": "concepts/entropy",
                "wikiPath": "wiki/concepts/entropy.md",
                "collectionIds": ["c1"],
                "blockIds": ["b1"],
                "evidence": "[b1] 给出定义",
                "outline": [
                    {"id": "s1", "title": "定义", "collectionId": "c1", "blockIds": ["b1"]}
                ],
            },
            "alsoInSources": [],
            "edges": [],
            "notes": "",
        }
        parsed = parse_topic_map(
            json.dumps(payload), intent="", collections=(_collection(),)
        )
        node = parsed.primary
        self.assertEqual(node.title, "熵")  # the plan's `name` is a page's title
        self.assertEqual(node.slug, "concepts/entropy")
        self.assertEqual(node.outline[0].title, "定义")
        # One spelling goes out, and a model's `name` is still understood coming in.
        stored = parsed.to_dict()
        self.assertEqual(stored["primary"]["title"], "熵")
        reread = parse_topic_map(
            json.dumps({**payload, "primary": {**payload["primary"], "title": "信息熵"}}),
            intent="",
            collections=(_collection(),),
        )
        self.assertEqual(reread.primary.title, "信息熵")

    def test_the_two_containers_can_hold_the_same_kind_of_thing(self) -> None:
        # A proposal that starts in a round's list can be carried into a plan's
        # candidates: same type, so no conversion and nothing lost on the way.
        from_round = WhitelistPage(
            slug="concepts/entropy", title="熵", action="revise", query="熵"
        )
        as_plan_node = TopicNode(
            slug=from_round.slug,
            title=from_round.title,
            action=from_round.action,
            query=from_round.query,
            coverage="write",
            wiki_path="wiki/concepts/entropy.md",
        )
        self.assertEqual(as_plan_node.slug, from_round.slug)
        self.assertEqual(as_plan_node.title, from_round.title)
        self.assertEqual(as_plan_node.action, "revise")
        self.assertEqual(as_plan_node.coverage, "write")

    def test_a_section_is_part_of_a_proposal_wherever_it_lives(self) -> None:
        node = TopicNode(
            slug="concepts/entropy",
            title="熵",
            outline=(PageSection(id="s1", title="定义", block_ids=("b1",)),),
        )
        self.assertIsInstance(node.outline[0], PageSection)


if __name__ == "__main__":
    unittest.main()
