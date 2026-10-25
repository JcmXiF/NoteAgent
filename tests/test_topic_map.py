from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.api.topic_gate import run_topic_map_job
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    GateDecision,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.confirmed import confirmed_from_dict
from noteagent.ingest.topic_map import (
    carry_over_choices,
    extract_json_object,
    map_clips,
    merge_plan_board,
    parse_topic_map,
)
from noteagent.project.init import init_project
from noteagent.util.cancel import JobCancelled
from noteagent.util.refusal import Refusal

MAP_JSON = """
```json
{
  "primary": {
    "name": "KL散度",
    "slug": "kl-divergence",
    "wikiPath": "wiki/concepts/kl-divergence.md",
    "collectionIds": ["c1"],
    "blockIds": ["b1"],
    "evidence": "集合1的 [b1] 给出相对熵定义。",
    "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}]
  },
  "alsoInSources": [
    {
      "name": "信息熵",
      "slug": "information-entropy",
      "wikiPath": "wiki/concepts/information-entropy.md",
      "relationToPrimary": "prerequisite",
      "collectionIds": ["c1"],
      "blockIds": ["b2"],
      "evidence": "[b2] 在定义 KL 之前先写熵。",
      "outline": [{"id": "s1", "title": "定义", "blockIds": ["b2"]}]
    }
  ],
  "notes": "不要把熵自动写成页。"
}
```
"""


def _store() -> ClipStore:
    return ClipStore(
        intent="KL散度",
        collections=(
            ClipCollection(
                id="c1",
                source_key="raw/web/example.com/kl.html",
                source_label="KL",
                block_ids=("b1", "b2"),
                blocks=(
                    DerivedBlock(id="b1", tag="p", text="KL 是相对熵。", preview="KL"),
                    DerivedBlock(id="b2", tag="p", text="先有信息熵。", preview="熵"),
                ),
            ),
        ),
        topic_map=None,
    )


def _seed_raw(layout, relative: str = "raw/web/example.com/kl.html") -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<p>KL</p>\n", encoding="utf-8")


OTHER_SOURCE = "raw/web/example.com/other.html"

#: A board as it stands after one pass over everything: one page from each source.
BOARD_JSON = {
    "intent": "KL散度",
    "candidates": [
        {
            "name": "KL散度",
            "slug": "kl-divergence",
            "wikiPath": "wiki/concepts/kl-divergence.md",
            "collectionIds": ["c1"],
            "blockIds": ["b1"],
            "coverage": "write",
            "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
        },
        {
            "name": "另一页",
            "slug": "other-page",
            "wikiPath": "wiki/concepts/other-page.md",
            "collectionIds": ["c2"],
            "blockIds": ["b3"],
            "coverage": "write",
            "outline": [{"id": "s9", "title": "别的节", "blockIds": ["b3"]}],
        },
    ],
}


def _store_with_other() -> ClipStore:
    """The everyday store with a second source selected alongside the first."""
    base = _store()
    other = ClipCollection(
        id="c2",
        source_key=OTHER_SOURCE,
        source_label="other",
        block_ids=("b3",),
        blocks=(DerivedBlock(id="b3", tag="p", text="别的源。", preview="别"),),
    )
    return ClipStore(
        intent=base.intent,
        collections=(*base.collections, other),
        topic_map=BOARD_JSON,
    )


class TopicMapTests(unittest.TestCase):
    def test_parse_strips_fence_and_unknown_ids(self) -> None:
        store = _store()
        parsed = parse_topic_map(
            MAP_JSON,
            intent="KL散度",
            collections=store.collections,
        )
        self.assertEqual(parsed.primary.title, "KL散度")
        self.assertEqual(parsed.primary.block_ids, ("b1",))
        self.assertEqual(parsed.also_in_sources[0].relation_to_primary, "prerequisite")
        self.assertEqual(parsed.primary.outline[0].collection_id, "c1")
        self.assertEqual(parsed.collection_summaries[0].id, "c1")
        self.assertIn("不要把熵", parsed.notes)
        unknown = parse_topic_map(
            {
                "primary": {
                    "name": "KL散度",
                    "collectionIds": ["nope"],
                    "blockIds": ["b9"],
                },
                "alsoInSources": [
                    {"name": "噪声", "relationToPrimary": "score", "blockIds": ["b1"]}
                ],
            },
            intent="KL散度",
            collections=store.collections,
        )
        self.assertEqual(unknown.primary.collection_ids, ())
        self.assertEqual(unknown.also_in_sources[0].relation_to_primary, "cooccurs")
        self.assertEqual(unknown.also_in_sources[0].block_ids, ("b1",))

    def test_extract_skips_set_notation_braces(self) -> None:
        """Wikipedia clips use `{ x 1 , ..., x n }`; naive first-brace slice fails at char 2."""
        reply = (
            "值域为{ x 1 , ..., x n }。\n"
            '{"primary": {"name": "信息熵", "slug": "information-entropy",'
            ' "wikiPath": "wiki/concepts/information-entropy.md",'
            ' "collectionIds": ["c1"], "blockIds": ["b1"],'
            ' "evidence": "[b1]", "outline": []},'
            ' "alsoInSources": [], "notes": ""}\n'
        )
        data = extract_json_object(reply)
        self.assertEqual(data["primary"]["name"], "信息熵")
        parsed = parse_topic_map(reply, intent="信息熵", collections=_store().collections)
        self.assertEqual(parsed.primary.title, "信息熵")

    def test_map_clips_injected_does_not_write_wiki(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            save_clip_store(layout, _store())
            mapped = map_clips(layout, map_text=MAP_JSON)
            self.assertEqual(mapped.primary.wiki_path, "wiki/concepts/kl-divergence.md")
            self.assertEqual(mapped.intent, "KL散度")
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_map_clips_cancel_before_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            save_clip_store(layout, _store())
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                map_clips(layout, map_text=MAP_JSON, cancel=cancel)

    def test_topic_map_job_cancel_does_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            save_clip_store(layout, _store())
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(JobCancelled):
                run_topic_map_job(layout, map_text=MAP_JSON, cancel=cancel)
            self.assertIsNone(load_clip_store(layout).topic_map)

    def test_source_identity_filters_planning_keeps_other_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            _seed_raw(layout, "raw/web/example.com/other.html")
            extra = ClipCollection(
                id="c2",
                source_key="raw/web/example.com/other.html",
                source_label="other",
                block_ids=("b3",),
                blocks=(
                    DerivedBlock(id="b3", tag="p", text="别的源。", preview="别"),
                ),
            )
            base = _store()
            save_clip_store(
                layout,
                ClipStore(
                    intent=base.intent,
                    collections=(*base.collections, extra),
                    topic_map=None,
                ),
            )
            seen: list[tuple[str, ...]] = []

            def wrapped(*args, **kwargs):
                store = kwargs["store"]
                seen.append(tuple(item.id for item in store.collections))
                return map_clips(*args, **kwargs)

            with (
                patch("noteagent.api.topic_gate.map_clips", side_effect=wrapped),
                patch("noteagent.ingest.confirmed.ingest_confirmed") as ingest,
            ):
                payload = run_topic_map_job(
                    layout,
                    map_text=MAP_JSON,
                    source_identity="raw/web/example.com/kl.html",
                )
            ingest.assert_not_called()
            self.assertEqual(seen, [("c1",)])
            self.assertEqual(payload["primary"]["slug"], "kl-divergence")
            disk = load_clip_store(layout)
            self.assertEqual({item.id for item in disk.collections}, {"c1", "c2"})
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_merge_plan_board_replaces_only_the_covered_pages(self) -> None:
        standing = parse_topic_map(
            BOARD_JSON, intent="KL散度", collections=_store_with_other().collections
        )
        answer = parse_topic_map(
            MAP_JSON, intent="KL散度", collections=_store().collections
        )
        merged = merge_plan_board(standing, answer, covered=_store().collections)
        self.assertEqual(
            [node.slug for node in merged.nodes()],
            ["kl-divergence", "other-page", "information-entropy"],
        )

    def test_merge_plan_board_drops_a_page_its_material_no_longer_feeds(self) -> None:
        standing = parse_topic_map(
            BOARD_JSON, intent="KL散度", collections=_store_with_other().collections
        )
        answer = parse_topic_map(
            {
                "primary": {
                    "name": "信息熵",
                    "slug": "information-entropy",
                    "collectionIds": ["c1"],
                    "blockIds": ["b2"],
                    "outline": [],
                },
                "alsoInSources": [],
            },
            intent="KL散度",
            collections=_store().collections,
        )
        merged = merge_plan_board(standing, answer, covered=_store().collections)
        # The pass read c1 and no longer has a page for it, so the page goes; the
        # page from the material it was not handed stays.
        self.assertEqual(
            [node.slug for node in merged.nodes()], ["other-page", "information-entropy"]
        )

    def test_carry_over_choices_keeps_actions_and_narrows_sections(self) -> None:
        board = parse_topic_map(
            BOARD_JSON, intent="KL散度", collections=_store_with_other().collections
        )
        kept = carry_over_choices(
            (
                GateDecision(slug="kl-divergence", action="mention", section_ids=("s1", "s0")),
                GateDecision(slug="other-page", action="skip", section_ids=("s0",)),
                GateDecision(slug="gone", action="write", section_ids=()),
            ),
            board,
        )
        self.assertEqual(
            kept,
            (
                # an action is the reader's, a section has to exist
                GateDecision(slug="kl-divergence", action="mention", section_ids=("s1",)),
                # nothing left of the answer: the whole new outline
                GateDecision(slug="other-page", action="skip", section_ids=("s9",)),
            ),
        )

    def test_source_identity_plan_merges_into_the_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            _seed_raw(layout, OTHER_SOURCE)
            store = _store_with_other()
            save_clip_store(
                layout,
                ClipStore(
                    intent=store.intent,
                    collections=store.collections,
                    topic_map=store.topic_map,
                    choices=(
                        GateDecision(slug="kl-divergence", action="mention", section_ids=("s1",)),
                        GateDecision(slug="other-page", action="skip", section_ids=()),
                        GateDecision(slug="gone", action="write", section_ids=()),
                    ),
                ),
            )
            payload = run_topic_map_job(
                layout,
                map_text=MAP_JSON,
                source_identity="raw/web/example.com/kl.html",
            )
            self.assertEqual(
                [node["slug"] for node in payload["candidates"]],
                ["kl-divergence", "other-page", "information-entropy"],
            )
            disk = load_clip_store(layout)
            # Re-planning one source ends the gate — the reader confirms again — but
            # does not make them answer the board from scratch.
            self.assertEqual(disk.decisions, ())
            self.assertEqual(
                [(item.slug, item.action) for item in disk.choices],
                [("kl-divergence", "mention"), ("other-page", "skip")],
            )

    def test_source_identity_without_clips_does_not_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _seed_raw(layout)
            save_clip_store(layout, _store())
            with self.assertRaises(ValueError) as ctx:
                run_topic_map_job(
                    layout,
                    map_text=MAP_JSON,
                    source_identity="raw/web/example.com/missing.html",
                )
            self.assertEqual(getattr(ctx.exception, "code", ""), "plan.noSelection")
            self.assertIsNone(load_clip_store(layout).topic_map)

    def test_map_clips_rejects_preview_collections(self) -> None:
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
                topic_map=None,
            )
            save_clip_store(layout, store)
            with self.assertRaises(Refusal) as ctx:
                map_clips(layout, map_text=MAP_JSON)
            self.assertEqual(ctx.exception.code, "source.missingOriginal")

    def test_outline_block_ids_stay_inside_named_collection(self) -> None:
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
                DerivedBlock(id="b2", tag="p", text="天气与编码。", preview="天气"),
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
        parsed = parse_topic_map(
            {
                "primary": {
                    "name": "信息熵",
                    "collectionIds": ["c-blog", "c-wiki"],
                    "blockIds": ["b1"],
                    "outline": [
                        {
                            "id": "s1",
                            "title": "编码推导",
                            "role": "derivation",
                            "collectionId": "c-blog",
                            "blockIds": ["b1", "b2"],
                        },
                        {
                            "id": "s2",
                            "title": "定义",
                            "role": "definition",
                            "collectionId": "c-wiki",
                            "blockIds": ["b1"],
                        },
                        {
                            "id": "s-mix",
                            "title": "混杂",
                            "collectionId": "c-wiki",
                            "blockIds": ["b1", "b2"],
                        },
                        {
                            "id": "s-ambiguous",
                            "title": "歧义块号",
                            "blockIds": ["b1"],
                        },
                    ],
                },
                "alsoInSources": [],
            },
            intent="信息熵",
            collections=(blog, wiki),
        )
        by_id = {item.id: item for item in parsed.primary.outline}
        self.assertEqual(by_id["s1"].block_ids, ("b1", "b2"))
        self.assertEqual(by_id["s1"].collection_id, "c-blog")
        self.assertEqual(by_id["s2"].block_ids, ("b1",))
        self.assertEqual(by_id["s2"].collection_id, "c-wiki")
        self.assertEqual(by_id["s-mix"].block_ids, ("b1",))
        self.assertNotIn("s-ambiguous", by_id)
        self.assertEqual(parsed.collection_summaries[0].title, "如何衡量不确定事物的发生？")
        self.assertEqual(parsed.collection_summaries[1].title, "定义")

    def test_flat_candidates_fill_coverage(self) -> None:
        parsed = parse_topic_map(
            MAP_JSON,
            intent="KL散度",
            collections=_store().collections,
        )
        self.assertEqual(len(parsed.candidates), 2)
        self.assertEqual(parsed.primary.coverage, "write")
        self.assertEqual(parsed.also_in_sources[0].coverage, "skip")
        dumped = parsed.to_dict()
        self.assertEqual(len(dumped["candidates"]), 2)
        self.assertEqual(dumped["candidates"][0]["coverage"], "write")
        self.assertEqual(len(dumped["edges"]), 1)
        self.assertEqual(dumped["edges"][0]["from"], "information-entropy")
        self.assertEqual(dumped["edges"][0]["to"], "kl-divergence")
        self.assertEqual(dumped["edges"][0]["relation"], "prerequisite")

    def test_declared_edges_keep_pairwise_and_drop_unknown(self) -> None:
        parsed = parse_topic_map(
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
                        "collectionId": "c1",
                        "blockIds": ["b2"],
                        "evidence": "[b2] 先定义熵。",
                    },
                    {
                        "from": "kl-divergence",
                        "to": "mutual-information",
                        "relation": "used_by",
                        "evidence": "[b1] 再接到互信息。",
                    },
                    {
                        "from": "missing",
                        "to": "kl-divergence",
                        "relation": "prerequisite",
                    },
                    {
                        "from": "kl-divergence",
                        "to": "information-entropy",
                        "relation": "used_by",
                    },
                ],
            },
            intent="信息论",
            collections=_store().collections,
        )
        pairs = {(item.source, item.target, item.relation) for item in parsed.edges}
        self.assertEqual(
            pairs,
            {
                ("information-entropy", "kl-divergence", "prerequisite"),
                ("kl-divergence", "mutual-information", "used_by"),
            },
        )

    def test_parse_candidates_without_intent(self) -> None:
        parsed = parse_topic_map(
            {
                "candidates": [
                    {
                        "name": "信息熵",
                        "slug": "information-entropy",
                        "coverage": "write",
                        "collectionIds": ["c1"],
                        "collectionRoles": [
                            {"collectionId": "c1", "role": "defines"}
                        ],
                        "blockIds": ["b1"],
                        "evidence": "[b1] 定义熵。",
                        "outline": [
                            {
                                "id": "s1",
                                "title": "定义",
                                "collectionId": "c1",
                                "blockIds": ["b1"],
                            }
                        ],
                    },
                    {
                        "name": "相对熵一带而过",
                        "coverage": "mention",
                        "collectionIds": ["c1"],
                        "collectionRoles": [
                            {"collectionId": "c1", "role": "mentions"}
                        ],
                        "blockIds": ["b2"],
                        "evidence": "[b2] 提了一句。",
                    },
                ]
            },
            intent="",
            collections=_store().collections,
        )
        self.assertEqual(parsed.intent, "")
        self.assertEqual(parsed.primary.title, "信息熵")
        self.assertEqual(parsed.primary.coverage, "write")
        self.assertEqual(parsed.also_in_sources[0].coverage, "mention")
        self.assertEqual(parsed.candidates[0].collection_roles, (("c1", "defines"),))

    def test_two_write_pages_cannot_share_a_collection(self) -> None:
        parsed = parse_topic_map(
            {
                "candidates": [
                    {
                        "name": "信息熵",
                        "coverage": "write",
                        "collectionIds": ["c1"],
                        "blockIds": ["b1"],
                        "evidence": "[b1]",
                    },
                    {
                        "name": "KL散度",
                        "coverage": "write",
                        "collectionIds": ["c1"],
                        "blockIds": ["b1"],
                        "evidence": "[b1]",
                    },
                ]
            },
            intent="",
            collections=_store().collections,
        )
        self.assertEqual(parsed.candidates[0].collection_ids, ("c1",))
        self.assertEqual(parsed.candidates[0].coverage, "write")
        self.assertEqual(parsed.candidates[1].collection_ids, ())
        self.assertEqual(parsed.candidates[1].coverage, "skip")

    def test_role_infers_write_coverage(self) -> None:
        parsed = parse_topic_map(
            {
                "candidates": [
                    {
                        "name": "信息熵",
                        "collectionIds": ["c1"],
                        "collectionRoles": [
                            {"collectionId": "c1", "role": "defines"}
                        ],
                        "blockIds": ["b1"],
                    }
                ]
            },
            intent="",
            collections=_store().collections,
        )
        self.assertEqual(parsed.primary.coverage, "write")

    def test_extract_candidates_without_primary_key(self) -> None:
        reply = (
            "值域为{ x 1 , ..., x n }。\n"
            '{"candidates": [{"name": "信息熵", "slug": "information-entropy",'
            ' "coverage": "write", "collectionIds": ["c1"], "blockIds": ["b1"],'
            ' "evidence": "[b1]", "outline": []}], "notes": ""}\n'
        )
        data = extract_json_object(reply)
        self.assertEqual(data["candidates"][0]["name"], "信息熵")
        parsed = parse_topic_map(reply, intent="", collections=_store().collections)
        self.assertEqual(parsed.primary.title, "信息熵")

    def test_map_clips_empty_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            store = _store()
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=store.collections,
                    topic_map=None,
                ),
            )
            _seed_raw(layout)
            mapped = map_clips(layout, intent="", map_text=MAP_JSON)
            self.assertEqual(mapped.intent, "")
            self.assertEqual(mapped.primary.title, "KL散度")
            self.assertFalse((layout.wiki / "concepts" / "kl-divergence.md").exists())

    def test_hangs_existing_page_by_title_not_new_concepts_slug(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            existing = layout.wiki / "entities" / "kl-divergence.md"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text(
                "---\ntype: entity\ntitle: KL散度\n---\n\n# KL散度\n",
                encoding="utf-8",
            )
            parsed = parse_topic_map(
                {
                    "candidates": [
                        {
                            "name": "KL散度",
                            "slug": "kl-new",
                            "wikiPath": "wiki/concepts/kl-new.md",
                            "coverage": "write",
                            "collectionIds": ["c1"],
                            "blockIds": ["b1"],
                            "evidence": "[b1]",
                        }
                    ]
                },
                intent="KL散度",
                collections=_store().collections,
                layout=layout,
            )
            self.assertEqual(parsed.primary.wiki_path, "wiki/entities/kl-divergence.md")
            self.assertEqual(parsed.primary.slug, "kl-new")

    def test_hangs_bare_slug_to_entities_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            existing = layout.wiki / "entities" / "information-entropy.md"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text(
                "---\ntype: entity\ntitle: 信息熵\n---\n\n# 熵\n",
                encoding="utf-8",
            )
            parsed = parse_topic_map(
                MAP_JSON,
                intent="KL散度",
                collections=_store().collections,
                layout=layout,
            )
            self.assertEqual(
                parsed.also_in_sources[0].wiki_path,
                "wiki/entities/information-entropy.md",
            )
            self.assertEqual(
                parsed.primary.wiki_path, "wiki/concepts/kl-divergence.md"
            )

    def test_hangs_index_title_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.wiki_index.write_text(
                "# Wiki Index\n\n## Concepts\n\n- [[concepts/kl-divergence]] — KL散度\n",
                encoding="utf-8",
            )
            parsed = parse_topic_map(
                {
                    "primary": {
                        "name": "KL散度",
                        "slug": "kl-new",
                        "wikiPath": "wiki/concepts/kl-new.md",
                        "collectionIds": ["c1"],
                        "blockIds": ["b1"],
                    }
                },
                intent="KL散度",
                collections=_store().collections,
                layout=layout,
            )
            self.assertEqual(parsed.primary.wiki_path, "wiki/concepts/kl-divergence.md")

    def test_does_not_hang_onto_clip_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            summary = layout.wiki / "sources" / "kl-clips.md"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(
                "---\ntype: source\ntitle: KL散度\n---\n\n# clips\n",
                encoding="utf-8",
            )
            parsed = parse_topic_map(
                {
                    "primary": {
                        "name": "KL散度",
                        "slug": "kl-new",
                        "wikiPath": "wiki/concepts/kl-new.md",
                        "collectionIds": ["c1"],
                        "blockIds": ["b1"],
                    }
                },
                intent="KL散度",
                collections=_store().collections,
                layout=layout,
            )
            self.assertEqual(parsed.primary.wiki_path, "wiki/concepts/kl-new.md")

    def test_collapses_two_candidates_onto_one_existing_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            existing = layout.wiki / "concepts" / "kl-divergence.md"
            existing.parent.mkdir(parents=True, exist_ok=True)
            existing.write_text(
                "---\ntype: concept\ntitle: KL散度\n---\n\n# KL\n",
                encoding="utf-8",
            )
            parsed = parse_topic_map(
                {
                    "candidates": [
                        {
                            "name": "KL散度",
                            "slug": "kl-a",
                            "wikiPath": "wiki/concepts/kl-a.md",
                            "coverage": "write",
                            "collectionIds": ["c1"],
                            "blockIds": ["b1"],
                        },
                        {
                            "name": "KL Divergence",
                            "slug": "kl-b",
                            "wikiPath": "wiki/concepts/kl-divergence.md",
                            "coverage": "write",
                            "collectionIds": ["c1"],
                            "blockIds": ["b2"],
                        },
                    ]
                },
                intent="KL散度",
                collections=_store().collections,
                layout=layout,
            )
            paths = [item.wiki_path for item in parsed.nodes()]
            self.assertEqual(paths, ["wiki/concepts/kl-divergence.md"])

    def test_confirmed_uses_hung_path_not_client_wiki_path(self) -> None:
        store = _store()
        parsed = parse_topic_map(
            {
                "primary": {
                    "name": "KL散度",
                    "slug": "kl-divergence",
                    "wikiPath": "wiki/entities/kl-divergence.md",
                    "collectionIds": ["c1"],
                    "blockIds": ["b1"],
                }
            },
            intent="KL散度",
            collections=store.collections,
        )
        page = confirmed_from_dict(
            {
                "name": "KL散度",
                "slug": "kl-divergence",
                "wikiPath": "wiki/concepts/kl-new.md",
                "action": "write",
                "sectionIds": ["s1"],
            },
            parsed,
        )
        self.assertEqual(page.wiki_path, "wiki/entities/kl-divergence.md")


if __name__ == "__main__":
    unittest.main()
