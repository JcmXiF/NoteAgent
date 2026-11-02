"""The configuration document, its precedence rules and its credential store."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from noteagent.settings.credentials import (
    KEYCHAIN_SERVICE,
    SEARCH_KEY,
    KeyringCredentialStore,
    llm_key,
)
from noteagent.settings.effective import (
    SOURCE_CONFIG,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_KEYCHAIN,
    apply,
    resolve,
)
from noteagent.settings.store import (
    Config,
    ConfigStore,
    GenerationSettings,
    Profile,
    SearchSettings,
    default_config,
    dump_config,
    parse_config,
    remove_profile,
    set_active_profile,
)
from noteagent.util.refusal import Refusal
from fakes import FakeCredentials


def sample_config(revision: int = 0) -> Config:
    return Config(
        profiles=(
            Profile("p_1", "OpenAI", "openai", "https://api.openai.com/v1", "gpt-4o-mini"),
            Profile("p_2", "Local", "ollama", "http://localhost:11434/v1", "llama3"),
        ),
        active_profile_id="p_1",
        generation=GenerationSettings(temperature=0.2, max_tokens=2048, timeout_seconds=90),
        search=SearchSettings(
            mode="auto",
            external_provider="brave",
            timeout_seconds=20,
            country="CN",
            search_lang="zh-hans",
            ui_lang="zh-CN",
        ),
        extract_media=False,
        revision=revision,
    )


class SchemaTests(unittest.TestCase):
    def test_the_empty_state_is_one_document(self) -> None:
        config = default_config()
        self.assertEqual(config.profiles, ())
        self.assertIsNone(config.active_profile_id)
        self.assertEqual(config.revision, 0)

    def test_a_document_round_trips(self) -> None:
        config = sample_config(revision=4)
        parsed = parse_config(json.loads(dump_config(config)))
        self.assertEqual(parsed, config)

    def test_a_document_is_always_versioned(self) -> None:
        text = dump_config(default_config())
        self.assertIn('"schemaVersion": 1', text)
        with self.assertRaises(Refusal) as caught:
            parse_config({"profiles": [], "activeProfileId": None})
        self.assertEqual(caught.exception.code, "settings.configVersion")

    def test_a_secret_in_the_document_is_refused(self) -> None:
        raw = {
            "schemaVersion": 1,
            "profiles": [
                {
                    "id": "p_1",
                    "name": "One",
                    "provider": "openai",
                    "baseUrl": "https://example.com/v1",
                    "model": "m",
                    "apiKey": "must-not-be-here",
                }
            ],
            "activeProfileId": "p_1",
        }
        with self.assertRaises(Refusal) as caught:
            parse_config(raw)
        self.assertEqual(caught.exception.code, "settings.configInvalid")
        self.assertNotIn("must-not-be-here", str(caught.exception))

    def test_exactly_one_profile_is_active(self) -> None:
        with self.assertRaises(Refusal) as caught:
            parse_config(
                {
                    "schemaVersion": 1,
                    "profiles": [
                        {
                            "id": "p_1",
                            "name": "One",
                            "provider": "custom",
                            "baseUrl": "https://example.com/v1",
                            "model": "m",
                        }
                    ],
                    "activeProfileId": None,
                }
            )
        self.assertEqual(caught.exception.code, "settings.configInvalid")
        self.assertEqual(caught.exception.params["field"], "activeProfileId")

    def test_duplicate_ids_are_refused(self) -> None:
        profile = {
            "id": "p_1",
            "name": "One",
            "provider": "custom",
            "baseUrl": "https://example.com/v1",
            "model": "m",
        }
        with self.assertRaises(Refusal) as caught:
            parse_config(
                {"schemaVersion": 1, "profiles": [profile, profile], "activeProfileId": "p_1"}
            )
        self.assertEqual(caught.exception.code, "settings.configInvalid")

    def test_values_outside_their_range_are_refused(self) -> None:
        cases = [
            ("generation", {"temperature": 3}),
            ("generation", {"maxTokens": 0}),
            ("generation", {"timeoutSeconds": 2}),
            ("search", {"timeoutSeconds": 0}),
            ("search", {"externalProvider": "tavily"}),
            ("search", {"mode": "sometimes"}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                raw = {
                    "schemaVersion": 1,
                    "profiles": [],
                    "activeProfileId": None,
                    key: value,
                }
                with self.assertRaises(Refusal) as caught:
                    parse_config(raw)
                self.assertEqual(caught.exception.code, "settings.configInvalid")


class ActiveProfileTests(unittest.TestCase):
    def test_removing_the_active_profile_selects_the_next(self) -> None:
        config = sample_config()
        after = remove_profile(config, "p_1")
        self.assertEqual([p.id for p in after.profiles], ["p_2"])
        self.assertEqual(after.active_profile_id, "p_2")

    def test_removing_a_later_active_selects_the_previous(self) -> None:
        config = replace(sample_config(), active_profile_id="p_2")
        after = remove_profile(config, "p_2")
        self.assertEqual(after.active_profile_id, "p_1")

    def test_removing_an_inactive_profile_keeps_the_active_one(self) -> None:
        config = sample_config()
        after = remove_profile(config, "p_2")
        self.assertEqual(after.active_profile_id, "p_1")

    def test_removing_the_last_profile_returns_the_empty_state(self) -> None:
        config = replace(
            sample_config(),
            profiles=(sample_config().profiles[0],),
            active_profile_id="p_1",
        )
        after = remove_profile(config, "p_1")
        self.assertEqual(after.profiles, ())
        self.assertIsNone(after.active_profile_id)

    def test_an_unknown_profile_is_a_refusal(self) -> None:
        with self.assertRaises(Refusal) as caught:
            remove_profile(sample_config(), "p_9")
        self.assertEqual(caught.exception.code, "settings.profileNameNotFound")

    def test_switching_active_requires_an_existing_profile(self) -> None:
        config = set_active_profile(sample_config(), "p_2")
        self.assertEqual(config.active_profile_id, "p_2")
        with self.assertRaises(Refusal):
            set_active_profile(config, "p_9")


class StoreTests(unittest.TestCase):
    def test_a_missing_file_is_the_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "config.json", FakeCredentials())
            self.assertFalse(store.exists())
            self.assertEqual(store.read(), default_config())

    def test_a_save_is_revisioned_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = ConfigStore(path, FakeCredentials())
            first = store.update(lambda config: sample_config())
            self.assertEqual(first.revision, 1)
            self.assertTrue(path.is_file())
            second = store.update(
                lambda config: replace(config, generation=replace(config.generation, temperature=0.7))
            )
            self.assertEqual(second.revision, 2)
            self.assertEqual(store.read(), second)
            # Nothing half-written is left beside the document.
            self.assertEqual(
                [p.name for p in path.parent.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")],
                [],
            )

    def test_an_unchanged_save_does_not_advance_the_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "config.json", FakeCredentials())
            first = store.update(lambda config: config)
            self.assertEqual(first.revision, 0)
            self.assertFalse(store.exists())

    def test_a_stale_revision_is_refused_and_nothing_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            store = ConfigStore(path, FakeCredentials())
            first = store.update(lambda config: sample_config(), expected_revision=0)
            self.assertEqual(first.revision, 1)
            with self.assertRaises(Refusal) as caught:
                store.update(
                    lambda config: replace(config, extract_media=False),
                    expected_revision=0,
                )
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(caught.exception.code, "settings.staleRevision")
            self.assertEqual(store.read(), first)

    def test_concurrent_saves_are_serialised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "config.json", FakeCredentials())
            barrier = threading.Barrier(4)
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    barrier.wait()
                    store.update(
                        lambda config: replace(
                            config,
                            generation=replace(
                                config.generation,
                                temperature=config.generation.temperature + 0.1,
                            ),
                        )
                    )
                except BaseException as err:  # pragma: no cover - reported below
                    errors.append(err)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            final = store.read()
            self.assertEqual(final.revision, 4)
            self.assertAlmostEqual(final.generation.temperature, 0.1 + 0.4)

    def test_a_damaged_file_is_refused_not_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            store = ConfigStore(path, FakeCredentials())
            with self.assertRaises(Refusal) as caught:
                store.read()
            self.assertEqual(caught.exception.code, "settings.configInvalid")
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_keys_are_written_to_the_credential_store_never_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            credentials = FakeCredentials()
            store = ConfigStore(path, credentials)
            store.update(lambda config: sample_config())
            store.set_llm_key("p_1", "sk-secret")
            store.set_search_key("brave-secret")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-secret", text)
            self.assertNotIn("brave-secret", text)
            self.assertEqual(credentials.values[llm_key("p_1")], "sk-secret")
            self.assertEqual(credentials.values[SEARCH_KEY], "brave-secret")
            self.assertEqual(store.get_llm_key("p_1"), "sk-secret")
            self.assertEqual(store.get_search_key(), "brave-secret")

    def test_a_missing_credential_store_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp) / "config.json", FakeCredentials(available=False))
            self.assertFalse(store.keychain_available())
            for operation in (
                lambda: store.require_keychain(),
                lambda: store.set_llm_key("p_1", "secret"),
                lambda: store.delete_search_key(),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaises(Refusal) as caught:
                        operation()
                    self.assertEqual(caught.exception.code, "settings.keychainUnavailable")

    def test_deleting_a_profile_removes_its_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            credentials = FakeCredentials()
            store = ConfigStore(Path(tmp) / "config.json", credentials)
            store.update(lambda config: sample_config())
            store.set_llm_key("p_1", "secret")
            after = store.delete_profile("p_1")
            self.assertEqual(after.active_profile_id, "p_2")
            self.assertNotIn(llm_key("p_1"), credentials.values)


class _PasswordDeleteError(Exception):
    pass


class FakeKeyring:
    """The small part of ``keyring`` the adapter uses, with injectable faults."""

    def __init__(self, backend: object | None = None, fail: str = "") -> None:
        self.backend = backend if backend is not None else object()
        self.values: dict[tuple[str, str], str] = {}
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []
        self.errors = SimpleNamespace(PasswordDeleteError=_PasswordDeleteError)

    def get_keyring(self) -> object:
        if self.fail == "backend":
            raise RuntimeError("no backend")
        return self.backend

    def get_password(self, service: str, key: str) -> str | None:
        self.calls.append(("get", service, key))
        if self.fail == "read":
            raise RuntimeError("locked")
        return self.values.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.calls.append(("set", service, key))
        if self.fail == "write":
            raise RuntimeError("locked")
        self.values[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self.calls.append(("delete", service, key))
        if self.fail == "delete":
            raise RuntimeError("locked")
        if (service, key) not in self.values:
            raise _PasswordDeleteError(key)
        del self.values[(service, key)]


class KeyringStoreTests(unittest.TestCase):
    def test_a_key_round_trips_under_one_service(self) -> None:
        module = FakeKeyring()
        store = KeyringCredentialStore(KEYCHAIN_SERVICE, module)
        self.assertTrue(store.available())
        store.set("k", "v")
        self.assertEqual(store.get("k"), "v")
        store.delete("k")
        self.assertIsNone(store.get("k"))
        self.assertTrue(all(service == KEYCHAIN_SERVICE for _, service, _ in module.calls))

    def test_the_null_backend_is_reported_unavailable(self) -> None:
        from keyring.backends import fail

        store = KeyringCredentialStore(KEYCHAIN_SERVICE, FakeKeyring(backend=fail.Keyring()))
        self.assertFalse(store.available())
        with self.assertRaises(Refusal) as caught:
            store.require()
        self.assertEqual(caught.exception.code, "settings.keychainUnavailable")

    def test_a_backend_that_cannot_be_loaded_is_unavailable(self) -> None:
        store = KeyringCredentialStore(KEYCHAIN_SERVICE, FakeKeyring(fail="backend"))
        self.assertFalse(store.available())

    def test_a_backend_failure_becomes_a_coded_refusal(self) -> None:
        for operation, fault in (
            (lambda store: store.get("k"), "read"),
            (lambda store: store.set("k", "v"), "write"),
            (lambda store: store.delete("k"), "delete"),
        ):
            with self.subTest(fault=fault):
                store = KeyringCredentialStore(KEYCHAIN_SERVICE, FakeKeyring(fail=fault))
                with self.assertRaises(Refusal) as caught:
                    operation(store)
                self.assertEqual(caught.exception.code, "settings.keychainUnavailable")

    def test_deleting_a_missing_password_is_not_a_failure(self) -> None:
        store = KeyringCredentialStore(KEYCHAIN_SERVICE, FakeKeyring())
        store.delete("never-stored")


class PrecedenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.credentials = FakeCredentials()
        self.store = ConfigStore(Path(self.tmp.name) / "config.json", self.credentials)

    def test_the_document_is_used_when_no_environment_overrides_it(self) -> None:
        self.store.update(lambda config: sample_config())
        effective = resolve(
            self.store.read(),
            self.store,
            process_keys=frozenset(),
            environ={},
        )
        self.assertEqual(effective.model.text, "gpt-4o-mini")
        self.assertEqual(effective.model.source, SOURCE_CONFIG)
        self.assertEqual(effective.base_url.text, "https://api.openai.com/v1")
        self.assertEqual(effective.search_provider.text, "brave")
        self.assertEqual(effective.temperature.text, "0.2")
        self.assertEqual(effective.extract_media.text, "0")

    def test_the_process_environment_wins_and_is_reported(self) -> None:
        self.store.update(lambda config: sample_config())
        effective = resolve(
            self.store.read(),
            self.store,
            process_keys=frozenset({"NOTEAGENT_LLM_MODEL"}),
            environ={"NOTEAGENT_LLM_MODEL": "from-env"},
        )
        self.assertEqual(effective.model.text, "from-env")
        self.assertEqual(effective.model.source, SOURCE_ENV)
        self.assertTrue(effective.model.locked)
        self.assertIn("llm.model", effective.locked())

    def test_a_name_added_after_startup_is_not_a_source(self) -> None:
        # The snapshot is what the process was started with; a value written
        # into the environment later is not an outside override and must not
        # become one by accident.
        effective = resolve(
            default_config(),
            self.store,
            file_exists=False,
            process_keys=frozenset(),
            environ={"NOTEAGENT_LLM_MODEL": "late"},
        )
        self.assertEqual(effective.model.text, "")
        self.assertEqual(effective.model.source, SOURCE_DEFAULT)

    def test_a_key_in_the_credential_store_is_the_source(self) -> None:
        self.store.update(lambda config: sample_config())
        self.credentials.values[llm_key("p_1")] = "from-keychain"
        self.credentials.values[SEARCH_KEY] = "search-key"
        effective = resolve(self.store.read(), self.store, process_keys=frozenset(), environ={})
        self.assertEqual(effective.llm_key.text, "from-keychain")
        self.assertEqual(effective.llm_key.source, SOURCE_KEYCHAIN)
        self.assertEqual(effective.search_key.text, "search-key")
        self.assertTrue(effective.keychain_available)

    def test_an_environment_secret_wins_over_the_credential_store(self) -> None:
        self.store.update(lambda config: sample_config())
        self.credentials.values[llm_key("p_1")] = "from-keychain"
        effective = resolve(
            self.store.read(),
            self.store,
            process_keys=frozenset({"NOTEAGENT_LLM_API_KEY"}),
            environ={"NOTEAGENT_LLM_API_KEY": "from-env"},
        )
        self.assertEqual(effective.llm_key.text, "from-env")
        self.assertEqual(effective.llm_key.source, SOURCE_ENV)

    def test_an_unavailable_store_leaves_the_secret_unset_without_failing_the_view(self) -> None:
        unavailable = ConfigStore(Path(self.tmp.name) / "other.json", FakeCredentials(available=False))
        effective = resolve(
            default_config(),
            unavailable,
            file_exists=False,
            process_keys=frozenset(),
            environ={},
        )
        self.assertEqual(effective.llm_key.text, "")
        self.assertFalse(effective.keychain_available)

    def test_applying_writes_only_values_the_document_owns(self) -> None:
        self.store.update(lambda config: sample_config())
        effective = resolve(self.store.read(), self.store, process_keys=frozenset(), environ={})
        target: dict[str, str] = {}
        apply(effective, process_keys=frozenset(), environ=target)
        self.assertEqual(target["NOTEAGENT_LLM_MODEL"], "gpt-4o-mini")
        self.assertEqual(target["NOTEAGENT_SEARCH_PROVIDER"], "brave")
        self.assertEqual(target["NOTEAGENT_EXTRACT_MEDIA"], "0")

    def test_applying_never_touches_a_process_owned_name(self) -> None:
        self.store.update(lambda config: sample_config())
        effective = resolve(
            self.store.read(),
            self.store,
            process_keys=frozenset({"NOTEAGENT_LLM_MODEL"}),
            environ={"NOTEAGENT_LLM_MODEL": "outside"},
        )
        target = {"NOTEAGENT_LLM_MODEL": "outside"}
        apply(effective, process_keys=frozenset({"NOTEAGENT_LLM_MODEL"}), environ=target)
        self.assertEqual(target["NOTEAGENT_LLM_MODEL"], "outside")
        self.assertEqual(target["NOTEAGENT_LLM_BASE_URL"], "https://api.openai.com/v1")


if __name__ == "__main__":
    unittest.main()
