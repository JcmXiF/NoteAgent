"""The settings screen's RAG status and rebuild, through the HTTP API.

Every case runs against a temporary project, a fake embedder and a process
environment of its own: no network, no real credential store, no saved user
settings.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from noteagent.project.init import init_project
from noteagent.rag.embedding import EmbeddingError
from noteagent.rag.faux import FakeEmbedder
from noteagent.rag.search import reset_backends

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
except Exception:  # pragma: no cover - depends on the API extra
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]

DIM = 8

BASE_ENV = {
    "NOTEAGENT_RAG_ENABLED": "true",
    "NOTEAGENT_RAG_EMBEDDING_ENDPOINT": "http://fake/v1",
    "NOTEAGENT_RAG_EMBEDDING_MODEL": "fake-model",
    # Presence wins over the credential store, so the test never opens it.
    "NOTEAGENT_RAG_EMBEDDING_API_KEY": "",
    "NOTEAGENT_RAG_RERANK_API_KEY": "",
}


@contextmanager
def api_env(project: Path | None, **values: str):
    """A process environment holding only what this case sets."""
    source = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("NOTEAGENT_RAG_")
        and key
        not in {"NOTEAGENT_PROJECT", "NOTEAGENT_HOME", "NOTEAGENT_CONFIG_DIR"}
    }
    if project is not None:
        source["NOTEAGENT_PROJECT"] = str(project)
    source.update(values)
    with patch.dict(os.environ, source, clear=True):
        yield


class FailingEmbedder:
    """An embedding service that is down; it opens no socket."""

    dim = DIM

    def embed(self, texts, *, query: bool = False):
        raise EmbeddingError("the service is down")


def fake_client(_config):
    return FakeEmbedder(dim=DIM)


def write_page(project: Path, slug: str, title: str, body: str) -> None:
    path = project / "wiki" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntitle: "{title}"\n---\n\n{body}\n',
        encoding="utf-8",
    )


@unittest.skipIf(TestClient is None, "fastapi not installed")
class RagStatusApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = init_project(self.root / "project").root
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def test_rag_off_answers_disabled_without_a_project(self) -> None:
        with api_env(None):
            response = TestClient(create_app()).get("/api/settings/rag/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "disabled")
        self.assertEqual(body["units"], 0)
        self.assertEqual(body["chunks"], 0)
        self.assertEqual(body["failed"], 0)

    def test_no_active_project_answers_unavailable_not_500(self) -> None:
        with api_env(self.root / "missing", **BASE_ENV):
            response = TestClient(create_app()).get("/api/settings/rag/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "unavailable")
        self.assertIn("project", body["reason"].lower())

    def test_an_incomplete_service_answers_unavailable_with_a_reason(self) -> None:
        with api_env(
            self.project,
            **dict(BASE_ENV, NOTEAGENT_RAG_EMBEDDING_ENDPOINT=""),
        ):
            response = TestClient(create_app()).get("/api/settings/rag/status")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["state"], "unavailable")
        self.assertTrue(body["reason"])


@unittest.skipIf(TestClient is None, "fastapi not installed")
class RagRebuildApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = init_project(self.root / "project").root
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def test_rebuild_builds_the_index_and_status_follows(self) -> None:
        write_page(self.project, "a", "A", "alpha")

        with api_env(self.project, **BASE_ENV):
            with patch(
                "noteagent.rag.search.OpenAIEmbeddingClient",
                fake_client,
            ):
                client = TestClient(create_app())
                before = client.get("/api/settings/rag/status")
                rebuilt = client.post("/api/settings/rag/rebuild")
                after = client.get("/api/settings/rag/status")

        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["state"], "cold")
        self.assertEqual(before.json()["units"], 1)

        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        report = rebuilt.json()
        self.assertEqual(report["added"], 1)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["unchanged"], 0)
        self.assertEqual(report["deleted"], 0)
        self.assertGreater(report["embeddedChunks"], 0)
        self.assertEqual(report["failures"], [])
        self.assertTrue(report["rebuilt"])

        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json()["state"], "ready")
        self.assertEqual(after.json()["units"], 1)
        self.assertGreater(after.json()["chunks"], 0)

    def test_a_failed_page_still_returns_the_report(self) -> None:
        write_page(self.project, "a", "A", "alpha")

        with api_env(self.project, **BASE_ENV):
            with patch(
                "noteagent.rag.search.OpenAIEmbeddingClient",
                lambda _config: FailingEmbedder(),
            ):
                client = TestClient(create_app())
                rebuilt = client.post("/api/settings/rag/rebuild")
                status = client.get("/api/settings/rag/status")

        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)
        report = rebuilt.json()
        self.assertEqual(report["added"], 1)
        self.assertEqual(report["embeddedChunks"], 0)
        self.assertEqual(len(report["failures"]), 1)
        self.assertEqual(report["failures"][0]["pageId"], "a")
        self.assertIn("down", report["failures"][0]["reason"])

        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["state"], "stale")
        self.assertEqual(status.json()["failed"], 1)

    def test_rebuild_writes_no_settings_document(self) -> None:
        write_page(self.project, "a", "A", "alpha")
        config_path = self.root / "config.json"

        with api_env(self.project, **BASE_ENV):
            with (
                patch(
                    "noteagent.rag.search.OpenAIEmbeddingClient",
                    fake_client,
                ),
                patch(
                    "noteagent.settings.install.default_config_path",
                    lambda: config_path,
                ),
            ):
                response = TestClient(create_app()).post(
                    "/api/settings/rag/rebuild"
                )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(config_path.exists())

    def test_rebuild_is_refused_when_rag_is_off(self) -> None:
        with api_env(self.project):
            response = TestClient(create_app()).post(
                "/api/settings/rag/rebuild"
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "settings.ragDisabled")

    def test_rebuild_without_a_project_is_a_coded_refusal(self) -> None:
        with api_env(self.root / "missing", **BASE_ENV):
            response = TestClient(create_app()).post(
                "/api/settings/rag/rebuild"
            )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "project.notConfigured")

    def test_status_and_rebuild_follow_the_active_project(self) -> None:
        other = init_project(self.root / "other").root
        write_page(self.project, "a", "A", "alpha")
        write_page(other, "b1", "B1", "beta")
        write_page(other, "b2", "B2", "gamma")

        with patch(
            "noteagent.rag.search.OpenAIEmbeddingClient",
            fake_client,
        ):
            with api_env(self.project, **BASE_ENV):
                client = TestClient(create_app())
                first = client.get("/api/settings/rag/status")
            with api_env(other, **BASE_ENV):
                second = client.get("/api/settings/rag/status")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["units"], 1)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["units"], 2)
        self.assertEqual(second.json()["state"], "cold")


if __name__ == "__main__":
    unittest.main()
