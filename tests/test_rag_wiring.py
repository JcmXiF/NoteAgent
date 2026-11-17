"""The RAG switch at the API seam: off, unusable, and a real backend."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from noteagent.api.session_access import _rag_backend
from noteagent.project.layout import ProjectLayout
from noteagent.rag.search import RagSearchBackend, reset_backends

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
            self.assertIsNone(_rag_backend(self.layout))

    def test_an_unusable_config_returns_a_degraded_backend(self) -> None:
        unusable = dict(BASE_ENV, NOTEAGENT_RAG_EMBEDDING_ENDPOINT="")

        with rag_env(**unusable):
            backend = _rag_backend(self.layout)
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
            backend = _rag_backend(self.layout)

        self.assertIsInstance(backend, RagSearchBackend)


if __name__ == "__main__":
    unittest.main()
