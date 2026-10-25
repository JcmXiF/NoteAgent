"""Settings the screen can change: the install file, and one project's brief."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from noteagent.acquire.search_config import SearchError
from noteagent.llm.config import load_dotenv, reset_process_env_keys
from noteagent.project.init import init_project
from noteagent.settings.brief import brief_view, save_brief
from noteagent.settings.envfile import update_env_text
from noteagent.settings.install import (
    MANAGED_ENV_NAMES,
    apply_install,
    install_view,
    probe_llm,
    probe_search,
)
from noteagent.util.refusal import Refusal


@contextmanager
def isolated_env():
    """Drop the managed names so a case cannot lock or leak the developer's settings."""
    saved = {key: os.environ.get(key) for key in MANAGED_ENV_NAMES}
    for key in MANAGED_ENV_NAMES:
        os.environ.pop(key, None)
    reset_process_env_keys()
    try:
        yield
    finally:
        reset_process_env_keys()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class EnvTextTests(unittest.TestCase):
    def test_keeps_comments_and_unrelated_keys(self) -> None:
        text = update_env_text(
            "# keep me\nOTHER=1\nNOTEAGENT_LLM_MODEL=old\n",
            {"NOTEAGENT_LLM_MODEL": "new", "NOTEAGENT_LLM_API_KEY": "ab c"},
        )
        self.assertIn("# keep me\n", text)
        self.assertIn("OTHER=1\n", text)
        self.assertIn("NOTEAGENT_LLM_MODEL=new\n", text)
        self.assertNotIn("old", text)
        self.assertIn('NOTEAGENT_LLM_API_KEY="ab c"\n', text)

    def test_delete_removes_only_that_line(self) -> None:
        text = update_env_text(
            "NOTEAGENT_LLM_MODEL=m\n# stay\nNOTEAGENT_LLM_API_KEY=secret\n",
            {"NOTEAGENT_LLM_API_KEY": None},
        )
        self.assertIn("NOTEAGENT_LLM_MODEL=m\n", text)
        self.assertIn("# stay\n", text)
        self.assertNotIn("API_KEY", text)


class InstallSettingsTests(unittest.TestCase):
    def test_apply_updates_the_file_and_this_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            path.write_text("# keep\nOTHER=1\n", encoding="utf-8")
            view = apply_install(
                {
                    "llm": {
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "ab c",
                        "model": "demo",
                    }
                },
                env_file=path,
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("# keep", text)
            self.assertIn("OTHER=1", text)
            self.assertIn("NOTEAGENT_LLM_MODEL=demo", text)
            self.assertIn('NOTEAGENT_LLM_API_KEY="ab c"', text)
            self.assertEqual(view["llm"]["model"], "demo")
            self.assertTrue(view["llm"]["apiKeySet"])
            self.assertTrue(view["llm"]["configured"])
            self.assertNotIn("apiKey", view["llm"])
            self.assertNotIn("ab c", str(view))
            self.assertEqual(os.environ["NOTEAGENT_LLM_MODEL"], "demo")
            self.assertEqual(os.environ["NOTEAGENT_LLM_API_KEY"], "ab c")

    def test_explicit_file_does_not_read_a_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            root = Path(tmp)
            (root / ".env").write_text("NOTEAGENT_LLM_MODEL=parent\n", encoding="utf-8")
            child = root / "child"
            child.mkdir()
            view = install_view(env_file=child / ".env")
            self.assertEqual(view["llm"]["model"], "")
            self.assertNotIn("NOTEAGENT_LLM_MODEL", os.environ)
            self.assertIn("parent", (root / ".env").read_text(encoding="utf-8"))

    def test_locked_name_is_refused_and_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            os.environ["NOTEAGENT_LLM_MODEL"] = "from-env"
            reset_process_env_keys()
            path = Path(tmp) / ".env"
            path.write_text("NOTEAGENT_LLM_MODEL=file\n", encoding="utf-8")
            with self.assertRaises(Refusal) as caught:
                apply_install({"llm": {"model": "other"}}, env_file=path)
            self.assertEqual(caught.exception.code, "settings.locked")
            self.assertEqual(os.environ["NOTEAGENT_LLM_MODEL"], "from-env")
            self.assertEqual(path.read_text(encoding="utf-8"), "NOTEAGENT_LLM_MODEL=file\n")
            # Saving the value the process already has is not a change.
            same = apply_install({"llm": {"model": "from-env"}}, env_file=path)
            self.assertEqual(same["llm"]["model"], "from-env")
            self.assertIn("llm.model", same["locked"])
            self.assertEqual(path.read_text(encoding="utf-8"), "NOTEAGENT_LLM_MODEL=file\n")

    def test_clear_key_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            apply_install(
                {"llm": {"apiKey": "secret", "model": "m", "baseUrl": "https://example.com/v1"}},
                env_file=path,
            )
            view = apply_install({"llm": {"clearApiKey": True}}, env_file=path)
            self.assertFalse(view["llm"]["apiKeySet"])
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("NOTEAGENT_LLM_API_KEY", os.environ)

    def test_invalid_url_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            with self.assertRaises(Refusal) as caught:
                apply_install({"llm": {"baseUrl": "notaurl"}}, env_file=path)
            self.assertEqual(caught.exception.code, "settings.invalid")
            self.assertFalse(path.exists())

    def test_unreadable_number_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            path.write_text("NOTEAGENT_LLM_TEMPERATURE=abc\n", encoding="utf-8")
            view = install_view(env_file=path)
            self.assertEqual(view["llm"]["temperature"], 0.1)
            self.assertIn("llm.temperature", view["invalid"])

    def test_probe_reports_a_rejected_key_and_does_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            # Snapshot before the names exist, so the draft is a trial and not an
            # environment override the screen is forbidden to touch.
            install_view(env_file=path)
            os.environ["NOTEAGENT_LLM_BASE_URL"] = "https://example.com/v1"
            os.environ["NOTEAGENT_LLM_API_KEY"] = "secret"
            os.environ["NOTEAGENT_LLM_MODEL"] = "m"

            class Response:
                status_code = 401
                text = "unauthorized"

            def poster(*_args: object, **_kwargs: object) -> Response:
                return Response()

            with self.assertRaises(Refusal) as caught:
                probe_llm({"baseUrl": "https://other.example/v1"}, env_file=path, poster=poster)
            self.assertEqual(caught.exception.code, "settings.probeRejected")
            self.assertFalse(path.exists())
            self.assertEqual(os.environ["NOTEAGENT_LLM_BASE_URL"], "https://example.com/v1")

    def test_probe_reports_an_unreachable_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            os.environ["NOTEAGENT_LLM_BASE_URL"] = "https://example.com/v1"
            os.environ["NOTEAGENT_LLM_API_KEY"] = "secret"
            os.environ["NOTEAGENT_LLM_MODEL"] = "m"

            def poster(*_args: object, **_kwargs: object) -> object:
                raise httpx.ConnectError("down")

            with self.assertRaises(Refusal) as caught:
                probe_llm(env_file=Path(tmp) / ".env", poster=poster)
            self.assertEqual(caught.exception.code, "settings.probeUnreachable")

    def test_search_probe_uses_the_draft_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"
            seen: dict[str, object] = {}

            class Run:
                cards = ("a", "b")

            def runner(query: str, *, top_k: int, config: object) -> Run:
                seen["query"] = query
                seen["top_k"] = top_k
                seen["provider"] = config.provider  # type: ignore[attr-defined]
                seen["key"] = config.api_key  # type: ignore[attr-defined]
                return Run()

            count = probe_search(
                {"provider": "brave", "apiKey": "draft-key"},
                env_file=path,
                runner=runner,
            )
            self.assertEqual(count, 2)
            self.assertEqual(seen["provider"], "brave")
            self.assertEqual(seen["key"], "draft-key")
            self.assertFalse(path.exists())
            self.assertNotIn("NOTEAGENT_SEARCH_API_KEY", os.environ)

    def test_search_probe_names_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            path = Path(tmp) / ".env"

            def raising(message: str):
                def runner(*_args: object, **_kwargs: object) -> object:
                    raise SearchError(message)

                return runner

            cases = [
                ("search request failed: boom", "settings.searchUnreachable"),
                ("search HTTP 401: no key", "settings.searchRejected"),
                ("the provider said something odd", "settings.searchFailed"),
            ]
            for message, code in cases:
                with self.assertRaises(Refusal) as caught:
                    probe_search(
                        {"provider": "brave", "apiKey": "k"},
                        env_file=path,
                        runner=raising(message),
                    )
                self.assertEqual(caught.exception.code, code, message)

    def test_search_probe_without_a_provider_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            with self.assertRaises(Refusal) as caught:
                probe_search(env_file=Path(tmp) / ".env", runner=lambda *_a, **_k: None)
            self.assertEqual(caught.exception.code, "settings.searchOff")


class BriefTests(unittest.TestCase):
    def test_empty_save_removes_the_project_brief_and_keeps_a_legacy_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            self.assertEqual(brief_view(layout)["source"], "default")
            saved = save_brief(layout, "  About birds.\n")
            self.assertEqual(saved["source"], "project")
            self.assertEqual(saved["text"], "About birds.")
            self.assertTrue(layout.brief.is_file())
            layout.legacy_purpose.write_text("Old purpose.\n", encoding="utf-8")
            cleared = save_brief(layout, "   ")
            self.assertFalse(layout.brief.exists())
            self.assertEqual(cleared["source"], "legacy")
            self.assertEqual(cleared["text"], "Old purpose.")
            self.assertTrue(layout.legacy_purpose.is_file())

    def test_overlong_brief_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = init_project(Path(tmp) / "proj")
            with self.assertRaises(Refusal) as caught:
                save_brief(layout, "x" * 6001)
            self.assertEqual(caught.exception.code, "settings.briefTooLong")
            self.assertFalse(layout.brief.exists())


try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    import noteagent.api.projects as projects_mod
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]


@unittest.skipIf(TestClient is None, "fastapi not installed")
class SettingsApiTests(unittest.TestCase):
    def test_put_round_trip_and_brief(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            root = Path(tmp)
            env_file = root / ".env"
            env_file.write_text("# keep\n", encoding="utf-8")
            init_project(root / "projects" / "alpha")

            def only_this_file(start: Path | str | None = None, *, walk: bool = True) -> None:
                load_dotenv(env_file.parent, walk=False)

            with (
                patch("noteagent.settings.install.load_dotenv", only_this_file),
                patch("noteagent.settings.install.dotenv_path", lambda start=None: env_file),
                patch.object(projects_mod, "REPO_ROOT", root),
            ):
                client = TestClient(create_app())
                saved = client.put(
                    "/api/settings",
                    json={"llm": {"baseUrl": "https://example.com/v1", "model": "demo"}},
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                body = saved.json()
                self.assertEqual(body["llm"]["baseUrl"], "https://example.com/v1")
                self.assertEqual(body["llm"]["model"], "demo")
                self.assertNotIn("apiKey", body["llm"])
                self.assertIn("# keep", env_file.read_text(encoding="utf-8"))

                again = client.put(
                    "/api/settings",
                    json={"llm": {"temperature": 0.4}},
                )
                self.assertEqual(again.status_code, 200, again.text)
                self.assertEqual(again.json()["llm"]["model"], "demo")
                self.assertEqual(again.json()["llm"]["temperature"], 0.4)

                brief = client.get("/api/projects/alpha/brief")
                self.assertEqual(brief.status_code, 200, brief.text)
                self.assertEqual(brief.json()["source"], "default")
                wrote = client.put(
                    "/api/projects/alpha/brief",
                    json={"text": "About birds."},
                )
                self.assertEqual(wrote.status_code, 200, wrote.text)
                self.assertEqual(wrote.json()["text"], "About birds.")
                self.assertEqual(wrote.json()["source"], "project")
