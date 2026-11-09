"""The versioned session view: transcript plus the state derived from the log."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FakeSessionStore
from noteagent.agent.kernel import RunRegistry
from noteagent.project.init import init_project
from noteagent.sessions.jsonl import JsonlSessionStore

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _session_entries() -> list[dict[str, object]]:
    return [
        {
            "type": "system_section",
            "sections": {"identity": "你是 NoteAgent。", "tools": "wiki_read"},
        },
        {"type": "message", "role": "user", "content": "什么是熵？"},
        {
            "type": "message",
            "role": "assistant",
            "content": "我先查一下。",
            "toolCalls": [
                {
                    "id": "t1",
                    "name": "wiki_read",
                    "arguments": {"targets": ["concepts/entropy"]},
                }
            ],
        },
        {
            "type": "message",
            "role": "toolResult",
            "toolCallId": "t1",
            "name": "wiki_read",
            "content": "熵是……",
        },
        {
            "type": "message",
            "role": "assistant",
            "content": "熵衡量不确定性。",
            "stopReason": "stop",
            "meta": {"cards": [{"title": "熵"}]},
        },
    ]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class SessionApiTests(unittest.TestCase):
    def test_the_session_view_returns_the_transcript_and_derived_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            layout = init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                store = JsonlSessionStore(layout)
                store.create("s1", "Entropy")
                for entry in _session_entries():
                    store.append("s1", entry)

                client = TestClient(create_app())
                response = client.get("/api/v1/sessions/s1")

            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["id"], "s1")
            self.assertEqual(body["title"], "Entropy")
            self.assertEqual(
                [(item["role"], item["content"], item.get("isStep", False)) for item in body["messages"]],
                [
                    ("user", "什么是熵？", False),
                    ("assistant", "我先查一下。", True),
                    ("assistant", "熵衡量不确定性。", False),
                ],
            )
            self.assertEqual(body["messages"][2]["cards"], [{"title": "熵"}])
            self.assertEqual(
                body["derived"]["sections"],
                {"identity": "你是 NoteAgent。", "tools": "wiki_read"},
            )
            self.assertEqual(
                [message["role"] for message in body["derived"]["messages"]],
                ["user", "assistant", "tool", "assistant"],
            )

    def test_a_missing_session_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app())
                response = client.get("/api/v1/sessions/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "session.notFound")

    def test_a_session_id_that_is_not_a_safe_name_is_a_bad_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            init_project(repo / "projects" / "demo")
            with patch.object(projects_mod, "REPO_ROOT", repo):
                projects_mod.use_project("demo")
                client = TestClient(create_app())
                response = client.get("/api/v1/sessions/index")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "session.invalidId")

    def test_an_injected_store_is_used_without_an_active_project(self) -> None:
        store = FakeSessionStore()
        store.create("s1", "Injected")
        store.append("s1", {"type": "message", "role": "user", "content": "hello"})

        client = TestClient(create_app(AppDeps(sessions=store)))
        response = client.get("/api/v1/sessions/s1")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["title"], "Injected")
        self.assertEqual(response.json()["messages"][0]["content"], "hello")

    def test_the_view_says_whether_the_session_is_running(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        runs = RunRegistry()
        client = TestClient(create_app(AppDeps(sessions=store, runs=runs)))

        idle = client.get("/api/v1/sessions/s1").json()

        runs.claim("s1")
        try:
            running = client.get("/api/v1/sessions/s1").json()
        finally:
            runs.release("s1")

        self.assertEqual(idle["state"], {"busy": False})
        self.assertEqual(running["state"], {"busy": True})

    def test_an_unknown_entry_type_is_reported_in_the_derived_state(self) -> None:
        store = FakeSessionStore()
        store.create("s1")
        store.append(
            "s1", {"id": "e1", "type": "future_thing", "payload": {}}
        )

        client = TestClient(create_app(AppDeps(sessions=store)))
        body = client.get("/api/v1/sessions/s1").json()

        self.assertEqual(
            body["derived"]["diagnostics"],
            [
                {
                    "kind": "unknown_type",
                    "entryId": "e1",
                    "message": "unknown entry type: future_thing",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
