from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent.acquire.run_search import WebSearchRun
from noteagent.acquire.types import CandidateCard
from noteagent.api.topic_gate import run_topic_map_job
from noteagent.chat.limits import default_limits
from noteagent.chat.skills import list_skills
from noteagent.chat.tools import observe_tool
from noteagent.ingest.clips import (
    ClipCollection,
    ClipStore,
    DerivedBlock,
    load_clip_store,
    save_clip_store,
)
from noteagent.ingest.deep_search_store import (
    BLOCKS_PER_PAGE,
    CARDS_PER_PAGE,
    MAX_CARDS,
    MAX_OVERLAY_EDGES,
    MAX_WHITELIST,
)
from noteagent.project.init import init_project
from noteagent.util.refusal import Refusal
from noteagent.wiki.deep_search import (
    BODY_SHORT_CHARS,
    act_session,
    collections_for_planning,
    load_session,
    propose_wiki_gaps,
    remainder_potential,
    whitelist_slugs,
)
from noteagent.wiki.graph import wiki_graph_payload

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


def _write_page(layout, relative: str, body: str) -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _write_html(layout, relative: str, html: str) -> None:
    path = layout.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def _chew_to_step4(
    layout,
    *,
    rel: str = "raw/web/sample.html",
    daily: bool = True,
    second_page: bool = False,
) -> dict:
    bits = ['<p id="na-b1" data-na-block="b1">向量空间 定义</p>']
    if daily:
        bits.append('<p id="na-b9" data-na-block="b9">日常已圈</p>')
    _write_html(layout, rel, "\n".join(bits) + "\n")
    rel2 = "raw/web/entropy.html"
    if second_page:
        _write_html(
            layout,
            rel2,
            '<p id="na-b2" data-na-block="b2">信息熵 定义</p>\n',
        )
    _write_page(
        layout,
        "wiki/concepts/alive.md",
        "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
    )
    if daily:
        save_clip_store(
            layout,
            ClipStore(
                intent="日常",
                collections=(
                    ClipCollection(
                        id="daily-1",
                        source_key=rel,
                        source_label="sample",
                        block_ids=("b9",),
                        blocks=(
                            DerivedBlock(
                                id="b9", tag="p", text="日常已圈", preview="日常已圈"
                            ),
                        ),
                    ),
                ),
                topic_map={"primary": {"name": "日常篇", "slug": "daily-note"}},
            ),
        )
    pages = [
        {
            "slug": "向量空间",
            "title": "向量空间",
            "action": "add",
            "plan": "补定义",
            "query": "向量空间",
        }
    ]
    blocks = [
        {
            "pageSlug": "向量空间",
            "locator": f"{rel}#b1",
            "preview": "向量空间 定义",
        }
    ]
    if second_page:
        pages.append(
            {
                "slug": "信息熵",
                "title": "信息熵",
                "action": "add",
                "plan": "补定义",
                "query": "信息熵",
            }
        )
        blocks.append(
            {
                "pageSlug": "信息熵",
                "locator": f"{rel2}#b2",
                "preview": "信息熵 定义",
            }
        )
    act_session(layout, {"action": "start", "mode": "chew"})
    act_session(layout, {"action": "proposeBlocks"}, script={"blocks": blocks})
    for item in blocks:
        act_session(
            layout,
            {"action": "toggleBlock", "locator": item["locator"], "accepted": True},
        )
    return act_session(layout, {"action": "acceptBlocks"})


class DeepSearchGapTests(unittest.TestCase):
    def test_ghost_and_thin_still_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]] and [[concepts/softmax|softmax 函数]].\n"
                "Also [[index]] and [[concepts/alive]].\n",
            )
            _write_page(
                layout,
                "wiki/concepts/stub.md",
                "---\ntype: concept\ntitle: Stub\n---\n\n短。\n",
            )
            report = propose_wiki_gaps(layout)
            ghosts = [item for item in report.proposals if item.kind == "ghost"]
            slugs = {item.slug for item in ghosts}
            self.assertIn("向量空间", slugs)
            self.assertIn("concepts/softmax", slugs)
            self.assertNotIn("index", slugs)
            thin = [item for item in report.proposals if item.kind == "thin"]
            self.assertTrue(any(item.slug == "concepts/stub" for item in thin))
            self.assertLess(len("短。"), BODY_SHORT_CHARS)

    def test_old_v1_session_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.meta.mkdir(parents=True, exist_ok=True)
            layout.deep_search.write_text(
                '{"version": 1, "scope": "wiki-adjacent", "proposals": [{"id": "x"}]}\n',
                encoding="utf-8",
            )
            session = load_session(layout)
            self.assertEqual(session.round_id, "")
            self.assertEqual(session.step, 0)

    def test_missing_fields_and_corrupt_json_are_empty_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            layout.meta.mkdir(parents=True, exist_ok=True)
            layout.deep_search.write_text(
                '{"version": 2, "roundId": "abc123", "mode": "explore"}\n',
                encoding="utf-8",
            )
            session = load_session(layout)
            self.assertEqual(session.round_id, "abc123")
            self.assertEqual(session.mode, "explore")
            self.assertEqual(session.whitelist, ())
            self.assertEqual(session.overlay_edges, ())
            self.assertEqual(session.cards, ())
            self.assertFalse(session.truncated)
            payload = act_session(layout, {"action": "clear"})
            self.assertIn("truncated", payload)
            self.assertIn("whitelistOmitted", payload)
            self.assertIn("cardsOmitted", payload)

            layout.deep_search.write_text("{not json", encoding="utf-8")
            session = load_session(layout)
            self.assertEqual(session.round_id, "")

            layout.deep_search.write_text(
                '{"version": 2, "mode": "nope", "roundId": "x"}\n',
                encoding="utf-8",
            )
            session = load_session(layout)
            self.assertEqual(session.round_id, "")

            layout.deep_search.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "mode": "chew",
                        "roundId": "keep-me",
                        "whitelist": [{"slug": "ok", "title": "Ok"}, "bad", {}],
                        "overlayEdges": "nope",
                        "collectionIds": "nope",
                    }
                ),
                encoding="utf-8",
            )
            session = load_session(layout)
            self.assertEqual(session.round_id, "keep-me")
            self.assertEqual([item.slug for item in session.whitelist], ["ok"])
            self.assertEqual(session.overlay_edges, ())
            self.assertEqual(session.collection_ids, ())


class DeepSearchRoundTests(unittest.TestCase):
    def test_chew_disabled_without_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            potential = remainder_potential(layout)
            self.assertTrue(potential["chewDisabled"])
            with self.assertRaises(ValueError):
                act_session(layout, {"action": "start", "mode": "chew"})

    def test_chew_potential_lists_top_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            for name, count in (("a.html", 3), ("b.html", 2), ("c.html", 1)):
                bits = "".join(
                    f'<p id="na-b{i}" data-na-block="b{i}">块{i}</p>\n'
                    for i in range(1, count + 1)
                )
                _write_html(layout, f"raw/web/{name}", bits)
            potential = remainder_potential(layout)
            self.assertFalse(potential["chewDisabled"])
            self.assertEqual(potential["filesWithRemainder"], 3)
            self.assertEqual(potential["sourceFiles"], 3)
            tops = [str(item["path"]) for item in potential["topFiles"]]  # type: ignore[index]
            self.assertEqual(
                tops,
                [
                    "raw/web/a.html",
                    "raw/web/b.html",
                    "raw/web/c.html",
                ],
            )

    def test_explore_start_ignores_raw_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_html(
                layout,
                "raw/web/sample.html",
                '<p id="na-b1" data-na-block="b1">块</p>\n',
            )
            payload = act_session(
                layout,
                {
                    "action": "start",
                    "mode": "explore",
                    "seed": {
                        "kind": "raw",
                        "path": "raw/web/sample.html",
                        "title": "sample.html",
                    },
                },
            )
            self.assertEqual(payload["mode"], "explore")
            self.assertEqual(payload["step"], 2)
            self.assertIsNone(payload["seed"])
            with self.assertRaises(ValueError):
                act_session(layout, {"action": "start", "mode": "explore"})

    def test_explore_whitelist_from_ghost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]].\n",
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            payload = act_session(
                layout, {"action": "proposeWhitelist"}, use_llm=False
            )
            slugs = [item["slug"] for item in payload["whitelist"]]
            self.assertIn("向量空间", slugs)
            overlay = [
                (item["source"], item["target"]) for item in payload["overlayEdges"]
            ]
            self.assertIn(("concepts/alive", "向量空间"), overlay)
            graph = wiki_graph_payload(layout)
            graph_ids = {str(item["id"]) for item in graph["nodes"]}  # type: ignore[index]
            self.assertIn("concepts/alive", graph_ids)
            self.assertNotIn("向量空间-overlay", graph_ids)
            self.assertEqual(len(payload["whitelist"]), len(payload["whitelist"][:8]))

    def test_chew_accept_blocks_are_round_collections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n'
                '<p id="na-b2" data-na-block="b2">无关目录</p>\n',
            )
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(
                        ClipCollection(
                            id="daily-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b2",),
                            blocks=(
                                DerivedBlock(
                                    id="b2", tag="p", text="无关目录", preview="无关目录"
                                ),
                            ),
                        ),
                    ),
                ),
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {
                            "pageSlug": "向量空间",
                            "locator": f"{rel}#b1",
                            "preview": "向量空间 定义",
                        }
                    ]
                },
            )
            act_session(
                layout,
                {"action": "toggleBlock", "locator": f"{rel}#b1", "accepted": True},
            )
            payload = act_session(layout, {"action": "acceptBlocks"})
            self.assertEqual(payload["step"], 4)
            store = load_clip_store(layout)
            round_ids = [item.id for item in store.collections if item.round_id]
            self.assertTrue(round_ids)
            self.assertTrue(any(item.id == "daily-1" for item in store.collections))
            planning = collections_for_planning(layout, store)
            self.assertEqual({item.id for item in planning}, set(round_ids))
            self.assertTrue(all(item.target_slug == "" for item in planning))

    def test_chew_select_all_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n'
                '<p id="na-b2" data-na-block="b2">向量空间 例子</p>\n',
            )
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {
                            "pageSlug": "向量空间",
                            "locator": f"{rel}#b1",
                            "preview": "向量空间 定义",
                        },
                        {
                            "pageSlug": "向量空间",
                            "locator": f"{rel}#b2",
                            "preview": "向量空间 例子",
                        },
                    ]
                },
            )
            selected = act_session(layout, {"action": "selectAllBlocks"})
            self.assertTrue(
                all(item["accepted"] for item in selected["proposedBlocks"])
            )
            cleared = act_session(
                layout, {"action": "selectAllBlocks", "accepted": False}
            )
            self.assertTrue(
                all(not item["accepted"] for item in cleared["proposedBlocks"])
            )

    def test_end_discard_drops_round_keeps_daily(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = _chew_to_step4(layout)
            self.assertEqual(payload["whitelist"], [])
            self.assertEqual(payload["overlayEdges"], [])
            graph_before = wiki_graph_payload(layout)
            with self.assertRaises(Refusal) as unsettled:
                act_session(layout, {"action": "end"})
            self.assertEqual(unsettled.exception.code, "search.unsettled")
            with self.assertRaises(Refusal) as has_collections:
                act_session(layout, {"action": "clear"})
            self.assertEqual(has_collections.exception.code, "search.hasCollections")
            with self.assertRaises(Refusal) as running:
                act_session(layout, {"action": "start", "mode": "chew"})
            self.assertEqual(running.exception.code, "search.running")
            ended = act_session(layout, {"action": "end", "leftover": "discard"})
            self.assertEqual(ended["step"], 0)
            self.assertEqual(ended["roundId"], "")
            self.assertEqual(ended["overlayEdges"], [])
            self.assertEqual(ended["whitelist"], [])
            store = load_clip_store(layout)
            self.assertEqual([item.id for item in store.collections], ["daily-1"])
            self.assertEqual(store.intent, "日常")
            self.assertEqual((store.topic_map or {}).get("primary", {}).get("slug"), "daily-note")
            graph_after = wiki_graph_payload(layout)
            self.assertEqual(graph_before["nodes"], graph_after["nodes"])
            self.assertEqual(graph_before["edges"], graph_after["edges"])
            again = act_session(layout, {"action": "start", "mode": "chew"})
            self.assertEqual(again["step"], 3)
            act_session(layout, {"action": "clear"})

    def test_end_merge_pending_into_daily(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_to_step4(layout)
            act_session(layout, {"action": "end", "leftover": "merge"})
            store = load_clip_store(layout)
            ids = {item.id: item for item in store.collections}
            self.assertIn("daily-1", ids)
            merged = [item for item in store.collections if item.id != "daily-1"]
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].round_id, "")
            self.assertEqual(merged[0].target_slug, "")
            self.assertEqual(load_session(layout).round_id, "")

    def test_end_ingested_dropped_without_leftover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_to_step4(layout)
            store = load_clip_store(layout)
            round_ids = [item.id for item in store.collections if item.round_id]
            self.assertTrue(round_ids)
            marked = act_session(
                layout, {"action": "markIngested", "collectionIds": round_ids}
            )
            self.assertEqual(marked["step"], 5)
            self.assertEqual(list(marked["ingestedIds"]), round_ids)
            act_session(layout, {"action": "end"})
            store = load_clip_store(layout)
            self.assertEqual([item.id for item in store.collections], ["daily-1"])
            self.assertEqual(load_session(layout).overlay_edges, ())

    def test_end_mixed_ingested_and_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _chew_to_step4(layout, second_page=True)
            store = load_clip_store(layout)
            round_items = [item for item in store.collections if item.round_id]
            self.assertEqual(len(round_items), 2)
            first, second = round_items[0].id, round_items[1].id
            act_session(layout, {"action": "markIngested", "collectionIds": [first]})
            with self.assertRaises(Refusal) as unsettled:
                act_session(layout, {"action": "end"})
            self.assertEqual(unsettled.exception.code, "search.unsettled")
            act_session(layout, {"action": "end", "leftover": "merge"})
            store = load_clip_store(layout)
            ids = {item.id: item for item in store.collections}
            self.assertIn("daily-1", ids)
            self.assertNotIn(first, ids)
            self.assertIn(second, ids)
            self.assertEqual(ids[second].round_id, "")

    def test_end_before_plan_keeps_daily_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(layout, rel, '<p id="na-b1" data-na-block="b1">块</p>\n')
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(),
                    topic_map={"primary": {"name": "日常篇", "slug": "daily-note"}},
                ),
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            session = load_session(layout)
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(
                        ClipCollection(
                            id="round-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b1",),
                            blocks=(
                                DerivedBlock(id="b1", tag="p", text="块", preview="块"),
                            ),
                            round_id=session.round_id,
                            target_slug="向量空间",
                        ),
                    ),
                    topic_map={"primary": {"name": "日常篇", "slug": "daily-note"}},
                ),
            )
            act_session(layout, {"action": "end", "leftover": "discard"})
            store = load_clip_store(layout)
            self.assertEqual(store.collections, ())
            self.assertEqual(store.intent, "日常")
            self.assertEqual((store.topic_map or {}).get("primary", {}).get("slug"), "daily-note")

    def test_redo_only_clears_this_step_and_after(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = _chew_to_step4(layout)
            self.assertEqual(payload["whitelist"], [])
            self.assertEqual(payload["overlayEdges"], [])
            self.assertTrue(payload["collectionIds"])
            before_wiki = (layout.wiki / "concepts" / "alive.md").read_text(encoding="utf-8")

            redone4 = act_session(layout, {"action": "redo", "step": 4})
            self.assertEqual(redone4["step"], 4)
            self.assertEqual(redone4["whitelist"], [])
            self.assertTrue(redone4["collectionIds"])
            store = load_clip_store(layout)
            self.assertTrue(any(item.round_id for item in store.collections))
            self.assertTrue(any(item.id == "daily-1" for item in store.collections))
            self.assertEqual(
                (layout.wiki / "concepts" / "alive.md").read_text(encoding="utf-8"),
                before_wiki,
            )

            redone3 = act_session(layout, {"action": "redo", "step": 3})
            self.assertEqual(redone3["step"], 3)
            self.assertEqual(redone3["whitelist"], [])
            self.assertEqual(redone3["overlayEdges"], [])
            self.assertEqual(redone3["proposedBlocks"], [])
            self.assertEqual(redone3["collectionIds"], [])
            store = load_clip_store(layout)
            self.assertEqual([item.id for item in store.collections], ["daily-1"])

            act_session(layout, {"action": "end", "leftover": "discard"})

    def test_restart_discards_round_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = _chew_to_step4(layout)
            self.assertEqual(payload["step"], 4)
            restarted = act_session(layout, {"action": "restart"})
            self.assertEqual(restarted["step"], 0)
            self.assertEqual(restarted["roundId"], "")
            store = load_clip_store(layout)
            self.assertEqual([item.id for item in store.collections], ["daily-1"])
            self.assertEqual(store.intent, "日常")
            again = act_session(layout, {"action": "start", "mode": "chew"})
            self.assertEqual(again["step"], 3)

    def test_restart_blocked_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            payload = _chew_to_step4(layout)
            act_session(
                layout,
                {"action": "markIngested", "collectionIds": payload["collectionIds"]},
            )
            with self.assertRaises(Refusal) as written:
                act_session(layout, {"action": "restart"})
            self.assertEqual(written.exception.code, "search.alreadyWritten")
            with self.assertRaises(Refusal) as undo:
                act_session(layout, {"action": "redo", "step": 5})
            self.assertEqual(undo.exception.code, "search.cannotUndo")
            act_session(layout, {"action": "end"})

    def test_chat_run_no_longer_has_deep_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            obs = observe_tool(
                "run",
                layout,
                {"job": "deep_search"},
                default_limits(),
            )[0]
            self.assertTrue(obs.meta.get("error"))
            self.assertIn("doctor or fetch", obs.content)

    def test_chat_has_no_deep_search_skill(self) -> None:
        names = {item.name for item in list_skills()}
        self.assertNotIn("deep-search", names)

    def test_one_round_at_a_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[缺口]].\n",
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            with self.assertRaises(ValueError):
                act_session(layout, {"action": "start", "mode": "explore"})

    def test_whitelist_caps_and_c_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_html(
                layout,
                "raw/web/sample.html",
                '<p id="na-b1" data-na-block="b1">未圈选块</p>\n',
            )
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\n正文。\n",
            )
            graph_before = {
                str(item["id"]) for item in wiki_graph_payload(layout)["nodes"]
            }
            act_session(layout, {"action": "start", "mode": "explore"})
            pages = [
                {
                    "slug": f"page-{index}",
                    "title": f"Page {index}",
                    "action": "add",
                    "plan": "补",
                    "query": f"page {index}",
                }
                for index in range(10)
            ]
            edges = [
                {
                    "source": "concepts/alive",
                    "target": "page-0",
                    "relation": "拟补",
                }
            ]
            edges.extend(
                {"source": f"page-{i}", "target": f"page-{j}", "relation": "拟链"}
                for i in range(10)
                for j in range(10)
                if i != j
            )
            payload = act_session(
                layout,
                {"action": "proposeWhitelist"},
                script={"pages": pages, "edges": edges},
            )
            self.assertEqual(len(payload["whitelist"]), MAX_WHITELIST)
            self.assertEqual(payload["whitelistOmitted"], 2)
            self.assertEqual(len(payload["overlayEdges"]), MAX_OVERLAY_EDGES)
            overlay = {
                (item["source"], item["target"]) for item in payload["overlayEdges"]
            }
            self.assertIn(("concepts/alive", "page-0"), overlay)
            graph_after = {
                str(item["id"]) for item in wiki_graph_payload(layout)["nodes"]
            }
            self.assertEqual(graph_before, graph_after)
            self.assertNotIn("page-0", graph_after)

            toggled = act_session(
                layout,
                {"action": "togglePage", "slug": "page-1", "accepted": False},
            )
            dropped = [item for item in toggled["whitelist"] if item["slug"] == "page-1"]
            self.assertTrue(dropped)
            self.assertFalse(dropped[0]["accepted"])

            accepted = act_session(layout, {"action": "acceptWhitelist"})
            kept = {item["slug"] for item in accepted["whitelist"]}
            self.assertNotIn("page-1", kept)
            self.assertIn("page-0", kept)
            self.assertEqual(accepted["step"], 3)
            self.assertIn(
                ("concepts/alive", "page-0"),
                {(item["source"], item["target"]) for item in accepted["overlayEdges"]},
            )

            redone = act_session(layout, {"action": "redo", "step": 2})
            self.assertEqual(redone["step"], 2)
            self.assertEqual(redone["whitelist"], [])
            self.assertEqual(redone["overlayEdges"], [])

    def test_chew_blocks_cap_leftover_and_drop_empty_add(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            bits = "".join(
                f'<p id="na-b{i}" data-na-block="b{i}">向量空间 块{i}</p>\n'
                for i in range(1, 11)
            )
            _write_html(layout, rel, bits)
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(
                        ClipCollection(
                            id="daily-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b10",),
                            blocks=(
                                DerivedBlock(
                                    id="b10",
                                    tag="p",
                                    text="向量空间 块10",
                                    preview="向量空间 块10",
                                ),
                            ),
                        ),
                    ),
                ),
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            proposed = act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {
                            "pageSlug": "向量空间",
                            "locator": f"{rel}#b{i}",
                            "preview": f"向量空间 块{i}",
                        }
                        for i in range(1, 11)
                    ]
                    + [
                        {
                            "pageSlug": "外人",
                            "locator": f"{rel}#b2",
                            "preview": "向量空间 块2",
                        }
                    ]
                },
            )
            locators = [item["locator"] for item in proposed["proposedBlocks"]]
            self.assertEqual(len(locators), BLOCKS_PER_PAGE)
            self.assertNotIn(f"{rel}#b10", locators)
            self.assertNotIn(f"{rel}#b9", locators)
            self.assertTrue(proposed["truncated"])
            self.assertTrue(
                all(item["pageSlug"] == "" for item in proposed["proposedBlocks"])
            )
            with self.assertRaises(ValueError):
                act_session(layout, {"action": "acceptBlocks"})
            act_session(
                layout,
                {"action": "toggleBlock", "locator": f"{rel}#b1", "accepted": True},
            )
            accepted = act_session(layout, {"action": "acceptBlocks"})
            self.assertEqual(accepted["whitelist"], [])
            self.assertEqual(accepted["step"], 4)
            store = load_clip_store(layout)
            round_items = [item for item in store.collections if item.round_id]
            self.assertEqual(len(round_items), 1)
            self.assertEqual(round_items[0].block_ids, ("b1",))
            self.assertEqual(round_items[0].target_slug, "")
            self.assertTrue(any(item.id == "daily-1" for item in store.collections))

    def test_chew_blocks_accept_title_slug_and_short_locator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n',
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            proposed = act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {
                            "pageSlug": "向量空间",
                            "locator": "sample.html#b1",
                            "preview": "向量空间 定义",
                        }
                    ]
                },
            )
            self.assertEqual(len(proposed["proposedBlocks"]), 1)
            self.assertEqual(proposed["proposedBlocks"][0]["pageSlug"], "")
            self.assertEqual(proposed["proposedBlocks"][0]["locator"], f"{rel}#b1")

    def test_chew_blocks_llm_empty_falls_back_to_title_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n',
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            with patch(
                "noteagent.wiki.deep_search._blocks_from_llm",
                return_value=((), False, "ok"),
            ):
                proposed = act_session(layout, {"action": "proposeBlocks"})
            self.assertEqual(len(proposed["proposedBlocks"]), 1)
            self.assertIn("fell back", proposed["notice"])

    def test_chew_fallback_blocks_from_unclipped_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n'
                '<p id="na-b2" data-na-block="b2">目录无关</p>\n',
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            proposed = act_session(
                layout, {"action": "proposeBlocks"}, use_llm=False
            )
            locators = [item["locator"] for item in proposed["proposedBlocks"]]
            self.assertEqual(set(locators), {f"{rel}#b1", f"{rel}#b2"})
            self.assertTrue(all(item["pageSlug"] == "" for item in proposed["proposedBlocks"]))
            self.assertFalse(proposed["proposedBlocks"][0]["accepted"])

    def test_explore_cards_caps_and_not_clips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            act_session(
                layout,
                {"action": "proposeWhitelist"},
                script={
                    "pages": [
                        {
                            "slug": "向量空间",
                            "title": "向量空间",
                            "action": "add",
                            "query": "向量空间",
                        },
                        {
                            "slug": "concepts/alive",
                            "title": "Alive",
                            "action": "revise",
                            "query": "alive",
                        },
                    ]
                },
            )
            cards = [
                {
                    "pageSlug": "向量空间",
                    "title": f"A{i}",
                    "url": f"https://example.test/a/{i}",
                    "snippet": "snippet",
                    "site": "example.test",
                }
                for i in range(1, 7)
            ] + [
                {
                    "pageSlug": "concepts/alive",
                    "title": f"B{i}",
                    "url": f"https://example.test/b/{i}",
                    "snippet": "snippet",
                    "site": "example.test",
                }
                for i in range(1, 7)
            ]
            cards.append(
                {
                    "pageSlug": "外人",
                    "title": "X",
                    "url": "https://example.test/x",
                    "snippet": "no",
                }
            )
            cards.append(
                {
                    "pageSlug": "concepts/alive",
                    "title": "dup",
                    "url": "https://example.test/a/1",
                    "snippet": "dup",
                }
            )
            payload = act_session(
                layout,
                {"action": "acceptWhitelist"},
                script={"cards": cards},
            )
            self.assertEqual(payload["step"], 3)
            rows = payload["cards"]
            self.assertEqual(len(rows), CARDS_PER_PAGE * 2)
            self.assertTrue(payload["cardsOmitted"] >= 2)
            by_page: dict[str, int] = {}
            urls = []
            for item in rows:
                by_page[item["pageSlug"]] = by_page.get(item["pageSlug"], 0) + 1
                urls.append(item["url"])
                self.assertFalse(item["picked"])
            self.assertLessEqual(max(by_page.values()), CARDS_PER_PAGE)
            self.assertEqual(len(urls), len(set(urls)))
            self.assertNotIn("https://example.test/x", urls)
            store = load_clip_store(layout)
            self.assertEqual(store.collections, ())
            toggled = act_session(
                layout,
                {
                    "action": "toggleCard",
                    "url": rows[0]["url"],
                    "picked": True,
                },
            )
            self.assertTrue(
                any(item["picked"] for item in toggled["cards"])
            )
            self.assertEqual(load_clip_store(layout).collections, ())
            with self.assertRaises(ValueError):
                act_session(layout, {"action": "enterPlan"})

    def test_explore_cards_round_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\n正文够长所以不是薄页。\n",
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            act_session(
                layout,
                {"action": "proposeWhitelist"},
                script={
                    "pages": [
                        {
                            "slug": f"page-{index}",
                            "title": f"Page {index}",
                            "action": "add",
                            "query": f"q{index}",
                        }
                        for index in range(8)
                    ]
                },
            )
            payload = act_session(
                layout,
                {"action": "acceptWhitelist"},
                script={
                    "cards": [
                        {
                            "pageSlug": f"page-{i}",
                            "title": f"{i}-{j}",
                            "url": f"https://example.test/{i}/{j}",
                        }
                        for i in range(8)
                        for j in range(CARDS_PER_PAGE)
                    ]
                },
            )
            self.assertEqual(len(payload["cards"]), MAX_CARDS)
            self.assertTrue(payload["cardsOmitted"] > 0)
            self.assertTrue(payload["truncated"])

    def test_chew_start_skips_whitelist_and_does_not_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            _write_html(
                layout,
                "raw/web/sample.html",
                '<p id="na-b1" data-na-block="b1">块</p>\n',
            )
            with patch("noteagent.acquire.run_search.run_web_search") as mocked:
                payload = act_session(layout, {"action": "start", "mode": "chew"})
                with self.assertRaises(Refusal) as chew_first:
                    act_session(layout, {"action": "proposeWhitelist"})
                self.assertEqual(chew_first.exception.code, "search.chewFirst")
            mocked.assert_not_called()
            self.assertEqual(payload["step"], 3)
            self.assertEqual(payload["cards"], [])
            self.assertEqual(payload["whitelist"], [])

    def test_whitelist_not_applied_before_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">未圈选块</p>\n'
                '<p id="na-b2" data-na-block="b2">日常已圈</p>\n',
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(
                        ClipCollection(
                            id="daily-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b2",),
                            blocks=(
                                DerivedBlock(
                                    id="b2", tag="p", text="日常已圈", preview="日常已圈"
                                ),
                            ),
                        ),
                    ),
                ),
            )
            payload = act_session(layout, {"action": "start", "mode": "chew"})
            self.assertEqual(payload["step"], 3)
            self.assertIsNone(whitelist_slugs(layout))
            planning = collections_for_planning(layout)
            self.assertEqual({item.id for item in planning}, {"daily-1"})

    def test_round_plan_whitelist_only_and_graph_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(
                layout,
                rel,
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n'
                '<p id="na-b2" data-na-block="b2">无关目录</p>\n',
            )
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            save_clip_store(
                layout,
                ClipStore(
                    intent="日常",
                    collections=(
                        ClipCollection(
                            id="daily-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b2",),
                            blocks=(
                                DerivedBlock(
                                    id="b2", tag="p", text="无关目录", preview="无关目录"
                                ),
                            ),
                        ),
                    ),
                ),
            )
            act_session(layout, {"action": "start", "mode": "chew"})
            act_session(
                layout,
                {"action": "proposeBlocks"},
                script={
                    "blocks": [
                        {
                            "pageSlug": "向量空间",
                            "locator": f"{rel}#b1",
                            "preview": "向量空间 定义",
                        }
                    ]
                },
            )
            act_session(
                layout,
                {"action": "toggleBlock", "locator": f"{rel}#b1", "accepted": True},
            )
            payload = act_session(layout, {"action": "acceptBlocks"})
            self.assertEqual(payload["step"], 4)
            self.assertIsNone(whitelist_slugs(layout))
            store = load_clip_store(layout)
            round_ids = [item.id for item in store.collections if item.round_id]
            self.assertTrue(round_ids)
            self.assertTrue(any(item.id == "daily-1" for item in store.collections))
            planning = collections_for_planning(layout, store)
            self.assertEqual({item.id for item in planning}, set(round_ids))
            graph_before = wiki_graph_payload(layout)
            mapped = run_topic_map_job(
                layout,
                intent="补定义",
                map_text=json.dumps(
                    {
                        "primary": {
                            "name": "向量空间",
                            "slug": "向量空间",
                            "wikiPath": "wiki/concepts/向量空间.md",
                            "collectionIds": round_ids,
                            "blockIds": ["b1"],
                            "evidence": "[b1]",
                            "outline": [{"id": "s1", "title": "定义", "blockIds": ["b1"]}],
                        },
                        "alsoInSources": [
                            {
                                "name": "外人",
                                "slug": "outsider",
                                "wikiPath": "wiki/concepts/outsider.md",
                                "relationToPrimary": "cooccurs",
                                "collectionIds": round_ids,
                                "blockIds": ["b1"],
                                "evidence": "[b1]",
                                "outline": [],
                            }
                        ],
                        "notes": "",
                    }
                ),
            )
            slugs = {mapped["primary"]["slug"]}
            slugs.update(item["slug"] for item in mapped.get("alsoInSources") or [])
            slugs.update(item["slug"] for item in mapped.get("candidates") or [])
            self.assertIn("向量空间", slugs)
            self.assertFalse((layout.wiki / "concepts" / "向量空间.md").exists())
            graph_after = wiki_graph_payload(layout)
            self.assertEqual(graph_before["nodes"], graph_after["nodes"])
            self.assertEqual(graph_before["edges"], graph_after["edges"])
            session = load_session(layout)
            self.assertEqual(session.ingested_ids, ())

    def test_explore_enter_plan_needs_round_collections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            rel = "raw/web/sample.html"
            _write_html(layout, rel, '<p id="na-b1" data-na-block="b1">块</p>\n')
            _write_page(
                layout,
                "wiki/concepts/alive.md",
                "---\ntype: concept\ntitle: Alive\n---\n\n# Alive\n\nSee [[向量空间]].\n",
            )
            act_session(layout, {"action": "start", "mode": "explore"})
            act_session(
                layout,
                {"action": "proposeWhitelist"},
                script={
                    "pages": [
                        {
                            "slug": "向量空间",
                            "title": "向量空间",
                            "action": "add",
                            "query": "向量空间",
                        }
                    ]
                },
                use_llm=False,
            )
            act_session(layout, {"action": "acceptWhitelist"}, use_llm=False)
            with self.assertRaises(Refusal) as no_blocks:
                act_session(layout, {"action": "enterPlan"})
            self.assertEqual(no_blocks.exception.code, "search.noAcceptedBlocks")
            session = load_session(layout)
            save_clip_store(
                layout,
                ClipStore(
                    intent="",
                    collections=(
                        ClipCollection(
                            id="round-1",
                            source_key=rel,
                            source_label="sample",
                            block_ids=("b1",),
                            blocks=(
                                DerivedBlock(id="b1", tag="p", text="块", preview="块"),
                            ),
                            round_id=session.round_id,
                            target_slug="向量空间",
                        ),
                    ),
                ),
            )
            payload = act_session(layout, {"action": "enterPlan"})
            self.assertEqual(payload["step"], 4)
            self.assertEqual(whitelist_slugs(layout), ("向量空间",))
            planning = collections_for_planning(layout)
            self.assertEqual({item.id for item in planning}, {"round-1"})


@unittest.skipIf(TestClient is None, "fastapi not installed")
class DeepSearchApiTests(unittest.TestCase):
    def test_start_and_whitelist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "beta")
            page = layout.wiki / "concepts" / "alive.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]].\n",
                encoding="utf-8",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                empty = client.get("/api/projects/beta/deep-search")
                self.assertEqual(empty.status_code, 200)
                self.assertEqual(empty.json()["step"], 0)
                self.assertIn("potential", empty.json())

                graph_before = client.get("/api/projects/beta/graph")
                self.assertEqual(graph_before.status_code, 200)
                ids_before = {item["id"] for item in graph_before.json()["nodes"]}
                self.assertIn("concepts/alive", ids_before)

                started = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "start", "mode": "explore"},
                )
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.json()["mode"], "explore")
                self.assertEqual(started.json()["step"], 2)

                proposed = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "proposeWhitelist", "useLlm": False},
                )
                self.assertEqual(proposed.status_code, 200)
                slugs = [item["slug"] for item in proposed.json()["whitelist"]]
                self.assertIn("向量空间", slugs)
                overlay = {
                    (item["source"], item["target"])
                    for item in proposed.json()["overlayEdges"]
                }
                self.assertIn(("concepts/alive", "向量空间"), overlay)

                graph_after = client.get("/api/projects/beta/graph")
                self.assertEqual(graph_after.status_code, 200)
                ids_after = {item["id"] for item in graph_after.json()["nodes"]}
                self.assertEqual(ids_before, ids_after)

                toggled = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={
                        "action": "togglePage",
                        "slug": "向量空间",
                        "accepted": False,
                    },
                )
                self.assertEqual(toggled.status_code, 200)
                flagged = [
                    item
                    for item in toggled.json()["whitelist"]
                    if item["slug"] == "向量空间"
                ]
                self.assertTrue(flagged)
                self.assertFalse(flagged[0]["accepted"])

                redone = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "redo", "step": 2},
                )
                self.assertEqual(redone.status_code, 200)
                self.assertEqual(redone.json()["whitelist"], [])
                self.assertEqual(redone.json()["overlayEdges"], [])

                cleared = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "clear"},
                )
                self.assertEqual(cleared.status_code, 200)
                self.assertEqual(cleared.json()["step"], 0)

    def test_chew_propose_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "beta")
            html = layout.raw / "web" / "sample.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text(
                '<p id="na-b1" data-na-block="b1">向量空间 定义</p>\n',
                encoding="utf-8",
            )
            page = layout.wiki / "concepts" / "alive.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]].\n",
                encoding="utf-8",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                started = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "start", "mode": "chew"},
                )
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.json()["step"], 3)

                blocked = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "proposeWhitelist", "useLlm": False},
                )
                self.assertEqual(blocked.status_code, 400)

                blocks = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "proposeBlocks", "useLlm": False},
                )
                self.assertEqual(blocks.status_code, 200)
                locators = [
                    item["locator"] for item in blocks.json()["proposedBlocks"]
                ]
                self.assertIn("raw/web/sample.html#b1", locators)
                self.assertFalse(blocks.json()["proposedBlocks"][0]["accepted"])

                toggled = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={
                        "action": "toggleBlock",
                        "locator": "raw/web/sample.html#b1",
                        "accepted": True,
                    },
                )
                self.assertEqual(toggled.status_code, 200)
                saved = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "acceptBlocks"},
                )
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["step"], 4)
                self.assertEqual(saved.json()["whitelist"], [])

                ended = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "end", "leftover": "discard"},
                )
                self.assertEqual(ended.status_code, 200)
                self.assertEqual(ended.json()["step"], 0)

    def test_explore_accept_whitelist_searches_stub(self) -> None:
        def fake_search(query: str, **kwargs: object) -> WebSearchRun:
            del kwargs
            return WebSearchRun(
                query=query,
                provider="stub",
                hits=[],
                cards=[
                    CandidateCard(
                        title=f"{query} {index}",
                        url=f"https://example.test/{index}",
                        site="example.test",
                        snippet="snippet",
                        why="",
                        via="search",
                        site_name="example.test",
                    )
                    for index in range(1, 7)
                ],
            )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "beta")
            page = layout.wiki / "concepts" / "alive.md"
            page.parent.mkdir(parents=True, exist_ok=True)
            page.write_text(
                "---\ntype: concept\ntitle: Alive\n---\n\n"
                "# Alive\n\nSee [[向量空间]].\n",
                encoding="utf-8",
            )
            with patch.object(projects_mod, "REPO_ROOT", repo):
                client = TestClient(create_app())
                started = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "start", "mode": "explore"},
                )
                self.assertEqual(started.status_code, 200)
                proposed = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "proposeWhitelist", "useLlm": False},
                )
                self.assertEqual(proposed.status_code, 200)
                with patch(
                    "noteagent.acquire.run_search.run_web_search",
                    side_effect=fake_search,
                ):
                    accepted = client.post(
                        "/api/projects/beta/deep-search/act",
                        json={"action": "acceptWhitelist"},
                    )
                self.assertEqual(accepted.status_code, 200)
                body = accepted.json()
                self.assertEqual(body["step"], 3)
                self.assertTrue(body["cards"])
                per_page: dict[str, int] = {}
                for item in body["cards"]:
                    per_page[item["pageSlug"]] = per_page.get(item["pageSlug"], 0) + 1
                    self.assertFalse(item["picked"])
                self.assertLessEqual(max(per_page.values()), CARDS_PER_PAGE)
                self.assertLessEqual(len(body["cards"]), MAX_CARDS)
                cleared = client.post(
                    "/api/projects/beta/deep-search/act",
                    json={"action": "clear"},
                )
                self.assertEqual(cleared.status_code, 200)


class RoundReadsTheGraphThePromptCarriedTests(unittest.TestCase):
    """What a round's answer is weighed against: the graph the prompt carried.

    An Explore round is seeded, and its prompt holds that seed's neighbourhood rather
    than the whole project. A page outside the neighbourhood is on disk and in no
    request, so a decision about the answer may not come from it — the model could not
    have known, and the reader would see a page move between "new" and "changed" for a
    reason nothing on screen explains.
    """

    def _seeded(self, tmp: str):
        """A project where a page exists two links away from the round's seed."""
        layout = init_project(Path(tmp) / "proj")
        _write_page(
            layout,
            "wiki/concepts/seed.md",
            "---\ntype: concept\ntitle: Seed\n---\n\n# Seed\n\nSee [[Neighbour]].\n",
        )
        _write_page(
            layout,
            "wiki/concepts/faraway.md",
            "---\ntype: concept\ntitle: Faraway\n---\n\n"
            "# Faraway\n\nNothing links here yet.\n",
        )
        act_session(
            layout,
            {
                "action": "start",
                "mode": "explore",
                "seed": {"slug": "concepts/seed", "title": "Seed"},
            },
        )
        return layout

    def _answered(self, layout, payload: dict) -> dict:
        """Runs the round against a model that answers with exactly this."""
        text = json.dumps(payload, ensure_ascii=False)

        class _ScriptedClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def chat(self, messages: object) -> str:
                return text

        with patch("noteagent.wiki.deep_search.load_llm_config", return_value=object()):
            with patch("noteagent.wiki.deep_search.LlmClient", _ScriptedClient):
                return act_session(layout, {"action": "proposeWhitelist"})

    def test_a_page_the_prompt_did_not_show_is_read_as_new(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._seeded(tmp)
            payload = self._answered(
                layout,
                {
                    "pages": [
                        {"slug": "concepts/seed", "title": "Seed", "query": "seed"},
                        {
                            "slug": "concepts/faraway",
                            "title": "Faraway",
                            "query": "faraway",
                        },
                    ]
                },
            )
            actions = {item["slug"]: item["action"] for item in payload["whitelist"]}
            # In the graph the prompt carried, so the round is changing a page it knows.
            self.assertEqual(actions["concepts/seed"], "revise")
            # On disk but nowhere in the request: nothing about it reached the model.
            self.assertEqual(actions["concepts/faraway"], "add")

    def test_a_link_to_a_page_the_prompt_did_not_show_is_not_drawn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = self._seeded(tmp)
            payload = self._answered(
                layout,
                {
                    "pages": [{"slug": "concepts/seed", "title": "Seed", "query": "seed"}],
                    "edges": [
                        {"source": "concepts/seed", "target": "Neighbour"},
                        {"source": "concepts/seed", "target": "concepts/faraway"},
                    ],
                },
            )
            drawn = [(item["source"], item["target"]) for item in payload["overlayEdges"]]
            # A link between pages the prompt showed is one the reader can be given.
            self.assertIn(("concepts/seed", "Neighbour"), drawn)
            # One that names a page the model was never shown is not.
            self.assertNotIn(("concepts/seed", "concepts/faraway"), drawn)


if __name__ == "__main__":
    unittest.main()
