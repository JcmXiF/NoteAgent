"""compose_wiki builds the structured draft from frames."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.acquire.types import CandidateCard, Capture
from noteagent.agent.runtime import RunCancelled
from noteagent.agent.tools import ToolContext
from noteagent.api.deps import AppDeps
from noteagent.api.session_access import session_tools
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore, draft_pages, draft_sections

PAGE_HTML = (
    "<html><body><article>"
    + "".join(
        f'<p data-na-block="b{index}">段落 {index}</p>'
        for index in range(1, 5)
    )
    + "</article></body></html>"
)
WIKI_FILE = "wiki/concepts/信息熵.md"


def _cards(_query: str) -> list[CandidateCard]:
    return [
        CandidateCard(
            title="信息熵",
            url="https://example.com/entropy",
            site="example.com",
            snippet="熵是信息的平均量。",
            why="",
            via="test",
        )
    ]


def _capture(_url: str) -> Capture:
    return Capture(
        url="https://example.com/entropy",
        title="信息熵",
        markdown="段落 1 段落 2 段落 3 段落 4",
        html=PAGE_HTML,
        capture_method="test",
        fidelity="ok",
        reasons=(),
        http_status=200,
        char_count=100,
    )


class WritingComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = init_project(Path(self._tmp.name) / "demo")

    def tools(self):
        with patch(
            "noteagent.api.session_access.rag_backend", return_value=None
        ):
            return session_tools(
                AppDeps(
                    clock=FixedClock(),
                    ids=SequentialIds(),
                    web_search=_cards,
                    web_fetch=_capture,
                ),
                self.layout,
            )

    def frame(self, tools) -> str:
        tools.dispatch(
            "web_search",
            json.dumps({"query": "信息熵"}),
            ToolContext("s1"),
        )
        state = WritingStateStore(self.layout).load()
        hit_id = str(state.searches[0]["hits"][0]["id"])
        page = tools.dispatch(
            "select_page",
            json.dumps({"hitId": hit_id}),
            ToolContext("s1"),
        )
        page_id = str(json.loads(page.content)["pageId"])
        result = tools.dispatch(
            "select_frame",
            json.dumps({"pageId": page_id, "blockIds": ["b2", "b3"]}),
            ToolContext("s1"),
        )
        return str(json.loads(result.content)["frameId"])

    def compose(
        self,
        tools,
        sections,
        *,
        base: int = 0,
        wiki: str = WIKI_FILE,
        overwrite: bool = False,
    ):
        body = {
            "wikiFile": wiki,
            "baseDraftVersion": base,
            "sections": sections,
        }
        if overwrite:
            body["overwrite"] = True
        return tools.dispatch(
            "compose_wiki",
            json.dumps(body),
            ToolContext("s1"),
        )

    def test_a_person_edited_section_is_refused_until_overwrite(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "旧正文",
                    "citations": [frame_id],
                }
            ],
        )
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        sections = [dict(section) for section in page["sections"]]
        sections[0] = {
            **sections[0],
            "body": "人工改过",
            "version": 2,
            "humanEdited": True,
        }
        page["sections"] = sections
        page["version"] = 2
        store.save(replace(state, draft={"pages": [page]}))

        refused = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "新正文",
                    "citations": [frame_id],
                    "baseSectionVersion": 2,
                }
            ],
            base=2,
        )

        self.assertTrue(refused.meta.get("error"))
        self.assertIn("edited by a person", refused.content)
        applied = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "新正文",
                    "citations": [frame_id],
                    "baseSectionVersion": 2,
                }
            ],
            base=2,
            overwrite=True,
        )
        self.assertFalse(applied.meta.get("error"), applied.content)
        saved = draft_sections(
            draft_pages(WritingStateStore(self.layout).load().draft)[0]
        )[0]
        self.assertEqual(saved["body"], "新正文")
        self.assertFalse(saved["humanEdited"])

    def test_a_section_is_composed_into_a_new_draft(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "熵是信息的平均量。",
                    "citations": [frame_id],
                }
            ],
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["draftVersion"], 1)
        self.assertEqual(payload["sections"][0]["id"], "definition")
        self.assertEqual(payload["sections"][0]["version"], 1)

        state = WritingStateStore(self.layout).load()
        pages = draft_pages(state.draft)
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page["wikiFile"], WIKI_FILE)
        self.assertEqual(page["title"], "信息熵")
        self.assertEqual(page["version"], 1)
        self.assertEqual(page["status"], "draft")
        sections = draft_sections(page)
        self.assertEqual(sections[0]["heading"], "定义")
        self.assertEqual(sections[0]["body"], "熵是信息的平均量。")
        citation = sections[0]["citations"][0]
        self.assertEqual(citation["frameId"], frame_id)
        self.assertEqual(citation["frameVersion"], 1)
        self.assertEqual(citation["blockIds"], ["b2", "b3"])

        listing = json.loads(
            tools.dispatch(
                "get_state",
                json.dumps({"section": "draft"}),
                ToolContext("s1"),
            ).content
        )
        self.assertEqual(listing[0]["wikiFile"], WIKI_FILE)
        self.assertEqual(
            [item["id"] for item in listing[0]["sections"]],
            ["definition"],
        )
        detail = json.loads(
            tools.dispatch(
                "get_state",
                json.dumps(
                    {
                        "section": "section",
                        "wikiFile": WIKI_FILE,
                        "sectionId": "definition",
                    }
                ),
                ToolContext("s1"),
            ).content
        )
        self.assertEqual(detail["body"], "熵是信息的平均量。")
        self.assertEqual(detail["citations"][0]["frameId"], frame_id)

    def test_an_existing_section_is_replaced_in_place(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "旧正文",
                    "citations": [frame_id],
                }
            ],
        )
        self.compose(
            tools,
            [
                {
                    "id": "properties",
                    "heading": "性质",
                    "body": "新增",
                    "citations": [frame_id],
                }
            ],
            base=1,
        )

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "新正文",
                    "citations": [frame_id],
                    "baseSectionVersion": 1,
                }
            ],
            base=2,
        )

        self.assertFalse(result.meta.get("error"), result.content)
        payload = json.loads(result.content)
        self.assertEqual(payload["draftVersion"], 3)
        self.assertEqual(payload["sections"][0]["version"], 2)
        page = draft_pages(WritingStateStore(self.layout).load().draft)[0]
        sections = draft_sections(page)
        self.assertEqual([item["id"] for item in sections], ["definition", "properties"])
        self.assertEqual(sections[0]["body"], "新正文")
        self.assertEqual(sections[0]["version"], 2)
        self.assertEqual(sections[1]["version"], 1)

    def test_a_new_section_is_inserted_after_its_anchor(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                }
            ],
        )

        self.compose(
            tools,
            [
                {
                    "id": "properties",
                    "heading": "性质",
                    "body": "二",
                    "citations": [frame_id],
                    "after": "definition",
                },
                {
                    "id": "examples",
                    "heading": "例子",
                    "body": "三",
                    "citations": [frame_id],
                    "after": "properties",
                },
            ],
            base=1,
        )

        page = draft_pages(WritingStateStore(self.layout).load().draft)[0]
        self.assertEqual(
            [item["id"] for item in draft_sections(page)],
            ["definition", "properties", "examples"],
        )

    def test_a_duplicate_section_id_is_an_error(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                },
                {
                    "id": "definition",
                    "heading": "定义二",
                    "body": "二",
                    "citations": [frame_id],
                },
            ],
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("duplicate section id", result.content)
        self.assertEqual(draft_pages(WritingStateStore(self.layout).load().draft), [])

    def test_an_unknown_after_is_an_error(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                    "after": "missing",
                }
            ],
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("insert after", result.content)
        self.assertEqual(draft_pages(WritingStateStore(self.layout).load().draft), [])

    def test_an_unknown_frame_is_an_error(self) -> None:
        tools = self.tools()

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": ["frame-missing"],
                }
            ],
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("no such frame", result.content)
        self.assertEqual(draft_pages(WritingStateStore(self.layout).load().draft), [])

    def test_a_stale_draft_version_is_an_error(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                }
            ],
        )

        result = self.compose(
            tools,
            [
                {
                    "id": "properties",
                    "heading": "性质",
                    "body": "二",
                    "citations": [frame_id],
                }
            ],
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("version", result.content)
        page = draft_pages(WritingStateStore(self.layout).load().draft)[0]
        self.assertEqual(page["version"], 1)
        self.assertEqual([item["id"] for item in draft_sections(page)], ["definition"])

    def test_a_stale_section_version_is_an_error(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                }
            ],
        )

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "二",
                    "citations": [frame_id],
                    "baseSectionVersion": 0,
                }
            ],
            base=1,
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn(
            "section definition is at version 1", result.content
        )
        self.assertIn("you read 0", result.content)
        page = draft_pages(WritingStateStore(self.layout).load().draft)[0]
        section = draft_sections(page)[0]
        self.assertEqual(section["body"], "一")
        self.assertEqual(section["version"], 1)

    def test_replacing_a_section_without_its_version_is_refused(
        self,
    ) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "一",
                    "citations": [frame_id],
                }
            ],
        )

        result = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "二",
                    "citations": [frame_id],
                }
            ],
            base=1,
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("pass baseSectionVersion 1", result.content)
        forced = self.compose(
            tools,
            [
                {
                    "id": "definition",
                    "heading": "定义",
                    "body": "二",
                    "citations": [frame_id],
                }
            ],
            base=1,
            overwrite=True,
        )
        self.assertTrue(forced.meta.get("error"))
        self.assertIn(
            "pass baseSectionVersion 1", forced.content
        )
        page = draft_pages(WritingStateStore(self.layout).load().draft)[0]
        self.assertEqual(draft_sections(page)[0]["body"], "一")

    def test_compose_requires_base_draft_version(self) -> None:
        tools = self.tools()

        result = tools.dispatch(
            "compose_wiki",
            json.dumps(
                {
                    "wikiFile": WIKI_FILE,
                    "sections": [
                        {"id": "definition", "heading": "定义"}
                    ],
                }
            ),
            ToolContext("s1"),
        )

        self.assertTrue(result.meta.get("error"))
        self.assertIn("baseDraftVersion", result.content)

    def test_spellings_of_one_page_share_one_draft(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        section = {
            "id": "definition",
            "heading": "定义",
            "body": "熵是信息的平均量。",
            "citations": [frame_id],
        }

        first = self.compose(tools, [section], wiki="concepts/信息熵")
        self.assertFalse(first.meta.get("error"), first.content)
        self.assertEqual(
            json.loads(first.content)["wikiFile"], WIKI_FILE
        )

        second = self.compose(
            tools,
            [{**section, "baseSectionVersion": 1}],
            base=1,
            wiki="[[wiki/concepts/信息熵.md]]",
        )
        self.assertFalse(second.meta.get("error"), second.content)

        pages = draft_pages(WritingStateStore(self.layout).load().draft)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["wikiFile"], WIKI_FILE)

    def test_an_old_draft_key_moves_to_the_canonical_key(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        section = {
            "id": "definition",
            "heading": "定义",
            "body": "熵是信息的平均量。",
            "citations": [frame_id],
        }
        self.compose(tools, [section])
        store = WritingStateStore(self.layout)
        state = store.load()
        page = dict(draft_pages(state.draft)[0])
        page["wikiFile"] = "concepts/信息熵"
        store.save(replace(state, draft={"pages": [page]}))

        result = self.compose(
            tools,
            [{**section, "baseSectionVersion": 1}],
            base=1,
            wiki="concepts/信息熵",
        )

        self.assertFalse(result.meta.get("error"), result.content)
        pages = draft_pages(WritingStateStore(self.layout).load().draft)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["wikiFile"], WIKI_FILE)

    def test_the_compose_handler_checks_cancel_before_the_write(
        self,
    ) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        cancel = Event()
        cancel.set()
        spec = tools.get("compose_wiki")

        with self.assertRaises(RunCancelled):
            spec.handler(
                {
                    "wikiFile": WIKI_FILE,
                    "baseDraftVersion": 0,
                    "sections": [
                        {
                            "id": "definition",
                            "heading": "定义",
                            "body": "正文",
                            "citations": [frame_id],
                        }
                    ],
                },
                ToolContext("s1", cancel=cancel),
            )

        self.assertEqual(
            list(
                draft_pages(
                    WritingStateStore(self.layout).load().draft
                )
            ),
            [],
        )

    def test_a_cancelled_compose_writes_nothing(self) -> None:
        tools = self.tools()
        frame_id = self.frame(tools)
        cancel = Event()
        cancel.set()

        with self.assertRaises(RunCancelled):
            tools.dispatch(
                "compose_wiki",
                json.dumps(
                    {
                        "wikiFile": WIKI_FILE,
                        "baseDraftVersion": 0,
                        "sections": [
                            {
                                "id": "definition",
                                "heading": "定义",
                                "body": "正文",
                                "citations": [frame_id],
                            }
                        ],
                    }
                ),
                ToolContext("s1", cancel=cancel),
            )

        self.assertEqual(
            list(
                draft_pages(
                    WritingStateStore(self.layout).load().draft
                )
            ),
            [],
        )

    def test_an_invalid_wiki_file_names_the_right_form(self) -> None:
        tools = self.tools()
        for wiki in ("../secret", "/etc/passwd", "C:/secret"):
            with self.subTest(wiki=wiki):
                result = self.compose(
                    tools,
                    [{"id": "definition", "heading": "定义"}],
                    wiki=wiki,
                )

                self.assertTrue(result.meta.get("error"))
                self.assertIn("concepts/信息熵", result.content)


if __name__ == "__main__":
    unittest.main()
