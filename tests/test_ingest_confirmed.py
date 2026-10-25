from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    save_clip_store,
)
from noteagent.ingest.confirmed import apply_block_sources, ingest_confirmed
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.ingest.topic_map import parse_topic_map
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled
from noteagent.util.refusal import Refusal

MAP = {
    "primary": {
        "name": "KL散度",
        "slug": "kl-divergence",
        "wikiPath": "wiki/concepts/kl-divergence.md",
        "collectionIds": ["c1"],
        "blockIds": ["b1"],
        "evidence": "[b1]",
        "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
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
            "outline": [{"id": "s1", "title": "定义", "blockIds": ["b2"]}],
        }
    ],
}

GENERATION = """---FILE: wiki/concepts/kl-divergence.md---
---
type: concept
title: KL散度
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: ["raw/web/example.com/kl.html"]
---

# KL散度

相对熵。
---END FILE---

---FILE: wiki/concepts/information-entropy.md---
---
type: concept
title: 信息熵
created: 2026-09-04
updated: 2026-09-04
tags: [info]
related: []
sources: ["raw/web/example.com/kl.html"]
---

# 信息熵

不该被写下的预习页。
---END FILE---
"""


class IngestConfirmedTests(unittest.TestCase):
    def test_writes_only_confirmed_and_block_locators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p id='na-b1'>KL</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="KL散度",
                collections=(
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
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            topic_map = parse_topic_map(MAP, intent="KL散度", collections=store.collections)
            result = ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "action": "write",
                        "sectionIds": ["s1"],
                    },
                    {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "action": "skip",
                        "sectionIds": [],
                    },
                ],
                topic_map=topic_map,
                generation_text=GENERATION,
            )
            written = {item.relative_path.replace("\\", "/") for item in result.write.written}
            self.assertIn("wiki/concepts/kl-divergence.md", written)
            self.assertNotIn("wiki/concepts/information-entropy.md", written)
            self.assertTrue(
                any(
                    path.startswith("wiki/sources/") and path.endswith("-clips.md")
                    for path in written
                )
            )
            page = (layout.wiki / "concepts" / "kl-divergence.md").read_text(encoding="utf-8")
            sources = parse_inline_list(parse_page(page).fields.get("sources", "[]"))
            self.assertIn("raw/web/example.com/kl.html#b1", sources)
            self.assertFalse((layout.wiki / "concepts" / "information-entropy.md").exists())
            self.assertTrue(
                any("unconfirmed FILE" in warning for warning in result.parse.warnings)
            )

    def test_empty_intent_falls_back_to_primary_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p id='na-b1'>KL</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="",
                collections=(
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
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            result = ingest_confirmed(
                layout,
                intent="",
                confirmed=[
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "action": "write",
                        "sectionIds": ["s1"],
                    }
                ],
                generation_text=GENERATION,
            )
            written = {item.relative_path.replace("\\", "/") for item in result.write.written}
            self.assertIn("wiki/concepts/kl-divergence.md", written)

    def test_hierarchical_scripts_write_confirmed_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            blog_html = layout.raw / "web" / "blog.example" / "entropy.html"
            wiki_html = layout.raw / "web" / "wiki.example" / "entropy.html"
            blog_html.parent.mkdir(parents=True, exist_ok=True)
            wiki_html.parent.mkdir(parents=True, exist_ok=True)
            blog_html.write_text("<p id='na-b1'>blog</p>\n", encoding="utf-8")
            wiki_html.write_text("<p id='na-b1'>wiki</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="信息熵",
                collections=(
                    ClipCollection(
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
                            DerivedBlock(
                                id="b2",
                                tag="p",
                                text="天气与 2bit。",
                                preview="天气",
                            ),
                        ),
                    ),
                    ClipCollection(
                        id="c-wiki",
                        source_key="raw/web/wiki.example/entropy.html",
                        source_label="wiki.html",
                        block_ids=("b1",),
                        blocks=(
                            DerivedBlock(id="b1", tag="h2", text="定义", preview="定义"),
                        ),
                    ),
                ),
                topic_map=None,
            )
            save_clip_store(layout, store)
            topic_map = parse_topic_map(
                {
                    "primary": {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "collectionIds": ["c-blog", "c-wiki"],
                        "outline": [
                            {
                                "id": "s1",
                                "title": "定义",
                                "collectionId": "c-wiki",
                                "blockIds": ["b1"],
                            },
                            {
                                "id": "s2",
                                "title": "编码",
                                "collectionId": "c-blog",
                                "blockIds": ["b1", "b2"],
                            },
                        ],
                    },
                    "alsoInSources": [
                        {
                            "name": "KL散度",
                            "slug": "kl-divergence",
                            "wikiPath": "wiki/concepts/kl-divergence.md",
                            "relationToPrimary": "used_by",
                            "collectionIds": ["c-blog"],
                            "blockIds": ["b2"],
                        }
                    ],
                },
                intent="信息熵",
                collections=store.collections,
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

---FILE: wiki/concepts/kl-divergence.md---
---
type: concept
title: KL散度
created: 2026-09-04
updated: 2026-09-04
tags: []
related: []
sources: []
---

# KL散度

不该出现。
---END FILE---
"""
            result = ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "action": "write",
                        "sectionIds": ["s1", "s2"],
                    },
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "action": "skip",
                        "sectionIds": [],
                    },
                ],
                topic_map=topic_map,
                agent_scripts={
                    "wiki/concepts/information-entropy.md": {
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
                    }
                },
            )
            written = {
                item.relative_path.replace("\\", "/") for item in result.write.written
            }
            self.assertIn("wiki/concepts/information-entropy.md", written)
            self.assertNotIn("wiki/concepts/kl-divergence.md", written)
            page = (layout.wiki / "concepts" / "information-entropy.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("百科定义单元", page)
            self.assertIn("天气与 2bit", page)
            self.assertNotIn("不该出现", page)
            sources = parse_inline_list(parse_page(page).fields.get("sources", "[]"))
            self.assertIn("raw/web/wiki.example/entropy.html#b1", sources)
            self.assertIn("raw/web/blog.example/entropy.html#b1", sources)
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_hierarchical_scripts_skip_a_page_whose_marker_misses_a_heading(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            wiki_html = layout.raw / "web" / "wiki.example" / "entropy.html"
            wiki_html.parent.mkdir(parents=True, exist_ok=True)
            wiki_html.write_text("<p id='na-b1'>wiki</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="信息熵",
                collections=(
                    ClipCollection(
                        id="c-wiki",
                        source_key="raw/web/wiki.example/entropy.html",
                        source_label="wiki.html",
                        block_ids=("b1",),
                        blocks=(
                            DerivedBlock(id="b1", tag="h2", text="定义", preview="定义"),
                        ),
                    ),
                ),
                topic_map=None,
            )
            save_clip_store(layout, store)
            topic_map = parse_topic_map(
                {
                    "primary": {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "collectionIds": ["c-wiki"],
                        "outline": [
                            {
                                "id": "s1",
                                "title": "定义",
                                "collectionId": "c-wiki",
                                "blockIds": ["b1"],
                            }
                        ],
                    }
                },
                intent="信息熵",
                collections=store.collections,
            )
            # The composed page drops the unit's heading, so the unit's marker can
            # never reach one. The page is skipped instead of written unmarked.
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

开门见山。
---END FILE---
"""
            result = ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "wikiPath": "wiki/concepts/information-entropy.md",
                        "action": "write",
                        "sectionIds": ["s1"],
                    }
                ],
                topic_map=topic_map,
                agent_scripts={
                    "wiki/concepts/information-entropy.md": {
                        "blueprint": {
                            "title": "信息熵",
                            "wikiPath": "wiki/concepts/information-entropy.md",
                            "contributions": [
                                {
                                    "collectionId": "c-wiki",
                                    "role": "definition",
                                    "sectionTitle": "定义",
                                    "blockIds": ["b1"],
                                }
                            ],
                        },
                        "drafts": {"c-wiki": "## 定义\n熵是平均信息量。"},
                        "compose": compose,
                    }
                },
            )
            skips = [
                warning
                for warning in result.write.warnings
                if warning.code == "skipped"
                and warning.path == "wiki/concepts/information-entropy.md"
            ]
            self.assertTrue(skips, result.write.warnings)
            self.assertFalse(
                (layout.wiki / "concepts" / "information-entropy.md").exists()
            )

    def test_preview_collections_cannot_compile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            store = ClipStore(
                intent="KL散度",
                collections=(
                    ClipCollection(
                        id="c1",
                        source_key="preview:https://example.com/kl",
                        source_label="KL",
                        block_ids=("b1",),
                        blocks=(
                            DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                        ),
                    ),
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            topic_map = parse_topic_map(MAP, intent="KL散度", collections=store.collections)
            with self.assertRaises(Refusal) as ctx:
                ingest_confirmed(
                    layout,
                    confirmed=[
                        {
                            "name": "KL散度",
                            "slug": "kl-divergence",
                            "wikiPath": "wiki/concepts/kl-divergence.md",
                            "action": "write",
                            "sectionIds": ["s1"],
                        }
                    ],
                    topic_map=topic_map,
                    generation_text=GENERATION,
                )
            self.assertEqual(ctx.exception.code, "source.missingOriginal")
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_attaches_doctor_and_skips_on_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p id='na-b1'>KL</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="KL散度",
                collections=(
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
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            confirmed = [
                {
                    "name": "KL散度",
                    "slug": "kl-divergence",
                    "wikiPath": "wiki/concepts/kl-divergence.md",
                    "action": "write",
                    "sectionIds": ["s1"],
                }
            ]
            written = ingest_confirmed(
                layout,
                confirmed=confirmed,
                topic_map=MAP,
                generation_text=GENERATION,
            )
            self.assertIsNotNone(written.doctor)
            dry = ingest_confirmed(
                layout,
                confirmed=confirmed,
                topic_map=MAP,
                generation_text=GENERATION,
                dry_run=True,
            )
            self.assertIsNone(dry.doctor)

    def test_unknown_session_clip_is_parse_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p id='na-b1'>KL</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="KL散度",
                collections=(
                    ClipCollection(
                        id="c1",
                        source_key="raw/web/example.com/kl.html",
                        source_label="KL",
                        block_ids=("b1",),
                        blocks=(
                            DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                        ),
                    ),
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            generation = GENERATION.replace(
                "相对熵。",
                "相对熵。\n\n<!-- na-clip:other -->\n",
            )
            result = ingest_confirmed(
                layout,
                confirmed=[
                    {
                        "name": "KL散度",
                        "slug": "kl-divergence",
                        "wikiPath": "wiki/concepts/kl-divergence.md",
                        "action": "write",
                        "sectionIds": ["s1"],
                    }
                ],
                topic_map=MAP,
                generation_text=generation,
            )
            self.assertTrue(
                any("not in the current clip session" in item for item in result.parse.warnings)
            )

    def test_cancel_before_write_does_not_write_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            html = layout.raw / "web" / "example.com" / "kl.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p id='na-b1'>KL</p>\n", encoding="utf-8")
            store = ClipStore(
                intent="KL散度",
                collections=(
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
                ),
                topic_map=MAP,
            )
            save_clip_store(layout, store)
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                ingest_confirmed(
                    layout,
                    confirmed=[
                        {
                            "name": "KL散度",
                            "slug": "kl-divergence",
                            "wikiPath": "wiki/concepts/kl-divergence.md",
                            "action": "write",
                            "sectionIds": ["s1"],
                        }
                    ],
                    generation_text=GENERATION,
                    cancel=cancel,
                )
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_apply_block_sources_drops_preview_locators(self) -> None:
        page = apply_block_sources(
            "---\n"
            "type: concept\n"
            "title: KL\n"
            'sources: ["preview:https://example.com/kl#b1", '
            '"raw/web/old.html#b2"]\n'
            "---\n\n正文\n",
            ["raw/web/new.html#b3"],
        )
        sources = parse_inline_list(parse_page(page).fields.get("sources", "[]"))
        self.assertEqual(
            sources,
            [
                "raw/web/old.html#b2",
                "raw/web/new.html#b3",
            ],
        )


if __name__ == "__main__":
    unittest.main()
