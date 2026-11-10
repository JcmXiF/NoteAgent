"""The session list and its create/rename/delete, including old-chat migration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds, old_chat_payload
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class SessionCrudApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.layout = ProjectLayout(Path(self._tmp.name))
        self.store = JsonlSessionStore(
            self.layout, clock=FixedClock(), ids=SequentialIds()
        )
        self.deps = AppDeps(
            sessions=self.store,
            clock=FixedClock(),
            ids=SequentialIds(),
        )

    def client(self) -> "TestClient":
        return TestClient(create_app(self.deps))

    def test_create_list_rename_and_delete(self) -> None:
        client = self.client()

        empty = client.get("/api/v1/sessions")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), {"sessions": []})

        created = client.post(
            "/api/v1/sessions", json={"title": "First"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        session_id = created.json()["id"]
        self.assertEqual(created.json()["title"], "First")

        listed = client.get("/api/v1/sessions").json()["sessions"]
        self.assertEqual([item["id"] for item in listed], [session_id])
        self.assertEqual(listed[0]["title"], "First")

        renamed = client.patch(
            f"/api/v1/sessions/{session_id}", json={"title": "Renamed"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "Renamed")
        self.assertEqual(
            client.get("/api/v1/sessions").json()["sessions"][0]["title"],
            "Renamed",
        )

        removed = client.delete(f"/api/v1/sessions/{session_id}")
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(
            client.get("/api/v1/sessions").json()["sessions"], []
        )
        self.assertFalse(self.layout.session_path(session_id).exists())

    def test_a_missing_session_is_not_found(self) -> None:
        client = self.client()

        renamed = client.patch(
            "/api/v1/sessions/missing", json={"title": "x"}
        )
        removed = client.delete("/api/v1/sessions/missing")

        self.assertEqual(renamed.status_code, 404)
        self.assertEqual(renamed.json()["code"], "session.notFound")
        self.assertEqual(removed.status_code, 404)
        self.assertEqual(removed.json()["code"], "session.notFound")

    def test_a_reserved_id_is_a_bad_request(self) -> None:
        response = self.client().get("/api/v1/sessions/index")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "session.invalidId")

    def test_a_running_session_is_not_deleted(self) -> None:
        client = self.client()
        session_id = client.post(
            "/api/v1/sessions", json={"title": "Busy"}
        ).json()["id"]
        self.deps.runs.claim(session_id)
        try:
            response = client.delete(f"/api/v1/sessions/{session_id}")
        finally:
            self.deps.runs.release(session_id)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "busy")
        self.assertTrue(self.layout.session_path(session_id).exists())

    def test_the_index_is_rebuilt_when_it_is_removed(self) -> None:
        client = self.client()
        client.post("/api/v1/sessions", json={"title": "First"})
        client.get("/api/v1/sessions")
        index = self.layout.sessions_dir / "index.json"
        self.assertTrue(index.is_file())
        index.unlink()

        listed = client.get("/api/v1/sessions").json()["sessions"]

        self.assertEqual(len(listed), 1)
        self.assertTrue(index.is_file())

    def test_old_chats_are_migrated_and_reported(self) -> None:
        self.layout.chats_dir.mkdir(parents=True, exist_ok=True)
        (self.layout.chats_dir / "c-1.json").write_text(
            json.dumps(old_chat_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        (self.layout.chats_dir / "c-bad.json").write_text(
            "{not json", encoding="utf-8"
        )
        client = self.client()

        response = client.get("/api/v1/sessions")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(
            [item["id"] for item in body["sessions"]], ["c-1"]
        )
        self.assertEqual(body["migration"]["migrated"], 1)
        self.assertEqual(
            [item["id"] for item in body["migration"]["failed"]], ["c-bad"]
        )
        opened = client.get("/api/v1/sessions/c-1")
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertEqual(opened.json()["title"], "Entropy")
        assistant = next(
            item
            for item in opened.json()["messages"]
            if item["role"] == "assistant"
        )
        self.assertEqual(
            assistant["cards"],
            [
                {
                    "tool": "wiki_read",
                    "title": "Entropy",
                    "slug": "concepts/entropy",
                }
            ],
        )
        self.assertTrue(
            (self.layout.chats_dir / "c-bad.json").is_file()
        )
