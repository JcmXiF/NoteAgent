"""What the core does with old chats when it starts.

The list and open routes already migrate lazily; an upgrade should not depend
on the reader opening one of them first.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FakeSessionStore, old_chat_payload

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover - CI installs the API extra
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _project(root: Path) -> Path:
    (root / "raw").mkdir(parents=True)
    return root


def _old_chat(project: Path, ident: str = "c-1") -> Path:
    chats = project / ".noteagent" / "chats"
    chats.mkdir(parents=True, exist_ok=True)
    path = chats / f"{ident}.json"
    path.write_text(
        json.dumps(old_chat_payload(ident), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@unittest.skipIf(TestClient is None, "fastapi not installed")
class StartupMigrationTests(unittest.TestCase):
    def _keep_settings_out(self):
        """The lifespan's saved-settings read stays out of these tests."""
        return patch("noteagent.api.app.install_view", lambda: None)

    def test_the_lifespan_brings_old_chats_across_before_a_route_asks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp) / "project")
            legacy = _old_chat(project)
            with patch.dict(
                os.environ,
                {
                    "NOTEAGENT_PROJECT": str(project),
                    "NOTEAGENT_CONFIG_DIR": str(Path(tmp) / "config"),
                },
            ), self._keep_settings_out():
                with TestClient(create_app()) as client:
                    # The record exists as soon as the core is ready, before the
                    # test has opened the list or a session.
                    self.assertTrue(
                        (project / ".noteagent" / "sessions" / "c-1.jsonl").is_file()
                    )
                    listed = client.get("/api/v1/sessions").json()["sessions"]

            self.assertEqual([item["id"] for item in listed], ["c-1"])
            self.assertTrue((legacy.parent / ".migrated").is_file())

    def test_an_injected_store_is_not_migrated_behind_its_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _project(Path(tmp) / "project")
            _old_chat(project)
            deps = AppDeps(sessions=FakeSessionStore())
            with patch.dict(
                os.environ,
                {
                    "NOTEAGENT_PROJECT": str(project),
                    "NOTEAGENT_CONFIG_DIR": str(Path(tmp) / "config"),
                },
            ), self._keep_settings_out():
                with TestClient(create_app(deps)) as client:
                    self.assertEqual(
                        client.get("/api/v1/sessions").json(), {"sessions": []}
                    )

            self.assertFalse(
                (project / ".noteagent" / "sessions" / "c-1.jsonl").exists()
            )

    def test_a_core_with_no_project_still_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "NOTEAGENT_PROJECT": "",
                    "NOTEAGENT_HOME": str(Path(tmp) / "home"),
                    "NOTEAGENT_CONFIG_DIR": str(Path(tmp) / "config"),
                },
            ), self._keep_settings_out():
                with TestClient(create_app()) as client:
                    response = client.get("/api/v1/health")

            self.assertEqual(response.status_code, 200, response.text)

    def test_a_broken_project_pointer_does_not_stop_the_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (home / ".noteagent-project").write_text("{not json", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "NOTEAGENT_PROJECT": "",
                    "NOTEAGENT_HOME": str(home),
                    "NOTEAGENT_CONFIG_DIR": str(Path(tmp) / "config"),
                },
            ), self._keep_settings_out(), self.assertLogs(
                "noteagent.api.session_access", level="WARNING"
            ) as recorded:
                with TestClient(create_app()) as client:
                    response = client.get("/api/v1/health")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("cannot be read at startup", recorded.output[0])


if __name__ == "__main__":
    unittest.main()
