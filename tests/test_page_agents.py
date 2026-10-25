from __future__ import annotations

import unittest

from noteagent.ingest.clips import ClipCollection, DerivedBlock
from noteagent.ingest.ecg import LogicLink
from noteagent.ingest.page_agents import (
    Contribution,
    PageStructureError,
    agent_marker_breaks,
    parse_blueprint,
    stamp_clip_markers,
    write_confirmed_page,
)
from noteagent.ingest.topic_map import PageSection, TopicNode


def _collections() -> tuple[ClipCollection, ClipCollection]:
    blog = ClipCollection(
        id="c-blog",
        source_key="raw/web/blog.example/entropy.html",
        source_label="blog.html",
        block_ids=("b1", "b2"),
        blocks=(
            DerivedBlock(
                id="b1",
                tag="h2",
                text="如何衡量不确定事物的发生？",
                preview="如何衡量",
            ),
            DerivedBlock(id="b2", tag="p", text="天气与 2bit。", preview="天气"),
        ),
    )
    wiki = ClipCollection(
        id="c-wiki",
        source_key="raw/web/wiki.example/entropy.html",
        source_label="wiki.html",
        block_ids=("b1",),
        blocks=(
            DerivedBlock(id="b1", tag="h2", text="定义", preview="定义"),
        ),
    )
    return blog, wiki


class PageAgentTests(unittest.TestCase):
    def test_parse_blueprint_one_collection_per_contribution(self) -> None:
        blog, wiki = _collections()
        parsed = parse_blueprint(
            {
                "title": "信息熵",
                "wikiPath": "wiki/concepts/information-entropy.md",
                "contributions": [
                    {
                        "collectionId": "c-wiki",
                        "role": "definition",
                        "sectionTitle": "定义",
                        "blockIds": ["b1", "b2"],
                        "instruction": "keep definition",
                    },
                    {
                        "collectionId": "missing",
                        "role": "derivation",
                        "sectionTitle": "丢弃",
                        "blockIds": ["b1"],
                    },
                    {
                        "collectionId": "c-blog",
                        "role": "derivation",
                        "sectionTitle": "编码",
                        "blockIds": ["b1", "b2"],
                    },
                ],
            },
            page_name="信息熵",
            wiki_path="wiki/concepts/information-entropy.md",
            collections=(blog, wiki),
        )
        self.assertEqual(len(parsed.contributions), 2)
        self.assertEqual(parsed.contributions[0].collection_id, "c-wiki")
        self.assertEqual(parsed.contributions[0].block_ids, ("b1",))
        self.assertEqual(parsed.contributions[1].collection_id, "c-blog")

    def test_write_confirmed_page_injects_units(self) -> None:
        blog, wiki = _collections()
        node = TopicNode(
            title="信息熵",
            slug="information-entropy",
            wiki_path="wiki/concepts/information-entropy.md",
            relation_to_primary="primary",
            collection_ids=("c-blog", "c-wiki"),
            block_ids=("b1", "b2"),
            evidence="两份集合",
            outline=(
                PageSection(
                    id="s1",
                    title="定义",
                    block_ids=("b1",),
                    collection_id="c-wiki",
                    role="definition",
                ),
                PageSection(
                    id="s2",
                    title="编码",
                    block_ids=("b1", "b2"),
                    collection_id="c-blog",
                    role="derivation",
                ),
            ),
        )
        compose = """---FILE: wiki/concepts/information-entropy.md---
---
type: concept
title: 信息熵
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: []
---

# 信息熵

## 定义

百科定义单元。

## 编码推导

天气与 2bit。
---END FILE---
"""
        block, refs = write_confirmed_page(
            intent="信息熵",
            page_name="信息熵",
            wiki_path="wiki/concepts/information-entropy.md",
            node=node,
            collections=(blog, wiki),
            mention_names=[],
            skip_names=[],
            client=None,
            script={
                "blueprint": {
                    "title": "信息熵",
                    "wikiPath": "wiki/concepts/information-entropy.md",
                    "contributions": [
                        {
                            "collectionId": "c-wiki",
                            "role": "definition",
                            "sectionTitle": "定义",
                            "blockIds": ["b1"],
                        },
                        {
                            "collectionId": "c-blog",
                            "role": "derivation",
                            "sectionTitle": "编码",
                            "blockIds": ["b1", "b2"],
                        },
                    ],
                },
                "drafts": {
                    "c-wiki": "## 定义\n熵是平均信息量。",
                    "c-blog": "## 编码推导\n天气与 2bit。",
                },
                "compose": compose,
            },
        )
        self.assertEqual(block.path, "wiki/concepts/information-entropy.md")
        self.assertIn("百科定义单元", block.content)
        self.assertIn("天气与 2bit", block.content)
        self.assertIn("<!-- na-clip:c-blog -->", block.content)
        self.assertIn("<!-- na-clip:c-wiki -->", block.content)
        self.assertIn(("c-wiki", "b1"), refs)
        self.assertIn(("c-blog", "b1"), refs)

    def test_write_confirmed_page_stamps_missing_typed_link(self) -> None:
        blog, wiki = _collections()
        node = TopicNode(
            title="信息熵",
            slug="information-entropy",
            wiki_path="wiki/concepts/information-entropy.md",
            relation_to_primary="primary",
            collection_ids=("c-blog", "c-wiki"),
            block_ids=("b1", "b2"),
            evidence="两份集合",
            outline=(),
        )
        compose = """---FILE: wiki/concepts/information-entropy.md---
---
type: concept
title: 信息熵
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: []
---

# 信息熵

## 定义

百科定义单元。
---END FILE---
"""
        block, _refs = write_confirmed_page(
            intent="信息熵",
            page_name="信息熵",
            wiki_path="wiki/concepts/information-entropy.md",
            node=node,
            collections=(blog, wiki),
            mention_names=[],
            skip_names=[],
            client=None,
            logic_links=(
                LogicLink(
                    name="KL散度",
                    slug="kl-divergence",
                    wiki_path="wiki/concepts/kl-divergence.md",
                    relation="used_by",
                    action="write",
                    wikilink="concepts/kl-divergence",
                ),
            ),
            script={
                "blueprint": {
                    "title": "信息熵",
                    "wikiPath": "wiki/concepts/information-entropy.md",
                    "contributions": [
                        {
                            "collectionId": "c-wiki",
                            "role": "definition",
                            "sectionTitle": "定义",
                            "blockIds": ["b1"],
                        },
                    ],
                },
                "drafts": {"c-wiki": "## 定义\n熵是平均信息量。"},
                "compose": compose,
            },
        )
        self.assertIn("[[concepts/kl-divergence]]", block.content)
        self.assertIn("<!-- na-ecg -->", block.content)

    def test_stamp_keeps_existing_markers_and_skips_unmatched_titles(self) -> None:
        body = (
            "<!-- na-clip:c-wiki -->\n"
            "## 定义\n\n"
            "百科定义。\n\n"
            "## 编码推导\n\n"
            "天气。\n"
        )
        stamped = stamp_clip_markers(
            body,
            [
                Contribution(
                    collection_id="c-wiki",
                    role="definition",
                    section_title="定义",
                    block_ids=("b1",),
                    instruction="",
                ),
                Contribution(
                    collection_id="c-blog",
                    role="derivation",
                    section_title="编码",
                    block_ids=("b1",),
                    instruction="",
                ),
            ],
        )
        self.assertEqual(stamped.count("<!-- na-clip:c-wiki -->"), 1)
        self.assertIn("<!-- na-clip:c-blog -->", stamped)
        self.assertLess(
            stamped.index("<!-- na-clip:c-blog -->"),
            stamped.index("## 编码推导"),
        )

    def test_write_confirmed_page_refuses_a_contribution_with_no_heading(self) -> None:
        blog, wiki = _collections()
        compose = """---FILE: wiki/concepts/information-entropy.md---
---
type: concept
title: 信息熵
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: []
---

# 信息熵

## 编码推导

天气与 2bit。
---END FILE---
"""
        with self.assertRaises(PageStructureError) as caught:
            write_confirmed_page(
                intent="信息熵",
                page_name="信息熵",
                wiki_path="wiki/concepts/information-entropy.md",
                node=None,
                collections=(blog, wiki),
                mention_names=[],
                skip_names=[],
                client=None,
                script={
                    "blueprint": {
                        "title": "信息熵",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "contributions": [
                            {
                                "collectionId": "c-wiki",
                                "role": "definition",
                                "sectionTitle": "定义",
                                "blockIds": ["b1"],
                            },
                            {
                                "collectionId": "c-blog",
                                "role": "derivation",
                                "sectionTitle": "编码",
                                "blockIds": ["b1", "b2"],
                            },
                        ],
                    },
                    "drafts": {
                        "c-wiki": "## 定义\n熵是平均信息量。",
                        "c-blog": "## 编码推导\n天气与 2bit。",
                    },
                    "compose": compose,
                },
            )
        self.assertIn("c-wiki", str(caught.exception))

    def test_agent_marker_breaks_reads_markers_the_reader_way(self) -> None:
        contribution = Contribution("c-a", "definition", "定义", ("b1",), "")
        stacked = (
            "<!-- na-clip:c-a -->\n"
            "<!-- na-clip:c-b -->\n"
            "## 定义\n\n"
            "正文。\n"
        )
        reasons = agent_marker_breaks(stacked, [contribution])
        self.assertTrue(
            any("do not sit directly above" in item for item in reasons), reasons
        )
        self.assertTrue(any("c-a" in item for item in reasons), reasons)

        bound = "<!-- na-clip:c-a -->\n## 定义\n\n正文。\n"
        self.assertEqual(agent_marker_breaks(bound, [contribution]), [])


if __name__ == "__main__":
    unittest.main()
