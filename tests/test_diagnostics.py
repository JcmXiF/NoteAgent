from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteagent import __version__
from noteagent.api import diagnostics as diag
from noteagent.util.refusal import Refusal

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]


class ReadTailTests(unittest.TestCase):
    def test_a_missing_file_reads_as_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(diag.read_tail(Path(tmp) / "nope.log"), [])

    def test_only_the_end_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.log"
            path.write_text("\n".join(f"line {index}" for index in range(50)), encoding="utf-8")
            rows = diag.read_tail(path, lines=3)
            self.assertEqual(rows, ["line 47", "line 48", "line 49"])

    def test_a_truncated_first_line_is_dropped(self) -> None:
        """Half a sentence about the crash is worse than the next whole one."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.log"
            body = "".join(f"row {index:03d} padding\n" for index in range(200))
            path.write_text(body, encoding="utf-8")
            # 37 is not a multiple of the line length, so the window opens inside
            # a line and the first row read is a fragment.
            rows = diag.read_tail(path, lines=5, max_bytes=37)
            self.assertTrue(rows)
            for row in rows:
                self.assertRegex(row, r"^row \d{3} padding$")
            self.assertEqual(rows[-1], "row 199 padding")

    def test_undecodable_bytes_do_not_stop_the_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.log"
            path.write_bytes(b"first\nsecond\n\xff\xfe broken\n")
            rows = diag.read_tail(path)
            self.assertEqual(rows[0], "first")
            self.assertIn("broken", rows[-1])


class SupervisorStateTests(unittest.TestCase):
    def test_a_record_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / diag.SUPERVISOR_NAME).write_text(
                json.dumps({"restarts": 2, "lastExit": {"code": 1, "at": 12.5}}),
                encoding="utf-8",
            )
            state = diag.supervisor_state(logs)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state["restarts"], 2)
            self.assertEqual(state["lastExit"], {"code": 1, "at": 12.5})

    def test_a_damaged_record_reads_as_no_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / diag.SUPERVISOR_NAME).write_text("{ not json", encoding="utf-8")
            self.assertIsNone(diag.supervisor_state(logs))

    def test_a_record_with_wrong_types_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / diag.SUPERVISOR_NAME).write_text(
                json.dumps({"restarts": "many", "lastExit": {"code": "x", "at": None}}),
                encoding="utf-8",
            )
            state = diag.supervisor_state(logs)
            assert state is not None
            self.assertEqual(state["restarts"], 0)
            self.assertIsNone(state["lastExit"])

    def test_no_log_folder_means_no_record(self) -> None:
        self.assertIsNone(diag.supervisor_state(None))


_INSTALL = {
    "llm": {"configured": True},
    "configPath": "C:/config.json",
    "schemaVersion": 1,
    "revision": 4,
    "keychainAvailable": True,
}


class DiagnosticsPayloadTests(unittest.TestCase):
    def _payload(self, repo: Path, install: dict | None = None, **kwargs: object) -> dict:
        with (
            patch.object(diag, "install_view", lambda: install or _INSTALL),
            patch.object(
                diag,
                "list_projects",
                lambda: [{"name": "alpha", "active": True}, {"name": "beta", "active": False}],
            ),
            patch.object(diag, "resolve_repo_root", lambda: repo),
        ):
            return diag.diagnostics_payload(**kwargs)  # type: ignore[arg-type]

    def test_the_figures_come_from_this_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            payload = self._payload(repo, now=1_000.0, started=940.0, env={})
            self.assertEqual(payload["version"], __version__)
            self.assertEqual(payload["pid"], os.getpid())
            self.assertEqual(payload["uptimeSeconds"], 60.0)
            self.assertEqual(payload["dataRoot"], str(repo))
            self.assertEqual(payload["projectCount"], 2)
            self.assertEqual(payload["activeProject"], "alpha")
            self.assertFalse(payload["supervised"])
            self.assertIsNone(payload["logs"])
            self.assertEqual(payload["warnings"], [])

    def test_the_configuration_block_says_where_the_document_lives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._payload(Path(tmp), env={})
            self.assertEqual(
                payload["configuration"],
                {
                    "path": "C:/config.json",
                    "schemaVersion": 1,
                    "revision": 4,
                    "keychainAvailable": True,
                },
            )

    def test_a_damaged_document_still_names_the_path_and_the_store(self) -> None:
        class Unavailable:
            def available(self) -> bool:
                return False

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(
                    diag,
                    "install_view",
                    side_effect=Refusal("settings.configInvalid", "bad document"),
                ),
                patch.object(diag, "list_projects", lambda: []),
                patch.object(diag, "resolve_repo_root", lambda: Path(tmp)),
                patch.object(diag, "keyring_store", lambda: Unavailable()),
            ):
                payload = diag.diagnostics_payload(env={})

        self.assertIn("noProject", payload["warnings"])
        self.assertIn("noModel", payload["warnings"])
        self.assertTrue(payload["configuration"]["path"].endswith("config.json"))
        self.assertIsNone(payload["configuration"]["schemaVersion"])
        self.assertIsNone(payload["configuration"]["revision"])
        self.assertFalse(payload["configuration"]["keychainAvailable"])

    def test_a_checkout_has_no_restart_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._payload(Path(tmp), env={})
            self.assertIsNone(payload["logs"])
            self.assertEqual(payload["restarts"], 0)
            self.assertIsNone(payload["lastExit"])

    def test_an_installed_app_reports_its_log_and_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / diag.LOG_NAME).write_text("starting\n", encoding="utf-8")
            (logs / diag.SUPERVISOR_NAME).write_text(
                json.dumps({"restarts": 1, "lastExit": {"code": 3, "at": 50.0}}),
                encoding="utf-8",
            )
            env = {
                diag.ENV_LOG_DIR: str(logs),
                diag.ENV_SUPERVISED: "1",
            }
            payload = self._payload(Path(tmp), env=env)
            self.assertTrue(payload["supervised"])
            self.assertEqual(payload["restarts"], 1)
            self.assertEqual(payload["lastExit"], {"code": 3, "at": 50.0})
            self.assertTrue(payload["logs"]["exists"])
            self.assertGreater(payload["logs"]["bytes"], 0)
            self.assertNotIn("noLog", payload["warnings"])

    def test_a_supervised_app_that_never_wrote_a_log_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._payload(Path(tmp), env={diag.ENV_LOG_DIR: tmp, diag.ENV_SUPERVISED: "1"})
            self.assertIn("noLog", payload["warnings"])

    def test_missing_settings_and_projects_are_named_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(diag, "install_view", lambda: {"llm": {"configured": False}}),
                patch.object(diag, "list_projects", lambda: []),
                patch.object(diag, "resolve_repo_root", lambda: Path(tmp)),
            ):
                payload = diag.diagnostics_payload(env={})
            self.assertIn("noProject", payload["warnings"])
            self.assertIn("noModel", payload["warnings"])
            self.assertIsNone(payload["activeProject"])


class LogPayloadTests(unittest.TestCase):
    def test_without_a_log_folder_there_is_nothing_to_show(self) -> None:
        self.assertEqual(diag.log_payload(env={}), {"path": None, "lines": []})

    def test_the_tail_is_returned_with_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / diag.LOG_NAME).write_text("one\ntwo\n", encoding="utf-8")
            payload = diag.log_payload(env={diag.ENV_LOG_DIR: tmp})
            self.assertEqual(payload["lines"], ["one", "two"])
            self.assertTrue(payload["path"].endswith(diag.LOG_NAME))

    def test_the_line_count_is_kept_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / diag.LOG_NAME).write_text(
                "\n".join(str(index) for index in range(900)), encoding="utf-8"
            )
            payload = diag.log_payload(lines=10_000, env={diag.ENV_LOG_DIR: tmp})
            self.assertEqual(len(payload["lines"]), diag.MAX_TAIL_LINES)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class DiagnosticsApiTests(unittest.TestCase):
    def test_the_screen_can_be_read(self) -> None:
        client = TestClient(create_app())
        answer = client.get("/api/diagnostics")
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["version"], __version__)
        self.assertIn("projectsRoot", body)
        self.assertIn("warnings", body)

    def test_the_log_is_empty_when_nothing_is_supervising(self) -> None:
        client = TestClient(create_app())
        with patch.dict(os.environ, {diag.ENV_LOG_DIR: ""}):
            answer = client.get("/api/diagnostics/log")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json(), {"path": None, "lines": []})

    def test_the_line_count_has_a_ceiling(self) -> None:
        client = TestClient(create_app())
        answer = client.get("/api/diagnostics/log", params={"lines": 10_000})
        self.assertEqual(answer.status_code, 422)

    def test_the_old_update_route_is_gone(self) -> None:
        app = create_app()
        self.assertNotIn(
            "/api/diagnostics/update",
            {getattr(route, "path", "") for route in app.routes},
        )
        client = TestClient(app)
        answer = client.post("/api/diagnostics/update")
        self.assertIn(answer.status_code, (404, 405))
