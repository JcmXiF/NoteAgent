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


class DiagnosticsPayloadTests(unittest.TestCase):
    def _payload(self, repo: Path, **kwargs: object) -> dict:
        with (
            patch.object(diag, "install_view", lambda: {"llm": {"configured": True}}),
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
                diag.ENV_UPDATE_URL: "https://example.test/latest.json",
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


class VersionTests(unittest.TestCase):
    def test_a_plain_version(self) -> None:
        self.assertEqual(diag.version_key("0.1.0"), (0, 1, 0))

    def test_a_leading_v_and_a_short_form(self) -> None:
        self.assertEqual(diag.version_key("v0.2"), (0, 2))

    def test_a_pre_release_suffix_is_dropped(self) -> None:
        self.assertEqual(diag.version_key("1.2.3-beta.1"), (1, 2, 3))
        self.assertEqual(diag.version_key("1.2.3+build.7"), (1, 2, 3))

    def test_anything_else_is_not_a_version(self) -> None:
        for value in ("", "newest", "1..2", "1.2.x"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    diag.version_key(value)


class UpdateCheckTests(unittest.TestCase):
    def test_no_source_configured_is_an_answer_not_a_failure(self) -> None:
        result = diag.check_update(env={})
        self.assertEqual(result, {"configured": False, "current": __version__})

    def test_a_newer_version_is_reported_with_its_notes(self) -> None:
        manifest = {
            "version": "9.9.9",
            "notes": "Faster saves.",
            "url": "https://example.test/download",
        }
        result = diag.check_update(
            current="0.1.0",
            manifest_url="https://example.test/latest.json",
            fetch=lambda _url: manifest,
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "9.9.9")
        self.assertEqual(result["notes"], "Faster saves.")
        self.assertEqual(result["url"], "https://example.test/download")
        self.assertEqual(result["current"], "0.1.0")

    def test_the_same_version_is_not_newer(self) -> None:
        result = diag.check_update(
            current="0.1.0",
            manifest_url="https://example.test/latest.json",
            fetch=lambda _url: {"version": "0.1"},
        )
        self.assertFalse(result["available"])

    def test_an_older_version_is_not_newer(self) -> None:
        result = diag.check_update(
            current="1.4.0",
            manifest_url="https://example.test/latest.json",
            fetch=lambda _url: {"version": "1.3.9"},
        )
        self.assertFalse(result["available"])

    def test_a_version_that_is_not_one_is_a_refusal(self) -> None:
        with self.assertRaises(Refusal) as raised:
            diag.check_update(
                current="0.1.0",
                manifest_url="https://example.test/latest.json",
                fetch=lambda _url: {"version": "newest"},
            )
        self.assertEqual(raised.exception.code, "update.badManifest")
        self.assertEqual(raised.exception.status, 502)

    def test_a_source_that_did_not_answer_is_a_refusal(self) -> None:
        def broken(_url: str) -> dict:
            raise diag.UpdateUnreachable("connection refused")

        with self.assertRaises(Refusal) as raised:
            diag.check_update(
                current="0.1.0",
                manifest_url="https://example.test/latest.json",
                fetch=broken,
            )
        self.assertEqual(raised.exception.code, "update.unreachable")
        self.assertEqual(raised.exception.status, 502)

    def test_a_source_that_is_not_a_web_address_is_refused(self) -> None:
        for url in ("file:///tmp/latest.json", "example.test/latest.json"):
            with self.subTest(url=url):
                with self.assertRaises(Refusal) as raised:
                    diag.check_update(current="0.1.0", manifest_url=url)
                self.assertEqual(raised.exception.code, "update.badSource")
                self.assertEqual(raised.exception.status, 409)

    def test_a_download_link_that_is_not_a_web_address_is_dropped(self) -> None:
        result = diag.check_update(
            current="0.1.0",
            manifest_url="https://example.test/latest.json",
            fetch=lambda _url: {"version": "0.2.0", "url": "javascript:alert(1)"},
        )
        self.assertEqual(result["url"], "")
        self.assertEqual(result["notes"], "")

    def test_the_source_is_read_from_the_environment(self) -> None:
        seen: list[str] = []

        def reader(url: str) -> dict:
            seen.append(url)
            return {"version": "0.2.0"}

        diag.check_update(
            current="0.1.0",
            env={diag.ENV_UPDATE_URL: "https://example.test/latest.json"},
            fetch=reader,
        )
        self.assertEqual(seen, ["https://example.test/latest.json"])


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

    def test_a_configured_source_can_be_checked_without_one_being_reached(self) -> None:
        """The route asks the configured source, and this test gives it none."""
        client = TestClient(create_app())
        with patch.dict(os.environ, {diag.ENV_UPDATE_URL: ""}):
            answer = client.post("/api/diagnostics/update")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertFalse(answer.json()["configured"])
