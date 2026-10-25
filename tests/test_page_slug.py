"""Page identity, in one rule.

A page's identity keeps the folders it lives in, because that is what the wiki's own
ids are made of and what everything downstream compares against. A *name* is a
different thing: a file name cannot hold folders, and a title that contains one
should not become one. The two rules are separate on purpose, and both are written
down here rather than left to whatever a caller happened to need.
"""

from __future__ import annotations

import json
import unittest

from noteagent.ingest.clips import ClipCollection, DerivedBlock
from noteagent.ingest.topic_map import (
    PlanTarget,
    target_index,
    page_slug,
    page_slug_from_path,
    parse_topic_map,
    resolve_map_to_targets,
    target_for_name,
    topic_slug,
    wiki_path_for_slug,
)


def _collection() -> ClipCollection:
    return ClipCollection(
        id="c1",
        source_key="raw/web/sample.html",
        source_label="sample",
        block_ids=("b1",),
        blocks=(DerivedBlock(id="b1", tag="p", text="正文", preview="正文"),),
    )


class PageSlugTests(unittest.TestCase):
    def test_folders_are_part_of_a_page(self) -> None:
        # A slash is part of the identity: stripped, `concepts/joint-entropy` would
        # become `conceptsjoint-entropy` and never equal the wiki's own id.
        self.assertEqual(page_slug("concepts/joint-entropy"), "concepts/joint-entropy")
        self.assertEqual(page_slug("entities/alpha"), "entities/alpha")

    def test_every_way_of_writing_a_page_gives_one_identity(self) -> None:
        for written in (
            "wiki/concepts/Joint Entropy.md",
            "concepts/joint-entropy.md",
            "concepts/joint-entropy",
            "/concepts/joint entropy/",
            "wiki\\concepts\\joint-entropy.md",
            "  concepts/joint-entropy  ",
        ):
            self.assertEqual(page_slug(written), "concepts/joint-entropy", written)

    def test_a_name_without_folders_stays_without_them(self) -> None:
        self.assertEqual(page_slug("Joint Entropy"), "joint-entropy")
        self.assertEqual(page_slug("信息熵"), "信息熵")

    def test_it_is_the_same_however_often_it_is_applied(self) -> None:
        once = page_slug("wiki/concepts/信息熵.md")
        self.assertEqual(page_slug(once), once)

    def test_nothing_at_all_is_a_placeholder_not_an_empty_page(self) -> None:
        self.assertEqual(page_slug("   "), "topic")
        self.assertEqual(page_slug_from_path(""), "")
        self.assertEqual(page_slug_from_path("raw/x.md"), "")

    def test_a_name_is_flattened_because_a_name_has_no_folders(self) -> None:
        # Kept separate from `page_slug` on purpose: file names and titles want this.
        self.assertEqual(topic_slug("concepts/joint-entropy"), "conceptsjoint-entropy")
        self.assertEqual(topic_slug("信息熵"), "信息熵")
        self.assertEqual(topic_slug(""), "topic")

    def test_a_page_is_looked_up_by_its_path_as_well_as_its_name(self) -> None:
        self.assertEqual(page_slug_from_path("wiki/concepts/entropy.md"), "concepts/entropy")
        self.assertEqual(page_slug_from_path("wiki/sources/什么-clips.md"), "sources/什么-clips")

    def test_a_default_path_does_not_nest_a_page_twice(self) -> None:
        # A new note is flat; an identity already naming a folder keeps it, so an
        # unmigrated page still resolves to the file it came from.
        self.assertEqual(
            wiki_path_for_slug("concepts/joint-entropy"),
            "wiki/concepts/joint-entropy.md",
        )
        self.assertEqual(wiki_path_for_slug("entities/alpha"), "wiki/entities/alpha.md")
        self.assertEqual(wiki_path_for_slug("entropy"), "wiki/entropy.md")


class NestedSlugThroughThePlanTests(unittest.TestCase):
    def _plan(self, slug: str, wiki_path: str = "") -> dict:
        return {
            "primary": {
                "name": "联合熵",
                "slug": slug,
                "wikiPath": wiki_path,
                "collectionIds": ["c1"],
                "blockIds": ["b1"],
                "evidence": "[b1]",
                "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
            },
            "alsoInSources": [],
            "edges": [],
            "notes": "",
        }

    def test_a_plan_keeps_the_folders_it_was_given(self) -> None:
        m = parse_topic_map(
            json.dumps(self._plan("concepts/joint-entropy")),
            intent="",
            collections=(_collection(),),
        )
        node = m.primary
        self.assertEqual(node.slug, "concepts/joint-entropy")
        self.assertEqual(node.wiki_path, "wiki/concepts/joint-entropy.md")

    def test_a_page_in_another_folder_does_not_land_under_concepts(self) -> None:
        m = parse_topic_map(
            json.dumps(self._plan("entities/alpha")),
            intent="",
            collections=(_collection(),),
        )
        self.assertEqual(m.primary.wiki_path, "wiki/entities/alpha.md")

    def test_the_list_binds_without_needing_the_path(self) -> None:
        # The page is named exactly as the list names it, and the path says something
        # else: exact names win, so this is bound by the name alone.
        m = parse_topic_map(
            json.dumps(self._plan("concepts/joint-entropy", "wiki/misc/elsewhere.md")),
            intent="",
            collections=(_collection(),),
        )
        bound = resolve_map_to_targets(
            m, (PlanTarget(slug="concepts/joint-entropy", title="联合熵"),)
        )
        self.assertFalse(bound.primary.unlisted)
        self.assertEqual(bound.primary.wiki_path, "wiki/misc/elsewhere.md")


class TargetMatchingTests(unittest.TestCase):
    def test_a_page_is_named_by_its_title_too(self) -> None:
        # A model asked what to write about answers with the name a reader knows.
        index = target_index((PlanTarget(slug="concepts/joint-entropy", title="联合熵"),))
        self.assertEqual(target_for_name("联合熵", *index).slug, "concepts/joint-entropy")

    def test_a_title_that_fits_two_pages_is_not_bound(self) -> None:
        index = target_index(
            (
                PlanTarget(slug="concepts/entropy", title="熵"),
                PlanTarget(slug="entities/entropy", title="熵"),
            )
        )
        self.assertIsNone(target_for_name("熵", *index))

    def test_an_identity_that_fits_two_pages_is_not_bound(self) -> None:
        index = target_index(
            (
                PlanTarget(slug="concepts/entropy", title="概念里的熵"),
                PlanTarget(slug="entities/entropy", title="实体里的熵"),
            )
        )
        self.assertIsNone(target_for_name("entropy", *index))
        self.assertEqual(
            target_for_name("concepts/entropy", *index).slug, "concepts/entropy"
        )


if __name__ == "__main__":
    unittest.main()
