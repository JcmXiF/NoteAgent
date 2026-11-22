"""The writing skill is a per-request context source, not a tool."""

from __future__ import annotations

import unittest

from agent_fakes import FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.projection import project
from noteagent.api.app import create_app
from noteagent.api.deps import AppDeps
from noteagent.prompts.writing_skill import (
    WRITING_SECTION,
    select_writing_skill,
    sync_writing_skill,
    writing_skill_text,
)

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]


def _sections(store: FakeSessionStore, session_id: str) -> dict[str, str]:
    return project(store.load(session_id).entries).sections


class WritingSkillSelectionTests(unittest.TestCase):
    def test_a_writing_request_selects_the_skill(self) -> None:
        self.assertTrue(
            select_writing_skill("帮我写一篇关于熵的 wiki")
        )
        self.assertTrue(select_writing_skill("更新这一页"))
        self.assertTrue(select_writing_skill("/writing 熵"))
        self.assertFalse(select_writing_skill("熵和交叉熵的区别是什么？"))
        self.assertFalse(select_writing_skill("把结果整理成表格"))

    def test_the_skill_is_a_clear_recipe(self) -> None:
        text = writing_skill_text()

        self.assertIn("compose_wiki", text)
        self.assertIn("write_wiki", text)
        self.assertIn("cites the frame", text)
        self.assertIn("stale frame", text)

    def test_the_skill_is_added_once_and_removed_later(self) -> None:
        store = FakeSessionStore()
        store.create("s1")

        self.assertTrue(
            sync_writing_skill(store, "s1", "写一篇 wiki")
        )
        self.assertIn(WRITING_SECTION, _sections(store, "s1"))
        self.assertFalse(
            sync_writing_skill(store, "s1", "写一篇 wiki")
        )

        self.assertTrue(
            sync_writing_skill(store, "s1", "熵是什么？")
        )
        self.assertNotIn(WRITING_SECTION, _sections(store, "s1"))

    def test_an_override_forces_the_skill_on_or_off(self) -> None:
        store = FakeSessionStore()
        store.create("s1")

        self.assertFalse(
            sync_writing_skill(
                store, "s1", "写一篇 wiki", enabled=False
            )
        )
        self.assertTrue(
            sync_writing_skill(
                store, "s1", "熵是什么", enabled=True
            )
        )
        self.assertIn(WRITING_SECTION, _sections(store, "s1"))


@unittest.skipIf(TestClient is None, "fastapi not installed")
class WritingSkillQueryTests(unittest.TestCase):
    def client(self, **options) -> "TestClient":
        deps = AppDeps(
            sessions=options.pop("sessions", FakeSessionStore()),
            model=FauxModelClient.text("已读。"),
            clock=FixedClock(),
            ids=SequentialIds(),
            **options,
        )
        return TestClient(create_app(deps))

    def test_a_product_session_adds_and_removes_the_skill(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        client = self.client(
            sessions=store, seed_system_sections=True
        )

        first = client.post(
            "/api/v1/sessions/s1/query",
            json={"content": "帮我写一篇关于熵的 wiki"},
        )

        self.assertEqual(first.status_code, 200, first.text)
        sections = _sections(store, "s1")
        self.assertIn(WRITING_SECTION, sections)
        self.assertIn("write_wiki", sections[WRITING_SECTION])

        client.post(
            "/api/v1/sessions/s1/query",
            json={"content": "熵是什么？"},
        )
        self.assertNotIn(WRITING_SECTION, _sections(store, "s1"))

    def test_a_question_does_not_add_the_recipe(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        client = self.client(
            sessions=store, seed_system_sections=True
        )

        client.post(
            "/api/v1/sessions/s1/query",
            json={"content": "熵是什么？"},
        )

        sections = _sections(store, "s1")
        self.assertNotIn(
            "write_wiki", sections.get(WRITING_SECTION, "")
        )

    def test_an_eval_harness_session_is_not_touched(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        client = self.client(
            sessions=store, seed_system_sections=False
        )

        client.post(
            "/api/v1/sessions/s1/query",
            json={"content": "帮我写一篇关于熵的 wiki"},
        )

        self.assertNotIn(WRITING_SECTION, _sections(store, "s1"))

    def test_the_override_controls_the_skill(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        self.client(sessions=store, writing_skill=True).post(
            "/api/v1/sessions/s1/query",
            json={"content": "熵是什么？"},
        )
        self.assertIn(WRITING_SECTION, _sections(store, "s1"))


if __name__ == "__main__":
    unittest.main()
