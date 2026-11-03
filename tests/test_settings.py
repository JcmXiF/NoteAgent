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

from noteagent.acquire.search_config import (
    NativeSearchRejected,
    SearchConfig,
    SearchError,
    active_llm_provider,
)
from noteagent.llm.config import reset_process_env_keys
from noteagent.project.init import init_project
from noteagent.settings.brief import brief_view, save_brief
from noteagent.settings.credentials import SEARCH_KEY, llm_key
from noteagent.settings.effective import forget_applied
from noteagent.settings.envfile import update_env_text
from noteagent.settings.fields import MANAGED_ENV_NAMES
from noteagent.settings.profiles import create_profile
from noteagent.settings.install import (
    apply_install,
    deep_check,
    install_view,
    list_models,
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
    def test_reading_the_settings_makes_no_network_call(self) -> None:
        """Startup reads the document and the credential store, and nothing else."""
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            with patch(
                "noteagent.settings.install.httpx.Client",
                side_effect=AssertionError("settings must not call the network"),
            ):
                view = install_view()
            self.assertIn("profiles", view)

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


class SearchSettingsTests(unittest.TestCase):
    """The search surface shares the document, and its mode has a source."""

    def test_search_mode_and_locale_persist_in_the_shared_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            view = apply_install(
                {"search": {"mode": "external", "country": "cn", "timeoutSeconds": 12}}
            )
            self.assertEqual(view["search"]["mode"], "external")
            self.assertEqual(view["search"]["country"], "CN")
            self.assertEqual(view["search"]["timeoutSeconds"], 12)
            self.assertFalse(view["search"]["nativeAvailable"])
            self.assertEqual(install_view()["search"]["mode"], "external")

    def test_a_search_mode_from_the_environment_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            os.environ["NOTEAGENT_SEARCH_MODE"] = "native"
            reset_process_env_keys()
            view = install_view()
            self.assertEqual(view["search"]["mode"], "native")
            self.assertEqual(view["sources"]["search.mode"], "env")
            self.assertIn("search.mode", view["locked"])

    def test_the_active_provider_is_read_for_native_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            create_profile(
                {
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "revision": 0,
                },
                path=path,
                credentials=credentials,
            )
            with (
                patch(
                    "noteagent.acquire.search_config.default_config_path",
                    lambda: path,
                ),
                patch(
                    "noteagent.acquire.search_config.keyring_store",
                    lambda: credentials,
                ),
            ):
                self.assertEqual(active_llm_provider(), "openai")

    def test_a_missing_config_names_no_active_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "noteagent.acquire.search_config.default_config_path",
                    lambda: Path(tmp) / "missing.json",
                ),
                patch(
                    "noteagent.acquire.search_config.keyring_store",
                    lambda: FakeCredentials(),
                ),
            ):
                self.assertEqual(active_llm_provider(), "")


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

    def test_search_probe_uses_a_saved_anthropic_connection_natively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            apply_install(
                {
                    "llm": {
                        "baseUrl": "https://api.anthropic.com/v1",
                        "apiKey": "sk-ant",
                        "model": "claude-sonnet-5-5",
                    }
                }
            )
            seen: dict[str, object] = {}

            class Run:
                cards = ()

            def runner(query: str, *, top_k: int, config: SearchConfig) -> Run:
                seen["mode"] = config.mode
                seen["provider"] = config.native.provider if config.native else None
                seen["base_url"] = config.native.base_url if config.native else None
                return Run()

            count = probe_search({"mode": "native"}, path=path, runner=runner)
            self.assertEqual(count, 0)
            self.assertEqual(seen["mode"], "native")
            self.assertEqual(seen["provider"], "anthropic")
            self.assertEqual(seen["base_url"], "https://api.anthropic.com/v1")

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

    def test_search_probe_with_native_mode_needs_no_search_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            seen: dict[str, object] = {}

            class Run:
                cards = ("a", "b")

            def runner(query: str, *, top_k: int, config: object) -> Run:
                seen["mode"] = config.mode  # type: ignore[attr-defined]
                return Run()

            count = probe_search({"mode": "native"}, path=path, runner=runner)
            self.assertEqual(count, 2)
            self.assertEqual(seen["mode"], "native")

class _CheckResponse:
    """One answer from a model endpoint, without a network."""

    def __init__(
        self, payload: object = None, status_code: int = 200, text: str = ""
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


TOOL_CALL_ANSWER = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "preflight", "scope": "wiki"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ]
}


class DeepCheckTests(unittest.TestCase):
    """The explicit check: tool calling now, native search when it is in use."""

    def _save(self, *, mode: str = "native") -> None:
        apply_install(
            {
                "llm": {
                    "baseUrl": "https://api.openai.com/v1",
                    "apiKey": "sk-openai",
                    "model": "gpt-4o-mini",
                },
                "search": {"mode": mode},
            }
        )

    def test_a_passing_check_names_what_it_verified_and_saves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save()
            before = path.read_text(encoding="utf-8")
            revision = install_view(path=path)["revision"]
            seen: dict[str, object] = {}

            class Run:
                cards = ("a", "b")

            def runner(query: str, *, top_k: int, config: SearchConfig) -> Run:
                seen["query"] = query
                seen["mode"] = config.mode
                seen["provider"] = config.native.provider if config.native else None
                return Run()

            result = deep_check(
                path=path,
                poster=lambda _url, **_kwargs: _CheckResponse(TOOL_CALL_ANSWER),
                runner=runner,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(
                [check["name"] for check in result["checks"]],
                ["toolCalling", "nativeSearch"],
            )
            self.assertTrue(all(check["ok"] for check in result["checks"]))
            self.assertEqual(seen["mode"], "native")
            self.assertEqual(seen["provider"], "openai")
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(install_view(path=path)["revision"], revision)

    def test_a_tool_call_failure_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save(mode="external")
            result = deep_check(
                path=path,
                poster=lambda _url, **_kwargs: _CheckResponse(
                    {
                        "choices": [
                            {
                                "message": {"content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ),
                runner=lambda *_args, **_kwargs: self.fail(
                    "external mode must not check native search"
                ),
            )

            self.assertFalse(result["ok"])
            self.assertEqual(len(result["checks"]), 1)
            check = result["checks"][0]
            self.assertEqual(check["name"], "toolCalling")
            self.assertFalse(check["ok"])
            self.assertEqual(check["code"], "settings.toolCallingUnsupported")

    def test_a_rejected_tool_call_request_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save(mode="external")
            result = deep_check(
                path=path,
                poster=lambda _url, **_kwargs: _CheckResponse(
                    status_code=401, text="unauthorized"
                ),
                runner=lambda *_args, **_kwargs: self.fail(
                    "external mode must not check native search"
                ),
            )

            self.assertFalse(result["checks"][0]["ok"])
            self.assertEqual(
                result["checks"][0]["code"], "settings.probeRejected"
            )

    def test_a_native_search_failure_is_named_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save()

            def runner(*_args: object, **_kwargs: object) -> object:
                raise NativeSearchRejected("native search HTTP 401: no")

            result = deep_check(
                path=path,
                poster=lambda _url, **_kwargs: _CheckResponse(TOOL_CALL_ANSWER),
                runner=runner,
            )

            self.assertFalse(result["ok"])
            checks = {check["name"]: check for check in result["checks"]}
            self.assertTrue(checks["toolCalling"]["ok"])
            self.assertFalse(checks["nativeSearch"]["ok"])
            self.assertEqual(
                checks["nativeSearch"]["code"], "provider.nativeRejected"
            )

    def test_a_tool_result_turn_failure_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            self._save(mode="external")
            calls = {"count": 0}

            def poster(_url: str, **_kwargs: object) -> _CheckResponse:
                calls["count"] += 1
                if calls["count"] == 1:
                    return _CheckResponse(TOOL_CALL_ANSWER)
                return _CheckResponse(status_code=400, text="tool result rejected")

            result = deep_check(
                path=path,
                poster=poster,
                runner=lambda *_args, **_kwargs: self.fail(
                    "external mode must not check native search"
                ),
            )

            self.assertEqual(calls["count"], 2)
            self.assertFalse(result["checks"][0]["ok"])
            self.assertEqual(
                result["checks"][0]["code"], "settings.toolCallingUnsupported"
            )

    def test_an_environment_only_openai_connection_checks_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            _credentials,
        ):
            os.environ["NOTEAGENT_LLM_BASE_URL"] = "https://api.openai.com/v1"
            os.environ["NOTEAGENT_LLM_API_KEY"] = "sk-openai"
            os.environ["NOTEAGENT_LLM_MODEL"] = "gpt-4o-mini"
            os.environ["NOTEAGENT_SEARCH_MODE"] = "native"
            reset_process_env_keys()
            seen: dict[str, object] = {}

            class Run:
                cards = ()

            def runner(query: str, *, top_k: int, config: SearchConfig) -> Run:
                seen["provider"] = (
                    config.native.provider if config.native else None
                )
                return Run()

            result = deep_check(
                path=path,
                poster=lambda _url, **_kwargs: _CheckResponse(TOOL_CALL_ANSWER),
                runner=runner,
            )

            self.assertIn(
                "nativeSearch",
                [check["name"] for check in result["checks"]],
            )
            self.assertEqual(seen["provider"], "openai")


class _ModelsResponse:
    """One answer from a model-list endpoint, without a network."""

    def __init__(self, payload: object = None, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class ModelDiscoveryTests(unittest.TestCase):
    """The one action that turns an endpoint into a list of model names."""

    def test_lists_openai_compatible_models_and_sends_the_key_to_that_host(self) -> None:
        seen: dict[str, object] = {}

        def getter(url, *, headers, timeout):
            seen.update(url=url, headers=headers)
            return _ModelsResponse(
                {"data": [{"id": "one"}, {"id": "two"}, {"id": "one"}]}
            )

        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            models = list_models(
                {
                    "baseUrl": "https://api.example.com/v1",
                    "provider": "openai",
                    "apiKey": "sk-test",
                },
                getter=getter,
            )

        self.assertEqual(models, ["one", "two"])
        self.assertEqual(seen["url"], "https://api.example.com/v1/models")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-test")

    def test_anthropic_gets_its_own_authentication_headers(self) -> None:
        seen: dict[str, object] = {}

        def getter(url, *, headers, timeout):
            seen["headers"] = headers
            return _ModelsResponse({"data": [{"id": "claude"}]})

        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            models = list_models(
                {
                    "baseUrl": "https://api.anthropic.com/v1",
                    "provider": "anthropic",
                    "apiKey": "sk-anthropic",
                },
                getter=getter,
            )

        self.assertEqual(models, ["claude"])
        headers = seen["headers"]
        self.assertEqual(headers["x-api-key"], "sk-anthropic")
        self.assertNotIn("Authorization", headers)
        self.assertIn("anthropic-version", headers)

    def test_a_rejected_or_missing_model_list_explains_itself(self) -> None:
        cases = (
            (401, "settings.probeRejected"),
            (404, "settings.modelsUnavailable"),
            (500, "settings.probeFailed"),
        )
        for status, code in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp, (
                isolated_env()
            ), settings_home(tmp):

                def getter(url, *, headers, timeout, status=status):
                    return _ModelsResponse(status_code=status, text="no")

                with self.assertRaises(Refusal) as caught:
                    list_models(
                        {
                            "baseUrl": "https://api.example.com/v1",
                            "provider": "openai",
                            "apiKey": "k",
                        },
                        getter=getter,
                    )
                self.assertEqual(caught.exception.code, code)

    def test_a_stored_key_is_not_sent_to_another_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            created = create_profile(
                {
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "apiKey": "sk-stored",
                    "revision": 0,
                },
                path=path,
                credentials=credentials,
            )
            profile_id = created["profiles"][0]["id"]

            seen: dict[str, object] = {}

            def getter(url, *, headers, timeout):
                seen["headers"] = headers
                return _ModelsResponse({"data": [{"id": "m"}]})

            # A different origin is asked without the key, never with it.
            self.assertEqual(
                list_models(
                    {
                        "baseUrl": "https://evil.example/v1",
                        "provider": "openai",
                        "profileId": profile_id,
                    },
                    path=path,
                    credentials=credentials,
                    getter=getter,
                ),
                ["m"],
            )
            self.assertNotIn("Authorization", seen["headers"])

            self.assertEqual(
                list_models(
                    {
                        "baseUrl": "https://api.openai.com/v1",
                        "provider": "openai",
                        "profileId": profile_id,
                    },
                    path=path,
                    credentials=credentials,
                    getter=getter,
                ),
                ["m"],
            )
            self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-stored")

    def test_a_local_endpoint_without_a_key_is_still_asked(self) -> None:
        seen: dict[str, object] = {}

        def getter(url, *, headers, timeout):
            seen["headers"] = headers
            return _ModelsResponse({"data": [{"id": "qwen2.5:7b"}]})

        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            # A saved cloud connection must not block a keyless local endpoint.
            create_profile(
                {
                    "name": "Cloud",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "apiKey": "sk-cloud",
                    "revision": 0,
                }
            )
            models = list_models(
                {
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "provider": "ollama",
                },
                getter=getter,
            )

        self.assertEqual(models, ["qwen2.5:7b"])
        self.assertNotIn("Authorization", seen["headers"])


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
    def test_the_deep_check_route_returns_its_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            answer = {
                "ok": False,
                "checks": [
                    {
                        "name": "toolCalling",
                        "ok": False,
                        "code": "settings.toolCallingUnsupported",
                    }
                ],
            }
            with patch("noteagent.api.app.deep_check", return_value=answer) as checked:
                response = client.post("/api/settings/deep-check", json={})

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), answer)
            checked.assert_called_once()

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

    def test_search_mode_saves_through_the_settings_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            saved = client.put(
                "/api/settings",
                json={"search": {"mode": "external", "country": "cn"}},
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["search"]["mode"], "external")
            self.assertEqual(saved.json()["search"]["country"], "CN")
            self.assertEqual(client.get("/api/settings").json()["search"]["mode"], "external")

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


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ProfileSettingsApiTests(unittest.TestCase):
    """The connection-profile screen's requests, through the API it calls."""

    def test_create_returns_the_profile_without_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            client = TestClient(create_app())
            created = client.post(
                "/api/settings/profiles",
                json={
                    "name": "DeepSeek 主账号",
                    "provider": "deepseek",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "apiKey": "sk-secret",
                    "revision": 0,
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            body = created.json()
            self.assertEqual([profile["name"] for profile in body["profiles"]], ["DeepSeek 主账号"])
            profile = body["profiles"][0]
            self.assertTrue(profile["active"])
            self.assertTrue(profile["apiKeySet"])
            self.assertEqual(body["activeProfileId"], profile["id"])
            self.assertEqual(body["revision"], 1)
            self.assertNotIn("sk-secret", created.text)
            self.assertEqual(credentials.values[llm_key(profile["id"])], "sk-secret")
            self.assertTrue(path.is_file())

    def test_editing_keeps_a_blank_key_and_advances_the_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            _path,
            credentials,
        ):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "apiKey": "sk-main",
                    "revision": 0,
                },
            ).json()
            profile_id = first["profiles"][0]["id"]

            edited = client.put(
                f"/api/settings/profiles/{profile_id}",
                json={
                    "name": "Work",
                    "model": "gpt-4.1",
                    "revision": first["revision"],
                },
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            body = edited.json()
            self.assertEqual(body["profiles"][0]["name"], "Work")
            self.assertEqual(body["profiles"][0]["model"], "gpt-4.1")
            self.assertTrue(body["profiles"][0]["apiKeySet"])
            self.assertEqual(body["revision"], first["revision"] + 1)
            self.assertEqual(credentials.values[llm_key(profile_id)], "sk-main")
            self.assertNotIn("sk-main", edited.text)

            cleared = client.put(
                f"/api/settings/profiles/{profile_id}",
                json={"clearApiKey": True, "revision": body["revision"]},
            )
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertFalse(cleared.json()["profiles"][0]["apiKeySet"])
            self.assertNotIn(llm_key(profile_id), credentials.values)
            self.assertEqual(cleared.json()["revision"], body["revision"] + 1)

    def test_a_stale_revision_is_a_409_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "revision": 0,
                },
            ).json()
            profile_id = first["profiles"][0]["id"]

            stale = client.put(
                f"/api/settings/profiles/{profile_id}",
                json={"model": "other", "revision": 0},
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(stale.json()["code"], "settings.staleRevision")
            current = client.get("/api/settings").json()
            self.assertEqual(current["profiles"][0]["model"], "gpt-4o-mini")

            refused = client.delete(f"/api/settings/profiles/{profile_id}?revision=0")
            self.assertEqual(refused.status_code, 409, refused.text)
            self.assertEqual(refused.json()["code"], "settings.staleRevision")

    def test_an_unknown_profile_is_a_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            for method, path, body in (
                ("put", "/api/settings/profiles/p_9", {"revision": 0}),
                ("post", "/api/settings/profiles/p_9/activate", {"revision": 0}),
                ("delete", "/api/settings/profiles/p_9?revision=0", None),
            ):
                call = getattr(client, method)
                answer = call(path, json=body) if body is not None else call(path)
                self.assertEqual(answer.status_code, 404, answer.text)
                self.assertEqual(answer.json()["code"], "settings.profileNameNotFound")

    def test_switching_the_active_profile_is_reflected_in_the_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "One",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m-one",
                    "revision": 0,
                },
            ).json()
            second = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Two",
                    "provider": "ollama",
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "model": "m-two",
                    "revision": first["revision"],
                },
            ).json()
            self.assertEqual(second["activeProfileId"], first["activeProfileId"])
            target = second["profiles"][1]["id"]

            switched = client.post(
                f"/api/settings/profiles/{target}/activate",
                json={"revision": second["revision"]},
            )
            self.assertEqual(switched.status_code, 200, switched.text)
            body = switched.json()
            self.assertEqual(body["activeProfileId"], target)
            self.assertEqual(
                [profile["active"] for profile in body["profiles"]], [False, True]
            )
            self.assertEqual(body["llm"]["baseUrl"], "http://127.0.0.1:11434/v1")
            self.assertEqual(body["revision"], second["revision"] + 1)

    def test_deleting_the_active_profile_selects_the_next_and_removes_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            _path,
            credentials,
        ):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "One",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m-one",
                    "apiKey": "sk-one",
                    "revision": 0,
                },
            ).json()
            second = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Two",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m-two",
                    "revision": first["revision"],
                },
            ).json()
            active_id = second["activeProfileId"]
            other_id = next(
                profile["id"] for profile in second["profiles"] if profile["id"] != active_id
            )

            deleted = client.delete(
                f"/api/settings/profiles/{active_id}?revision={second['revision']}"
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            body = deleted.json()
            self.assertEqual([profile["id"] for profile in body["profiles"]], [other_id])
            self.assertEqual(body["activeProfileId"], other_id)
            self.assertEqual(credentials.values, {})

    def test_deleting_the_last_profile_returns_the_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Only",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "revision": 0,
                },
            ).json()
            deleted = client.delete(
                f"/api/settings/profiles/{first['profiles'][0]['id']}?revision={first['revision']}"
            )
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(deleted.json()["profiles"], [])
            self.assertIsNone(deleted.json()["activeProfileId"])

    def test_a_key_that_cannot_be_stored_leaves_the_document_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(
            tmp, available=False
        ) as (path, credentials):
            client = TestClient(create_app())
            refused = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "apiKey": "sk-main",
                    "revision": 0,
                },
            )
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "settings.keychainUnavailable")
            self.assertFalse(path.exists())
            self.assertEqual(credentials.values, {})

    def test_a_key_store_that_fails_mid_save_leaves_no_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            path,
            credentials,
        ):
            client = TestClient(create_app())
            # Available when the save starts, gone when the key is written: the
            # document must not be committed for a save that failed.
            credentials.fail_writes = True
            refused = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "apiKey": "sk-main",
                    "revision": 0,
                },
            )
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "settings.keychainUnavailable")
            self.assertFalse(path.exists())
            self.assertEqual(credentials.values, {})

    def test_a_failed_key_write_on_edit_keeps_the_previous_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp) as (
            _path,
            credentials,
        ):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m",
                    "revision": 0,
                },
            ).json()
            profile_id = first["profiles"][0]["id"]
            credentials.fail_writes = True

            refused = client.put(
                f"/api/settings/profiles/{profile_id}",
                json={"model": "other", "apiKey": "sk-new", "revision": first["revision"]},
            )
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(refused.json()["code"], "settings.keychainUnavailable")

            view = client.get("/api/settings").json()
            self.assertEqual(view["profiles"][0]["model"], "m")
            self.assertEqual(view["revision"], first["revision"])
            self.assertFalse(view["profiles"][0]["apiKeySet"])

    def test_generation_is_global_and_survives_a_profile_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            first = client.post(
                "/api/settings/profiles",
                json={
                    "name": "One",
                    "provider": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "m-one",
                    "revision": 0,
                },
            ).json()
            second = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Two",
                    "provider": "ollama",
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "model": "m-two",
                    "revision": first["revision"],
                },
            ).json()
            saved = client.put(
                "/api/settings",
                json={
                    "llm": {
                        "temperature": 0.4,
                        "maxTokens": 2048,
                        "timeoutSeconds": 90,
                    },
                    "revision": second["revision"],
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["generation"]["temperature"], 0.4)

            target = second["profiles"][1]["id"]
            switched = client.post(
                f"/api/settings/profiles/{target}/activate",
                json={"revision": saved.json()["revision"]},
            )
            self.assertEqual(switched.status_code, 200, switched.text)
            body = switched.json()
            self.assertEqual(body["llm"]["temperature"], 0.4)
            self.assertEqual(body["llm"]["maxTokens"], 2048)
            self.assertEqual(body["llm"]["timeoutSeconds"], 90)

    def test_a_generation_value_from_the_environment_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            os.environ["NOTEAGENT_LLM_TEMPERATURE"] = "0.7"
            reset_process_env_keys()
            client = TestClient(create_app())
            view = client.get("/api/settings").json()
            self.assertEqual(view["llm"]["temperature"], 0.7)
            self.assertEqual(view["sources"]["llm.temperature"], "env")
            self.assertIn("llm.temperature", view["locked"])

    def test_a_supported_profile_reports_native_search_available(self) -> None:
        cases = (
            ("openai", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("anthropic", "https://api.anthropic.com/v1", "claude-sonnet-5-5"),
        )
        for provider, base_url, model in cases:
            with (
                self.subTest(provider=provider),
                tempfile.TemporaryDirectory() as tmp,
                isolated_env(),
                settings_home(tmp),
            ):
                client = TestClient(create_app())
                created = client.post(
                    "/api/settings/profiles",
                    json={
                        "name": "Main",
                        "provider": provider,
                        "baseUrl": base_url,
                        "model": model,
                        "revision": 0,
                    },
                )
                self.assertEqual(created.status_code, 200, created.text)
                view = client.get("/api/settings").json()
                self.assertTrue(view["search"]["nativeAvailable"])

    def test_a_profile_save_refuses_a_value_the_document_could_not_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, isolated_env(), settings_home(tmp):
            client = TestClient(create_app())
            refused = client.post(
                "/api/settings/profiles",
                json={
                    "name": "Main",
                    "provider": "unknown",
                    "baseUrl": "notaurl",
                    "model": "m",
                    "revision": 0,
                },
            )
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertEqual(refused.json()["code"], "settings.invalid")
            self.assertEqual(client.get("/api/settings").json()["profiles"], [])
