"""The writing state's save, lease, conflict and archive over HTTP."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.project.init import init_project
from noteagent.writing.state import WritingStateStore
from test_writing_edit import PAGE, _state

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


@unittest.skipIf(TestClient is None, "fastapi not installed")
class WritingSaveApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        self.layout = init_project(repo / "projects" / "demo")
        patched = patch.object(projects_mod, "REPO_ROOT", repo)
        patched.start()
        self.addCleanup(patched.stop)
        projects_mod.use_project("demo")
        self.clock = FixedClock()
        self.store = WritingStateStore(self.layout, clock=self.clock)
        self.client = TestClient(
            create_app(AppDeps(clock=self.clock, ids=SequentialIds()))
        )

    def changes(self) -> dict[str, object]:
        page = {
            **PAGE,
            "sections": [
                {**PAGE["sections"][0], "body": "新正文"},
                PAGE["sections"][1],
            ],
        }
        return {
            "searches": [
                {
                    "id": "s1",
                    "query": "q",
                    "status": "ok",
                    "hits": [
                        {
                            "id": "h1",
                            "title": "改过的标题",
                            "url": "u",
                            "snippet": "s",
                            "rank": 1,
                        }
                    ],
                }
            ],
            "draft": {"pages": [page]},
        }

    def test_a_save_applies_the_change_set(self) -> None:
        self.store.save(_state())

        response = self.client.post(
            "/api/projects/demo/writing/state",
            json={"baseVersion": 1, "changes": self.changes()},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["stateVersion"], 2)
        section = payload["draft"]["pages"][0]["sections"][0]
        self.assertEqual(section["body"], "新正文")
        self.assertTrue(section["humanEdited"])
        self.assertEqual(
            payload["searches"][0]["hits"][0]["title"], "改过的标题"
        )

    def test_a_stale_base_version_is_a_conflict(self) -> None:
        self.store.save(_state())

        response = self.client.post(
            "/api/projects/demo/writing/state",
            json={"baseVersion": 0, "changes": self.changes()},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "state.conflict")
        self.assertEqual(
            response.json()["params"]["stateVersion"], "1"
        )
        self.assertEqual(
            self.store.load().searches[0]["hits"][0]["title"],
            "旧标题",
        )

    def test_a_save_while_a_run_holds_the_state_is_busy(self) -> None:
        self.store.save(_state())
        held = self.store.ensure_writer("agent", "run-1")

        response = self.client.post(
            "/api/projects/demo/writing/state",
            json={
                "baseVersion": held.state_version,
                "changes": self.changes(),
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "state.busy")
        self.assertEqual(
            self.store.load().searches[0]["hits"][0]["title"],
            "旧标题",
        )

    def test_archive_puts_the_state_away_and_activate_restores_it(
        self,
    ) -> None:
        self.store.save(_state())

        archived = self.client.post(
            "/api/projects/demo/writing/archive"
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["items"][0]["id"], "000001")
        current = self.client.get(
            "/api/projects/demo/writing/state"
        ).json()
        self.assertEqual(current["searches"], [])

        restored = self.client.post(
            "/api/projects/demo/writing/archives/000001/activate"
        )

        self.assertEqual(restored.status_code, 200, restored.text)
        payload = restored.json()
        self.assertEqual(
            payload["searches"][0]["hits"][0]["title"], "旧标题"
        )


if __name__ == "__main__":
    unittest.main()
