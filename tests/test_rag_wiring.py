"""The RAG switch at the API seam: off, unusable, and a real backend."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from noteagent.api.session_access import rag_backend
from noteagent.project.layout import ProjectLayout
from noteagent.rag.search import RagSearchBackend, reset_backends
from noteagent.settings.install import apply_install
from fakes import FakeCredentials

BASE_ENV = {
    "NOTEAGENT_RAG_ENABLED": "true",
    "NOTEAGENT_RAG_EMBEDDING_ENDPOINT": "http://fake/v1",
    "NOTEAGENT_RAG_EMBEDDING_MODEL": "fake-model",
    # Presence wins over the credential store, so the test never opens it.
    "NOTEAGENT_RAG_EMBEDDING_API_KEY": "",
}


@contextmanager
def rag_env(**values: str):
    source = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("NOTEAGENT_RAG_")
    }
    source.update(values)
    with patch.dict(os.environ, source, clear=True):
        yield


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.layout = ProjectLayout(Path(self._tmp.name) / "project")
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def test_rag_off_returns_no_backend(self) -> None:
        with rag_env():
            self.assertIsNone(rag_backend(self.layout))

    def test_an_unusable_config_returns_a_degraded_backend(self) -> None:
        unusable = dict(BASE_ENV, NOTEAGENT_RAG_EMBEDDING_ENDPOINT="")

        with rag_env(**unusable):
            backend = rag_backend(self.layout)
            result = backend.search("query")

        self.assertTrue(result.degraded)
        self.assertEqual(result.mode, "lexical")
        self.assertTrue(
            any(
                "embedding service" in warning
                for warning in result.warnings
            )
        )

    def test_a_valid_config_returns_a_project_backend(self) -> None:
        with rag_env(**BASE_ENV):
            backend = rag_backend(self.layout)

        self.assertIsInstance(backend, RagSearchBackend)


class SettingsWiringTests(unittest.TestCase):
    """A saved switch and service reach the next run without a restart."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.layout = ProjectLayout(Path(self._tmp.name) / "project")
        self.path = Path(self._tmp.name) / "config.json"
        self.credentials = FakeCredentials()
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def _save(self, body: dict[str, object]) -> None:
        with (
            patch(
                "noteagent.settings.install.default_config_path",
                lambda: self.path,
            ),
            patch(
                "noteagent.settings.install.keyring_store",
                lambda: self.credentials,
            ),
        ):
            apply_install(body)

    def test_a_saved_switch_builds_the_backend_on_the_next_run(self) -> None:
        with rag_env(
            **{
                "NOTEAGENT_RAG_EMBEDDING_API_KEY": "",
                "NOTEAGENT_RAG_RERANK_API_KEY": "",
            }
        ):
            self._save(
                {
                    "rag": {
                        "enabled": True,
                        "embedding": {
                            "endpoint": "http://fake/v1",
                            "model": "fake-model",
                        },
                    }
                }
            )
            backend = rag_backend(self.layout)

        self.assertIsInstance(backend, RagSearchBackend)

    def test_turning_the_switch_off_returns_the_lexical_path(self) -> None:
        with rag_env(
            **{
                "NOTEAGENT_RAG_EMBEDDING_API_KEY": "",
                "NOTEAGENT_RAG_RERANK_API_KEY": "",
            }
        ):
            self._save(
                {
                    "rag": {
                        "enabled": True,
                        "embedding": {
                            "endpoint": "http://fake/v1",
                            "model": "fake-model",
                        },
                    }
                }
            )
            self.assertIsInstance(rag_backend(self.layout), RagSearchBackend)

            self._save({"rag": {"enabled": False}})
            self.assertIsNone(rag_backend(self.layout))


if __name__ == "__main__":
    unittest.main()
