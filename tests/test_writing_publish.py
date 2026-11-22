"""write_wiki publishes the state draft through the page-write seam."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event

from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import ToolContext
from noteagent.effects.journal import (
    COMMITTED,
    WIKI_PAGE_WRITE,
    EffectJournal,
)
from noteagent.ingest.frontmatter import parse_inline_list, parse_page
from noteagent.ingest.page_history import page_snapshots
from noteagent.ingest.source_citations import (
    list_source_citations,
    parse_clip_sections,
)
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore, draft_pages
from writing_fixtures import (
    prepare_draft,
    write_draft,
    writing_tools,
)

WIKI_FILE = "wiki/concepts/信息熵.md"


class WritingPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def prepared(self, tools, *, body: str = "熵是信息的平均量。"):
        return prepare_draft(
            tools,
            self.layout,
            wiki_file=WIKI_FILE,
            body=body,
        )

    def write(self, tools, draft_version: int):
        return write_draft(
            tools,
            wiki_file=WIKI_FILE,
            draft_version=draft_version,
        )

    def wiki_path(self) -> Path:
        return self.layout.root.joinpath(*WIKI_FILE.split("/"))

    def test_publish_writes_the_page_with_sources_and_markers(self) -> None:
        tools = writing_tools(self.layout)
        page_id, frame_id, draft_version = self.prepared(tools)

        result = self.write(tools, draft_version)

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["effectIds"]), 1)
        effect_id = payload["effectIds"][0]
        self.assertTrue(payload["writtenAt"])

        wiki = self.wiki_path()
        self.assertTrue(wiki.is_file())
        text = wiki.read_text(encoding="utf-8")
        parsed = parse_page(text)
        self.assertIn("信息熵", parsed.fields.get("title", ""))
        page_state = WritingStateStore(self.layout).load().selected_pages[0]
        material = str(page_state["material"])
        sources = parse_inline_list(parsed.fields.get("sources", ""))
        self.assertEqual(
            sources, [f"{material}#b2", f"{material}#b3"]
        )
        self.assertIn(f"<!-- na-clip:{frame_id} b2,b3 -->", text)
        self.assertIn("## 定义", text)
        self.assertIn("熵是信息的平均量。", text)
        self.assertTrue((self.layout.root / material).is_file())

        marks = parse_clip_sections(parsed.body)
        self.assertEqual(marks[0].collection_id, frame_id)
        self.assertEqual(marks[0].block_ids, ("b2", "b3"))

        state = WritingStateStore(self.layout).load()
        page = draft_pages(state.draft)[0]
        self.assertEqual(page["status"], "written")
        self.assertEqual(page["effectIds"], [effect_id])

        effects = EffectJournal(self.layout).query(
            kind=WIKI_PAGE_WRITE
        )
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].status, COMMITTED)
        self.assertEqual(effects[0].target, WIKI_FILE)
        self.assertEqual(effects[0].ident, effect_id)
        history = page_snapshots(self.layout, WIKI_FILE)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].effect_id, effect_id)

    def test_publishing_the_same_version_twice_is_idempotent(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        first = json.loads(self.write(tools, draft_version).content)
        before = self.wiki_path().read_text(encoding="utf-8")

        second = self.write(tools, draft_version)

        payload = json.loads(second.content)
        self.assertEqual(payload["effectIds"], first["effectIds"])
        self.assertEqual(payload["writtenAt"], first["writtenAt"])
        self.assertEqual(
            self.wiki_path().read_text(encoding="utf-8"), before
        )
        self.assertEqual(
            len(
                EffectJournal(self.layout).query(
                    kind=WIKI_PAGE_WRITE
                )
            ),
            1,
        )
        self.assertEqual(
            len(page_snapshots(self.layout, WIKI_FILE)), 1
        )

    def test_the_marker_resolves_to_the_raw_source(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, frame_id, draft_version = self.prepared(tools)
        self.write(tools, draft_version)
        state = WritingStateStore(self.layout).load()
        material = str(state.selected_pages[0]["material"])

        index = list_source_citations(self.layout)

        marks = index.marks.get(material, [])
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0].path, WIKI_FILE)
        self.assertEqual(marks[0].heading, "定义")
        self.assertEqual(marks[0].collection_id, frame_id)
        self.assertEqual(marks[0].block_ids, ("b2", "b3"))

    def test_a_pinned_block_missing_from_the_page_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        sections = [dict(section) for section in page["sections"]]
        sections[0] = {
            **sections[0],
            "citations": [
                {
                    "frameId": frame_id,
                    "frameVersion": 1,
                    "blockIds": ["b99"],
                }
            ],
        }
        page["sections"] = sections
        store.save(replace(state, draft={"pages": [page]}))

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such block", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_a_missing_frame_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        store.save(replace(store.load(), frames=()))

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such frame", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_an_existing_frontmatter_title_is_kept(self) -> None:
        target = self.wiki_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\n"
            "type: concept\n"
            "title: 自定义标题\n"
            "created: 2020-01-01\n"
            "updated: 2020-01-01\n"
            "sources: []\n"
            "---\n\n"
            "# 旧标题\n",
            encoding="utf-8",
        )
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)

        self.write(tools, draft_version)

        text = target.read_text(encoding="utf-8")
        parsed = parse_page(text)
        self.assertIn("自定义标题", parsed.fields.get("title", ""))
        self.assertIn("type: concept", text)
        self.assertIn("sources: [raw/", text)

    def test_an_empty_section_body_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(
            tools, body=""
        )

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("has no body", result.content)
        self.assertFalse(self.wiki_path().exists())
        page = draft_pages(
            WritingStateStore(self.layout).load().draft
        )[0]
        self.assertEqual(page["status"], "draft")

    def test_a_missing_source_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        page_id, _frame_id, draft_version = self.prepared(tools)
        source = (
            self.layout.writing_pages / f"{page_id}.material.html"
        )
        source.unlink()

        result = self.write(tools, draft_version)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("original", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_a_stale_draft_version_is_refused(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, _draft_version = self.prepared(tools)

        result = self.write(tools, 0)

        self.assertTrue(result.meta.get("error"))
        self.assertIn("version", result.content)
        self.assertFalse(self.wiki_path().exists())

    def test_a_legacy_draft_key_publishes_to_the_canonical_page(
        self,
    ) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        page["wikiFile"] = "concepts/信息熵"
        store.save(replace(state, draft={"pages": [page]}))

        result = write_draft(
            tools,
            wiki_file="concepts/信息熵",
            draft_version=draft_version,
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["wikiFile"], WIKI_FILE)
        self.assertTrue(self.wiki_path().is_file())

    def test_the_write_handler_checks_cancel_before_the_page_write(
        self,
    ) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        cancel = Event()
        cancel.set()
        spec = tools.get("write_wiki")

        with self.assertRaises(RunCancelled):
            spec.handler(
                {
                    "wikiFile": WIKI_FILE,
                    "draftVersion": draft_version,
                },
                ToolContext("s1", cancel=cancel),
            )

        self.assertFalse(self.wiki_path().exists())

    def test_a_cancelled_write_leaves_no_page(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = self.prepared(tools)
        cancel = Event()
        cancel.set()

        with self.assertRaises(RunCancelled):
            tools.dispatch(
                "write_wiki",
                json.dumps(
                    {
                        "wikiFile": WIKI_FILE,
                        "draftVersion": draft_version,
                    }
                ),
                ToolContext("s1", cancel=cancel),
            )

        self.assertFalse(self.wiki_path().exists())
        page = WritingStateStore(self.layout).load().draft
        status = page["pages"][0]["status"] if page["pages"] else ""
        self.assertEqual(status, "draft")

    def test_a_slug_publishes_to_the_canonical_page(self) -> None:
        tools = writing_tools(self.layout)
        _page_id, _frame_id, draft_version = prepare_draft(
            tools,
            self.layout,
            wiki_file="concepts/信息熵",
            body="熵是信息的平均量。",
        )

        result = write_draft(
            tools,
            wiki_file="concepts/信息熵",
            draft_version=draft_version,
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["wikiFile"], WIKI_FILE)
        self.assertTrue(self.wiki_path().is_file())


if __name__ == "__main__":
    unittest.main()
