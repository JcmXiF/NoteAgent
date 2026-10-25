"""Naming a conversation by hand.

A conversation names itself from its first question, which is right most of the time
and wrong exactly when the reader knows what the conversation is for — "熵的讨论"
rather than the whole opening sentence. These tests cover that one act, and what it
must leave alone: the turns, and when the conversation was last used.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from noteagent.chat.limits import default_limits
from noteagent.chat.store import (
    append_turn,
    create_conversation,
    list_conversations,
    load_conversation,
    rename_conversation,
    save_conversation,
)
from noteagent.project.init import init_project

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


class RenameConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.layout = init_project(Path(self._tmp.name) / "proj")
        self.limits = default_limits()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _asked(self, content: str = "什么是熵？"):
        created = create_conversation(self.layout, limits=self.limits)
        asked = append_turn(created, role="user", content=content, limits=self.limits)
        return save_conversation(self.layout, asked, limits=self.limits)

    def test_a_name_replaces_the_one_from_the_question(self) -> None:
        asked = self._asked()
        renamed = rename_conversation(self.layout, asked.id, "  熵的讨论  ", limits=self.limits)

        self.assertEqual(renamed.title, "熵的讨论")
        self.assertEqual(load_conversation(self.layout, asked.id).title, "熵的讨论")
        self.assertEqual(list_conversations(self.layout)[0].title, "熵的讨论")

    def test_the_turns_are_left_as_they_were(self) -> None:
        asked = self._asked()
        answered = save_conversation(
            self.layout,
            append_turn(
                asked, role="assistant", content="熵是不确定性的度量。", limits=self.limits
            ),
            limits=self.limits,
        )
        renamed = rename_conversation(self.layout, answered.id, "熵的讨论", limits=self.limits)

        self.assertEqual(
            [turn.content for turn in renamed.messages],
            ["什么是熵？", "熵是不确定性的度量。"],
        )
        self.assertEqual(
            [turn.id for turn in renamed.messages],
            [turn.id for turn in answered.messages],
        )

    def test_renaming_is_not_using_the_conversation(self) -> None:
        asked = self._asked()
        # The list is ordered by when each conversation was last used, so a rename
        # must not move it to the top as if something had been said in it.
        before = load_conversation(self.layout, asked.id).updated_at
        renamed = rename_conversation(self.layout, asked.id, "熵的讨论", limits=self.limits)
        self.assertEqual(renamed.updated_at, before)

    def test_a_name_is_one_line(self) -> None:
        asked = self._asked()
        renamed = rename_conversation(
            self.layout, asked.id, "第一行\n\n  第二行\t第三行", limits=self.limits
        )
        self.assertEqual(renamed.title, "第一行 第二行 第三行")

    def test_a_name_is_clipped_to_what_may_be_stored(self) -> None:
        asked = self._asked()
        limits = replace(self.limits, max_title_chars=8)
        renamed = rename_conversation(
            self.layout, asked.id, "一二三四五六七八九十", limits=limits
        )

        self.assertLessEqual(len(renamed.title), 8)
        self.assertNotEqual(renamed.title, "一二三四五六七八九十")
        self.assertTrue(renamed.title.endswith("…"))

    def test_clearing_the_name_gives_the_question_back(self) -> None:
        asked = self._asked("什么是条件熵？")
        rename_conversation(self.layout, asked.id, "另起一个名字", limits=self.limits)
        cleared = rename_conversation(self.layout, asked.id, "   ", limits=self.limits)

        # Not "unnamed" with a question sitting right there: the name the conversation
        # would have had is the one it goes back to.
        self.assertEqual(cleared.title, "什么是条件熵？")

    def test_an_empty_conversation_can_be_named_and_unnamed(self) -> None:
        created = create_conversation(self.layout, limits=self.limits)
        named = rename_conversation(self.layout, created.id, "以后再说", limits=self.limits)
        self.assertEqual(named.title, "以后再说")

        unnamed = rename_conversation(self.layout, created.id, "", limits=self.limits)
        self.assertEqual(unnamed.title, "")

    def test_a_conversation_that_is_not_there_cannot_be_named(self) -> None:
        with self.assertRaises(FileNotFoundError):
            rename_conversation(self.layout, "c-missing", "名字", limits=self.limits)

    def test_an_id_that_is_not_one_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            rename_conversation(self.layout, "../index", "名字", limits=self.limits)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class RenameConversationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        init_project(self.repo / "projects" / "demo")
        self.client = TestClient(create_app())
        self._patch = patch.object(projects_mod, "REPO_ROOT", self.repo)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.client.close()
        self._tmp.cleanup()

    def test_a_conversation_is_named_over_http(self) -> None:
        created = self.client.post("/api/projects/demo/chats", json={"title": ""})
        self.assertEqual(created.status_code, 200, created.text)
        ident = created.json()["id"]

        renamed = self.client.patch(
            f"/api/projects/demo/chats/{ident}", json={"title": "熵的讨论"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "熵的讨论")

        listed = self.client.get("/api/projects/demo/chats")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["conversations"][0]["title"], "熵的讨论")

    def test_naming_a_conversation_that_is_gone_is_not_found(self) -> None:
        renamed = self.client.patch(
            "/api/projects/demo/chats/c-missing", json={"title": "名字"}
        )
        self.assertEqual(renamed.status_code, 404, renamed.text)

    def test_a_name_that_is_not_text_is_refused(self) -> None:
        created = self.client.post("/api/projects/demo/chats", json={"title": ""})
        ident = created.json()["id"]
        renamed = self.client.patch(f"/api/projects/demo/chats/{ident}", json={"title": 7})
        # The request body says what a name is; a number is not one.
        self.assertEqual(renamed.status_code, 422, renamed.text)


if __name__ == "__main__":
    unittest.main()
