"""Settings the screen can change: installation settings, the brief, and trial calls.

Installation settings now live in a versioned document plus the credential
store. These cases are the external behaviour: what a save answers, what is
written where, what an environment override does, and what never reaches the
screen or the file.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx

from noteagent.acquire.search_config import SearchError
from noteagent.llm.config import reset_process_env_keys
from noteagent.project.init import init_project
from noteagent.settings.brief import brief_view, save_brief
from noteagent.settings.credentials import SEARCH_KEY, llm_key
from noteagent.settings.effective import forget_applied
from noteagent.settings.envfile import update_env_text
from noteagent.settings.fields import MANAGED_ENV_NAMES
from noteagent.settings.install import (
    apply_install,
    install_view,
    probe_llm,
    probe_search,
)
from noteagent.util.refusal import Refusal
from fakes import FakeCredentials


@contextmanager
def isolated_env():
    """Drop the managed names so a case cannot lock or leak the developer's settings."""
    saved = {key: os.environ.get(key) for key in MANAGED_ENV_NAMES}
    forget_applied()
    for key in MANAGED_ENV_NAMES:
        os.environ.pop(key, None)
    reset_process_env_keys()
    try:
        yield
    finally:
        forget_applied()
        reset_process_env_keys()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def settings_home(tmp: str, *, available: bool = True):
    """A temporary document and credential store, injected into the module."""
    path = Path(tmp) / "config.json"
    credentials = FakeCredentials(available)
    with (
        patch("noteagent.settings.install.default_config_path", lambda: path),
        patch("noteagent.settings.install.keyring_store", lambda: credentials),
    ):
        yield path, credentials


def saved_key(credentials: FakeCredentials, profile_id: str = "p_1") -> str | None:
    return credentials.values.get(llm_key(profile_id))


class EnvTextTests(unittest.TestCase):
    """The legacy file editor is still used by the command line."""

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
    def test_a_save_writes_the_document_and_the_credential_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            view = apply_install(
                {
                    "llm": {
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKey": "ab c",
                        "model": "demo",
                    }
                }
            )
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schemaVersion"], 1)
            self.assertEqual(document["revision"], 1)
            self.assertEqual(document["activeProfileId"], "p_1")
            self.assertEqual(document["profiles"][0]["provider"], "openai")
            self.assertNotIn("ab c", path.read_text(encoding="utf-8"))
            self.assertEqual(saved_key(credentials), "ab c")

            self.assertEqual(view["llm"]["model"], "demo")
            self.assertTrue(view["llm"]["apiKeySet"])
            self.assertTrue(view["llm"]["configured"])
            self.assertNotIn("apiKey", view["llm"])
            self.assertNotIn("ab c", json.dumps(view))
            self.assertEqual(view["revision"], 1)
            self.assertEqual(view["schemaVersion"], 1)
            self.assertEqual(view["activeProfileId"], "p_1")
            self.assertEqual([p["id"] for p in view["profiles"]], ["p_1"])
            self.assertTrue(view["profiles"][0]["active"])
            # The runtime reads the same values.
            self.assertEqual(os.environ["NOTEAGENT_LLM_MODEL"], "demo")
            self.assertEqual(os.environ["NOTEAGENT_LLM_API_KEY"], "ab c")

    def test_a_second_save_updates_the_same_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            apply_install(
                {
                    "llm": {
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "secret",
                        "model": "one",
                    }
                }
            )
            view = apply_install({"llm": {"temperature": 0.4}})
            self.assertEqual(view["llm"]["model"], "one")
            self.assertEqual(view["llm"]["temperature"], 0.4)
            self.assertEqual(view["revision"], 2)
            self.assertEqual(len(view["profiles"]), 1)
            self.assertEqual(saved_key(credentials), "secret")

    def test_a_different_path_is_a_different_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env():
            first = Path(tmp) / "one.json"
            second = Path(tmp) / "two.json"
            credentials = FakeCredentials()
            with (
                patch("noteagent.settings.install.default_config_path", lambda: first),
                patch("noteagent.settings.install.keyring_store", lambda: credentials),
            ):
                apply_install({"llm": {"baseUrl": "https://example.com/v1", "model": "one"}})
            view = install_view(path=second, credentials=credentials)
            self.assertEqual(view["llm"]["model"], "")
            self.assertEqual(view["profiles"], [])
            self.assertTrue(first.is_file())

    def test_a_process_environment_name_is_locked_and_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            os.environ["NOTEAGENT_LLM_MODEL"] = "from-env"
            reset_process_env_keys()
            with self.assertRaises(Refusal) as caught:
                apply_install({"llm": {"model": "other"}})
            self.assertEqual(caught.exception.code, "settings.locked")
            self.assertEqual(os.environ["NOTEAGENT_LLM_MODEL"], "from-env")
            self.assertFalse(path.exists())
            # Saving the value the process already has is not a change.
            same = apply_install({"llm": {"model": "from-env"}})
            self.assertEqual(same["llm"]["model"], "from-env")
            self.assertIn("llm.model", same["locked"])
            self.assertEqual(same["sources"]["llm.model"], "env")
            self.assertFalse(path.exists())

    def test_an_environment_override_is_reported_as_its_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            os.environ["NOTEAGENT_LLM_MODEL"] = "from-env"
            reset_process_env_keys()
            apply_install({"llm": {"baseUrl": "https://example.com/v1"}})
            view = install_view()
            self.assertEqual(view["llm"]["model"], "from-env")
            self.assertEqual(view["sources"]["llm.model"], "env")
            self.assertIn("llm.model", view["locked"])
            self.assertEqual(view["sources"]["llm.baseUrl"], "config")

    def test_clearing_a_key_removes_it_from_the_credential_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            apply_install(
                {
                    "llm": {
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "secret",
                        "model": "m",
                    }
                }
            )
            view = apply_install({"llm": {"clearApiKey": True}})
            self.assertFalse(view["llm"]["apiKeySet"])
            self.assertIsNone(saved_key(credentials))
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))
            self.assertNotIn("NOTEAGENT_LLM_API_KEY", os.environ)

    def test_an_invalid_url_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            with self.assertRaises(Refusal) as caught:
                apply_install({"llm": {"baseUrl": "notaurl"}})
            self.assertEqual(caught.exception.code, "settings.invalid")
            self.assertFalse(path.exists())

    def test_an_unreadable_number_in_the_environment_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            os.environ["NOTEAGENT_LLM_TEMPERATURE"] = "abc"
            reset_process_env_keys()
            view = install_view()
            self.assertEqual(view["llm"]["temperature"], 0.1)
            self.assertIn("llm.temperature", view["invalid"])

    def test_extract_media_is_part_of_the_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            view = apply_install({"extractMedia": False})
            self.assertFalse(view["extractMedia"])
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(document["extractMedia"])
            self.assertEqual(install_view()["extractMedia"], False)

    def test_a_stale_revision_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            first = apply_install({"llm": {"model": "one"}})
            self.assertEqual(first["revision"], 1)
            with self.assertRaises(Refusal) as caught:
                apply_install({"llm": {"model": "two"}, "revision": 0})
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(caught.exception.code, "settings.staleRevision")
            self.assertEqual(install_view()["llm"]["model"], "one")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["revision"], 1)
            # Saving from the revision just read works.
            second = apply_install({"llm": {"model": "two"}, "revision": first["revision"]})
            self.assertEqual(second["llm"]["model"], "two")
            self.assertEqual(second["revision"], 2)

    def test_a_missing_credential_store_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(
            tmp, available=False
        ) as (path, _credentials):
            with self.assertRaises(Refusal) as caught:
                apply_install(
                    {
                        "llm": {
                            "baseUrl": "https://example.com/v1",
                            "apiKey": "secret",
                            "model": "m",
                        }
                    }
                )
            self.assertEqual(caught.exception.code, "settings.keychainUnavailable")
            self.assertFalse(path.exists())
            self.assertNotIn("secret", os.environ.get("NOTEAGENT_LLM_API_KEY", ""))
            view = install_view()
            self.assertFalse(view["keychainAvailable"])

    def test_an_environment_key_still_works_without_a_credential_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(
            tmp, available=False
        ):
            os.environ["NOTEAGENT_LLM_BASE_URL"] = "https://example.com/v1"
            os.environ["NOTEAGENT_LLM_API_KEY"] = "from-env"
            os.environ["NOTEAGENT_LLM_MODEL"] = "m"
            reset_process_env_keys()
            view = install_view()
            self.assertTrue(view["llm"]["apiKeySet"])
            self.assertTrue(view["llm"]["configured"])
            self.assertFalse(view["keychainAvailable"])
            self.assertEqual(view["sources"]["llm.apiKey"], "env")


class ProbeTests(unittest.TestCase):
    def _save_connection(self, key: str = "secret") -> None:
        apply_install(
            {
                "llm": {
                    "baseUrl": "https://example.com/v1",
                    "apiKey": key,
                    "model": "m",
                }
            }
        )

    def test_probe_reports_a_rejected_key_and_does_not_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            before = apply_install(
                {"llm": {"baseUrl": "https://example.com/v1", "model": "m"}}
            )

            class Response:
                status_code = 401
                text = "unauthorized"

            def poster(*_args: object, **_kwargs: object) -> Response:
                return Response()

            with self.assertRaises(Refusal) as caught:
                # A new address needs a key of its own; this case is about the
                # service refusing it, not about the host rule.
                probe_llm(
                    {"baseUrl": "https://other.example/v1", "apiKey": "draft"},
                    path=path,
                    poster=poster,
                )
            self.assertEqual(caught.exception.code, "settings.probeRejected")
            self.assertEqual(install_view(path=path)["revision"], before["revision"])

    def test_probe_refuses_to_reuse_a_stored_key_at_another_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save_connection()
            calls: list[str] = []

            def poster(url: str, **_kwargs: object) -> object:
                calls.append(url)
                raise AssertionError("the stored key must not be sent")

            with self.assertRaises(Refusal) as caught:
                probe_llm({"baseUrl": "https://evil.example/v1"}, path=path, poster=poster)
            self.assertEqual(caught.exception.code, "settings.probeKeyRequired")
            self.assertEqual(calls, [])

    def test_probe_allows_a_new_path_on_the_same_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save_connection()
            calls: list[str] = []

            class Response:
                status_code = 200
                text = "ok"

            def poster(url: str, **_kwargs: object) -> Response:
                calls.append(url)
                return Response()

            probe_llm({"baseUrl": "https://example.com/v2"}, path=path, poster=poster)
            self.assertEqual(calls, ["https://example.com/v2/chat/completions"])

    def test_probe_refuses_a_new_scheme_or_port_for_a_stored_key(self) -> None:
        drafts = ("http://example.com/v1", "https://example.com:8443/v1")
        for draft in drafts:
            with self.subTest(draft=draft), tempfile.TemporaryDirectory() as tmp, (
                isolated_env()
            ), settings_home(tmp) as (path, _credentials):
                self._save_connection()

                def poster(*_args: object, **_kwargs: object) -> object:
                    raise AssertionError("the stored key must not be sent")

                with self.assertRaises(Refusal) as caught:
                    probe_llm({"baseUrl": draft}, path=path, poster=poster)
                self.assertEqual(caught.exception.code, "settings.probeKeyRequired")

    def test_probe_refuses_a_stored_key_when_no_address_was_saved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            # A key with no profile still creates the empty profile it belongs to.
            apply_install({"llm": {"apiKey": "secret"}})

            def poster(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("the stored key must not be sent")

            with self.assertRaises(Refusal) as caught:
                probe_llm({"baseUrl": "https://example.com/v1"}, path=path, poster=poster)
            self.assertEqual(caught.exception.code, "settings.probeKeyRequired")

    def test_probe_reports_an_unreachable_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save_connection()

            def poster(*_args: object, **_kwargs: object) -> object:
                raise httpx.ConnectError("down")

            with self.assertRaises(Refusal) as caught:
                probe_llm(path=path, poster=poster)
            self.assertEqual(caught.exception.code, "settings.probeUnreachable")

    def test_search_probe_uses_the_draft_without_saving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
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
                path=path,
                runner=runner,
            )
            self.assertEqual(count, 2)
            self.assertEqual(seen["provider"], "brave")
            self.assertEqual(seen["key"], "draft-key")
            self.assertFalse(path.exists())
            self.assertNotIn("NOTEAGENT_SEARCH_API_KEY", os.environ)

    def test_search_probe_uses_the_saved_key_when_the_draft_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            apply_install({"search": {"provider": "brave", "apiKey": "saved-key"}})
            self.assertEqual(credentials.values[SEARCH_KEY], "saved-key")
            seen: dict[str, object] = {}

            class Run:
                cards = ()

            def runner(query: str, *, top_k: int, config: object) -> Run:
                seen["key"] = config.api_key  # type: ignore[attr-defined]
                return Run()

            probe_search({"provider": "brave"}, path=path, runner=runner)
            self.assertEqual(seen["key"], "saved-key")

    def test_search_probe_names_the_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
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
                        path=path,
                        runner=raising(message),
                    )
                self.assertEqual(caught.exception.code, code, message)

    def test_search_probe_without_a_provider_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            with self.assertRaises(Refusal) as caught:
                probe_search(path=path, runner=lambda *_a, **_k: None)
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
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            root = Path(tmp)
            init_project(root / "projects" / "alpha")

            with patch.object(projects_mod, "REPO_ROOT", root):
                client = TestClient(create_app())
                saved = client.put(
                    "/api/settings",
                    json={
                        "llm": {
                            "baseUrl": "https://example.com/v1",
                            "apiKey": "secret",
                            "model": "demo",
                        }
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                body = saved.json()
                self.assertEqual(body["llm"]["baseUrl"], "https://example.com/v1")
                self.assertEqual(body["llm"]["model"], "demo")
                self.assertNotIn("apiKey", body["llm"])
                self.assertNotIn("secret", saved.text)
                self.assertEqual(body["revision"], 1)
                self.assertEqual(body["schemaVersion"], 1)
                self.assertEqual(body["activeProfileId"], "p_1")
                self.assertTrue(path.is_file())
                self.assertEqual(saved_key(credentials), "secret")

                again = client.put(
                    "/api/settings",
                    json={"llm": {"temperature": 0.4}, "revision": body["revision"]},
                )
                self.assertEqual(again.status_code, 200, again.text)
                self.assertEqual(again.json()["llm"]["model"], "demo")
                self.assertEqual(again.json()["llm"]["temperature"], 0.4)
                self.assertEqual(again.json()["revision"], 2)

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

    def test_a_stale_revision_is_a_409_with_its_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            first = client.put("/api/settings", json={"llm": {"model": "one"}})
            self.assertEqual(first.status_code, 200, first.text)
            stale = client.put(
                "/api/settings",
                json={"llm": {"model": "two"}, "revision": 0},
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(stale.json()["code"], "settings.staleRevision")
            current = client.get("/api/settings")
            self.assertEqual(current.json()["llm"]["model"], "one")

    def test_an_unavailable_credential_store_is_reported_not_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(
            tmp, available=False
        ) as (path, _credentials):
            client = TestClient(create_app())
            refused = client.put(
                "/api/settings",
                json={
                    "llm": {
                        "baseUrl": "https://example.com/v1",
                        "apiKey": "secret",
                        "model": "m",
                    }
                },
            )
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "settings.keychainUnavailable")
            self.assertFalse(path.exists())
            view = client.get("/api/settings")
            self.assertEqual(view.status_code, 200, view.text)
            self.assertFalse(view.json()["keychainAvailable"])
            self.assertFalse(view.json()["llm"]["apiKeySet"])

    def test_the_environment_wins_and_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            os.environ["NOTEAGENT_LLM_MODEL"] = "outside"
            reset_process_env_keys()
            client = TestClient(create_app())
            client.put(
                "/api/settings",
                json={"llm": {"baseUrl": "https://example.com/v1"}},
            )
            view = client.get("/api/settings").json()
            self.assertEqual(view["llm"]["model"], "outside")
            self.assertEqual(view["sources"]["llm.model"], "env")
            self.assertIn("llm.model", view["locked"])
