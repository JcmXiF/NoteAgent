from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    GateDecision,
    clip_store_from_dict,
    clip_store_to_dict,
    collection_headline,
    collections_as_source_text,
    format_collection_catalog,
    load_clip_store,
    locators_for_blocks,
    require_durable_sources,
    save_clip_store,
    source_locator,
)
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal


class ClipStoreTests(unittest.TestCase):
    def test_roundtrip_under_meta_not_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            store = ClipStore(
                intent="KL散度",
                collections=(
                    ClipCollection(
                        id="c1",
                        source_key="raw/web/example.com/kl.html",
                        source_label="KL",
                        block_ids=("b1", "b2"),
                        blocks=(
                            DerivedBlock(
                                id="b1",
                                tag="p",
                                text="KL 是相对熵。",
                                preview="KL 是相对熵。",
                            ),
                            DerivedBlock(
                                id="b2",
                                tag="p",
                                text="信息熵出现在定义里。",
                                preview="信息熵出现在定义里。",
                            ),
                        ),
                    ),
                ),
                topic_map=None,
            )
            save_clip_store(layout, store)
            self.assertTrue(layout.clips.is_file())
            self.assertTrue(str(layout.clips).replace("\\", "/").endswith(".noteagent/clips.json"))
            self.assertFalse(
                (layout.raw / "clips.json").exists(),
            )
            loaded = load_clip_store(layout)
            self.assertEqual(loaded.intent, "KL散度")
            self.assertEqual(loaded.collections[0].block_ids, ("b1", "b2"))
            self.assertEqual(loaded.decisions, ())
            text = collections_as_source_text(loaded.collections)
        self.assertIn("[b1]", text)
        self.assertIn("相对熵", text)

    def test_headline_prefers_heading_not_filename(self) -> None:
        collection = ClipCollection(
            id="c1",
            source_key="raw/web/example.com/page.html",
            source_label="page.html",
            block_ids=("b1", "b2", "b3"),
            blocks=(
                DerivedBlock(id="b1", tag="h2", text="编辑", preview="编辑"),
                DerivedBlock(
                    id="b2",
                    tag="h2",
                    text="如何衡量不确定事物的发生？",
                    preview="如何衡量",
                ),
                DerivedBlock(id="b3", tag="p", text="天气例子。", preview="天气"),
            ),
        )
        self.assertEqual(
            collection_headline(collection, max_chars=80),
            "如何衡量不确定事物的发生？",
        )
        catalog = format_collection_catalog((collection,))
        self.assertIn("如何衡量不确定事物的发生？", catalog)
        self.assertNotIn("集合 1 · page.html", catalog)
        text = collections_as_source_text((collection,))
        self.assertIn("如何衡量不确定事物的发生？", text)

    def test_rejects_escape_and_wiki_keys(self) -> None:
        with self.assertRaises(ValueError):
            clip_store_from_dict(
                {
                    "intent": "x",
                    "collections": [
                        {
                            "id": "c1",
                            "sourceKey": "raw/../wiki/secret.md",
                            "sourceLabel": "no",
                            "blockIds": ["b1"],
                            "blocks": [],
                        }
                    ],
                }
            )
        with self.assertRaises(ValueError):
            clip_store_from_dict(
                {
                    "intent": "x",
                    "collections": [
                        {
                            "id": "c1",
                            "sourceKey": "wiki/concepts/kl.md",
                            "blockIds": ["b1"],
                            "blocks": [],
                        }
                    ],
                }
            )

    def test_locators_skip_preview(self) -> None:
        preview = ClipCollection(
            id="c1",
            source_key="preview:https://example.com/kl",
            source_label="preview",
            block_ids=("b1",),
            blocks=(DerivedBlock(id="b1", tag="p", text="hi", preview="hi"),),
        )
        saved = ClipCollection(
            id="c2",
            source_key="raw/web/example.com/kl.html",
            source_label="saved",
            block_ids=("b1",),
            blocks=(DerivedBlock(id="b1", tag="p", text="hi", preview="hi"),),
        )
        self.assertIsNone(source_locator(preview.source_key, "b1"))
        locators = locators_for_blocks((preview, saved), {"b1"})
        self.assertEqual(locators, ["raw/web/example.com/kl.html#b1"])

    def test_require_durable_sources_rejects_preview_and_missing(self) -> None:
        preview = ClipCollection(
            id="c1",
            source_key="preview:https://example.com/kl",
            source_label="preview",
            block_ids=("b1",),
            blocks=(DerivedBlock(id="b1", tag="p", text="hi", preview="hi"),),
        )
        html_key = "raw/web/example.com/kl.html"
        pdf_key = "raw/pdf/example.com/paper.pdf"
        md_key = "raw/web/local/notes.md"
        html_col = ClipCollection(
            id="c2",
            source_key=html_key,
            source_label="html",
            block_ids=("b1",),
            blocks=(DerivedBlock(id="b1", tag="p", text="hi", preview="hi"),),
        )
        pdf_col = ClipCollection(
            id="c3",
            source_key=pdf_key,
            source_label="pdf",
            block_ids=("p1",),
            blocks=(DerivedBlock(id="p1", tag="p", text="page", preview="page"),),
        )
        md_col = ClipCollection(
            id="c4",
            source_key=md_key,
            source_label="md",
            block_ids=("b1",),
            blocks=(DerivedBlock(id="b1", tag="p", text="md", preview="md"),),
        )
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(Refusal) as ctx:
                require_durable_sources(layout, (preview,))
            self.assertEqual(ctx.exception.code, "source.missingOriginal")
            with self.assertRaises(Refusal) as ctx:
                require_durable_sources(layout, (html_col,))
            self.assertEqual(ctx.exception.code, "source.missingOriginal")
            html = layout.root / html_key
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<p>KL</p>\n", encoding="utf-8")
            pdf = layout.root / pdf_key
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(b"%PDF-1.4 dummy")
            md = layout.root / md_key
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text("# notes\n", encoding="utf-8")
            require_durable_sources(layout, (html_col, pdf_col, md_col))

    def test_persists_gate_decisions(self) -> None:
        store = clip_store_from_dict(
            {
                "intent": "KL散度",
                "collections": [
                    {
                        "id": "c1",
                        "sourceKey": "raw/web/example.com/kl.html",
                        "sourceLabel": "KL",
                        "blockIds": ["b1"],
                        "blocks": [
                            {
                                "id": "b1",
                                "tag": "p",
                                "text": "KL",
                                "preview": "KL",
                            }
                        ],
                    }
                ],
                "decisions": [
                    {
                        "slug": "kl散度",
                        "action": "write",
                        "sectionIds": ["s1"],
                    },
                    {"slug": "bad", "action": "explode"},
                ],
            }
        )
        self.assertEqual(
            store.decisions,
            (GateDecision(slug="kl散度", action="write", section_ids=("s1",)),),
        )
        dumped = clip_store_to_dict(store)
        self.assertEqual(dumped["decisions"][0]["action"], "write")

    def test_keeps_board_choices_apart_from_the_gate(self) -> None:
        store = clip_store_from_dict(
            {
                "intent": "KL散度",
                "collections": [
                    {
                        "id": "c1",
                        "sourceKey": "raw/web/example.com/kl.html",
                        "sourceLabel": "KL",
                        "blockIds": ["b1"],
                        "blocks": [
                            {"id": "b1", "tag": "p", "text": "KL", "preview": "KL"}
                        ],
                    }
                ],
                "decisions": [{"slug": "kl", "action": "write", "sectionIds": ["s1"]}],
                "choices": [{"slug": "kl", "action": "skip", "sectionIds": []}],
            }
        )
        self.assertEqual(
            store.decisions, (GateDecision(slug="kl", action="write", section_ids=("s1",)),)
        )
        self.assertEqual(
            store.choices, (GateDecision(slug="kl", action="skip", section_ids=()),)
        )
        dumped = clip_store_to_dict(store)
        self.assertEqual(dumped["decisions"][0]["action"], "write")
        self.assertEqual(dumped["choices"][0]["action"], "skip")


if __name__ == "__main__":
    unittest.main()
