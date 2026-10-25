from __future__ import annotations

import unittest

from noteagent.ingest.clips import ClipCollection, DerivedBlock
from noteagent.ingest.ecg import (
    ECG_MARK,
    LogicLink,
    _ecg_section,
    _strip_ecg_block,
    format_logic_links_for_prompt,
    logic_links_for_page,
    stamp_logic_wikilinks,
    wikilink_target,
)
from noteagent.ingest.topic_map import parse_topic_map


def _collections() -> tuple[ClipCollection, ...]:
    return (
        ClipCollection(
            id="c1",
            source_key="raw/web/example.com/kl.html",
            source_label="KL",
            block_ids=("b1", "b2"),
            blocks=(
                DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                DerivedBlock(id="b2", tag="p", text="信息熵。", preview="熵"),
            ),
        ),
    )


def _topic_map():
    return parse_topic_map(
        {
            "primary": {
                "name": "KL散度",
                "slug": "kl-divergence",
                "wikiPath": "wiki/concepts/kl-divergence.md",
                "collectionIds": ["c1"],
                "blockIds": ["b1"],
                "evidence": "[b1]",
            },
            "alsoInSources": [
                {
                    "name": "信息熵",
                    "slug": "information-entropy",
                    "wikiPath": "wiki/concepts/information-entropy.md",
                    "relationToPrimary": "prerequisite",
                    "collectionIds": ["c1"],
                    "blockIds": ["b2"],
                    "evidence": "[b2]",
                },
                {
                    "name": "互信息",
                    "slug": "mutual-information",
                    "wikiPath": "wiki/concepts/mutual-information.md",
                    "relationToPrimary": "used_by",
                    "collectionIds": ["c1"],
                    "blockIds": ["b2"],
                    "evidence": "[b2]",
                },
            ],
        },
        intent="KL散度",
        collections=_collections(),
    )


class EcgTests(unittest.TestCase):
    def test_wikilink_target_from_wiki_path(self) -> None:
        self.assertEqual(
            wikilink_target("wiki/concepts/kl-divergence.md", "kl-divergence"),
            "concepts/kl-divergence",
        )

    def test_primary_sees_star_neighbors(self) -> None:
        topic_map = _topic_map()
        links = logic_links_for_page(
            topic_map,
            "kl-divergence",
            {
                "kl-divergence": "write",
                "information-entropy": "write",
                "mutual-information": "mention",
            },
        )
        by_slug = {item.slug: item for item in links}
        self.assertEqual(by_slug["information-entropy"].relation, "prerequisite")
        self.assertEqual(by_slug["information-entropy"].action, "write")
        self.assertEqual(by_slug["mutual-information"].relation, "used_by")
        self.assertEqual(by_slug["mutual-information"].action, "mention")
        self.assertNotIn("kl-divergence", by_slug)

    def test_non_primary_only_links_primary_inverted(self) -> None:
        topic_map = _topic_map()
        links = logic_links_for_page(
            topic_map,
            "information-entropy",
            {"kl-divergence": "write", "information-entropy": "write"},
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].slug, "kl-divergence")
        self.assertEqual(links[0].relation, "used_by")
        self.assertEqual(links[0].wikilink, "concepts/kl-divergence")

    def test_pairwise_edges_reach_beyond_primary(self) -> None:
        topic_map = parse_topic_map(
            {
                "candidates": [
                    {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "coverage": "write",
                        "relationToPrimary": "primary",
                        "collectionIds": ["c1"],
                        "blockIds": ["b2"],
                    },
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "coverage": "write",
                        "collectionIds": ["c1"],
                        "blockIds": ["b1"],
                    },
                    {
                        "name": "互信息",
                        "slug": "mutual-information",
                        "wikiPath": "wiki/concepts/mutual-information.md",
                        "coverage": "mention",
                        "collectionIds": ["c1"],
                        "blockIds": ["b2"],
                    },
                ],
                "edges": [
                    {
                        "from": "information-entropy",
                        "to": "kl-divergence",
                        "relation": "prerequisite",
                        "evidence": "[b2]",
                    },
                    {
                        "from": "information-entropy",
                        "to": "mutual-information",
                        "relation": "contrast",
                        "evidence": "[b2]",
                    },
                ],
            },
            intent="信息论",
            collections=_collections(),
        )
        from_entropy = {
            item.slug: item.relation
            for item in logic_links_for_page(
                topic_map,
                "information-entropy",
                {
                    "information-entropy": "write",
                    "kl-divergence": "write",
                    "mutual-information": "mention",
                },
            )
        }
        self.assertEqual(from_entropy["kl-divergence"], "used_by")
        self.assertEqual(from_entropy["mutual-information"], "contrast")
        from_mi = {
            item.slug: item.relation
            for item in logic_links_for_page(
                topic_map,
                "mutual-information",
                {
                    "information-entropy": "write",
                    "kl-divergence": "write",
                    "mutual-information": "mention",
                },
            )
        }
        self.assertEqual(from_mi, {"information-entropy": "contrast"})

    def test_prompt_lists_skip_but_stamp_skips_it(self) -> None:
        topic_map = _topic_map()
        links = logic_links_for_page(
            topic_map,
            "kl-divergence",
            {"information-entropy": "skip"},
        )
        text = format_logic_links_for_prompt(links)
        self.assertIn("information-entropy", text)
        self.assertIn("no obligatory link", text)
        page = (
            "---\n"
            "type: concept\n"
            "title: KL散度\n"
            "related: []\n"
            "sources: []\n"
            "---\n\n"
            "# KL散度\n\n"
            "相对熵。\n"
        )
        stamped = stamp_logic_wikilinks(page, links)
        self.assertNotIn("[[concepts/information-entropy]]", stamped)
        self.assertNotIn(ECG_MARK, stamped)

    def test_stamp_appends_missing_write_and_mention(self) -> None:
        links = (
            LogicLink(
                name="信息熵",
                slug="information-entropy",
                wiki_path="wiki/concepts/information-entropy.md",
                relation="prerequisite",
                action="write",
                wikilink="concepts/information-entropy",
            ),
            LogicLink(
                name="互信息",
                slug="mutual-information",
                wiki_path="wiki/concepts/mutual-information.md",
                relation="used_by",
                action="mention",
                wikilink="concepts/mutual-information",
            ),
        )
        page = (
            "---\n"
            "type: concept\n"
            "title: KL散度\n"
            "related: []\n"
            "sources: []\n"
            "---\n\n"
            "# KL散度\n\n"
            "相对熵。\n"
        )
        stamped = stamp_logic_wikilinks(page, links)
        self.assertIn("[[concepts/information-entropy]]", stamped)
        self.assertIn("[[concepts/mutual-information]]", stamped)
        self.assertIn(ECG_MARK, stamped)
        again = stamp_logic_wikilinks(stamped, links)
        self.assertEqual(again, stamped)
        self.assertEqual(again.count("[[concepts/information-entropy]]"), 1)

    def test_writer_output_comes_off_whole(self) -> None:
        section = _ecg_section(
            (
                LogicLink(
                    name="Bellman Equation",
                    slug="bellman-equation",
                    wiki_path="wiki/concepts/bellman-equation.md",
                    relation="prerequisite",
                    action="write",
                    wikilink="concepts/bellman-equation",
                ),
            )
        )
        stripped = _strip_ecg_block(f"before\n\n{section}\nafter\n")
        self.assertEqual(stripped, "before\n\nafter\n")
        self.assertNotIn(ECG_MARK, stripped)
        self.assertNotIn("- ", stripped)

    def test_strip_accepts_a_loose_list(self) -> None:
        section = (
            f"{ECG_MARK}\n"
            "## 相关\n"
            "\n"
            "- 前置（本轮将写）：[[concepts/a]] A\n"
            "\n"
            "- 后置（仅提及）：[[concepts/b]] B\n"
        )
        self.assertEqual(_strip_ecg_block(section), "")
        self.assertEqual(_strip_ecg_block(f"body\n\n{section}").rstrip(), "body")

    def test_strip_takes_a_marker_that_has_no_heading(self) -> None:
        stripped = _strip_ecg_block("body\n\n<!-- na-ecg -->\n")
        self.assertNotIn(ECG_MARK, stripped)
        self.assertEqual(stripped.rstrip(), "body")

    def test_strip_leaves_text_the_marker_did_not_own(self) -> None:
        body = "<!-- na-ecg -->\n## 我的笔记\n\n这段是人写的。\n"
        stripped = _strip_ecg_block(body)
        self.assertNotIn(ECG_MARK, stripped)
        self.assertIn("## 我的笔记", stripped)
        self.assertIn("这段是人写的。", stripped)

    def test_stamp_noop_when_body_already_links(self) -> None:
        link = LogicLink(
            name="信息熵",
            slug="information-entropy",
            wiki_path="wiki/concepts/information-entropy.md",
            relation="prerequisite",
            action="write",
            wikilink="concepts/information-entropy",
        )
        page = (
            "---\n"
            "type: concept\n"
            "title: KL散度\n"
            "related: []\n"
            "sources: []\n"
            "---\n\n"
            "# KL散度\n\n"
            "先看 [[concepts/information-entropy]]。\n"
        )
        stamped = stamp_logic_wikilinks(page, (link,))
        self.assertNotIn(ECG_MARK, stamped)
        self.assertEqual(stamped.count("[[concepts/information-entropy]]"), 1)


if __name__ == "__main__":
    unittest.main()
