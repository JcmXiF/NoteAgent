"""The writing state over HTTP, and the agent's get_state summary."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.agent.tools import ToolContext
from noteagent.project.init import init_project
from noteagent.writing.state import WritingState, WritingStateStore

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
    from noteagent.api.session_access import session_tools
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]
    session_tools = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class WritingStateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        self.layout = init_project(repo / "projects" / "demo")
        patched = patch.object(projects_mod, "REPO_ROOT", repo)
        patched.start()
        self.addCleanup(patched.stop)
        projects_mod.use_project("demo")
        self.client = TestClient(
            create_app(
                AppDeps(clock=FixedClock(), ids=SequentialIds())
            )
        )

    def test_the_empty_state_is_readable(self) -> None:
        response = self.client.get("/api/projects/demo/writing/state")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["stateVersion"], 0)
        self.assertIsNone(payload["writer"])
        self.assertEqual(payload["searches"], [])
        self.assertEqual(payload["selectedPages"], [])
        self.assertEqual(payload["frames"], [])
        self.assertEqual(payload["draft"], {"pages": []})
        self.assertEqual(payload["attention"], [])

    def test_a_saved_state_is_read_back(self) -> None:
        saved = WritingStateStore(
            self.layout, clock=FixedClock()
        ).save(
            WritingState(
                searches=({"id": "s1"},),
                attention=({"kind": "fetch_failed"},),
            )
        )

        response = self.client.get("/api/projects/demo/writing/state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["stateVersion"], saved.state_version)
        self.assertEqual(payload["searches"], [{"id": "s1"}])
        self.assertEqual(payload["attention"], [{"kind": "fetch_failed"}])

    def tools(self):
        with patch(
            "noteagent.api.session_access.rag_backend", return_value=None
        ):
            return session_tools(
                AppDeps(clock=FixedClock(), ids=SequentialIds()),
                self.layout,
            )

    def test_get_state_returns_the_summary_of_an_empty_state(self) -> None:
        result = self.tools().dispatch(
            "get_state", "{}", ToolContext("s1")
        )

        self.assertFalse(result.meta.get("error"), result.content)
        summary = json.loads(result.content)
        self.assertEqual(summary["stateVersion"], 0)
        self.assertIsNone(summary["writer"])
        self.assertEqual(
            summary["areas"]["searches"], {"count": 0, "latest": None}
        )
        self.assertEqual(
            summary["areas"]["draft"], {"pages": 0, "sections": 0}
        )
        self.assertEqual(summary["attention"], [])

    def test_get_state_reflects_a_saved_state(self) -> None:
        WritingStateStore(
            self.layout, clock=FixedClock()
        ).save(
            WritingState(
                searches=({"id": "s1"},),
                selected_pages=({"id": "p1"},),
            )
        )

        result = self.tools().dispatch(
            "get_state", "{}", ToolContext("s1")
        )

        summary = json.loads(result.content)
        self.assertEqual(summary["stateVersion"], 1)
        self.assertEqual(
            summary["areas"]["searches"],
            {
                "count": 1,
                "latest": {"id": "s1", "query": "", "status": ""},
            },
        )
        self.assertEqual(
            summary["areas"]["selectedPages"], {"count": 1, "failed": 0}
        )


if __name__ == "__main__":
    unittest.main()
