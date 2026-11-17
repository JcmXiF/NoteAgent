"""The `noteagent rag` commands: disabled, unavailable, status and rebuild."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from noteagent.__main__ import cmd_rag_rebuild, cmd_rag_status
from noteagent.project.init import init_project
from noteagent.rag.index import IndexStatus, SyncFailure, SyncReport
from noteagent.rag.search import reset_backends

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


class FakeBackend:
    def __init__(
        self,
        *,
        status: str = "ready",
        report: SyncReport | None = None,
    ) -> None:
        self._status = status
        self._report = report or SyncReport()

    def status(self) -> IndexStatus:
        return IndexStatus(self._status)

    def rebuild(self) -> SyncReport:
        return self._report


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project = init_project(
            Path(self._tmp.name) / "project"
        ).root
        reset_backends()

    def tearDown(self) -> None:
        reset_backends()
        self._tmp.cleanup()

    def run_both(self, command, *args) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = command(*args)
        return code, out.getvalue(), err.getvalue()

    def test_status_reports_disabled(self) -> None:
        with rag_env():
            code, out, err = self.run_both(
                cmd_rag_status, self.project
            )

        self.assertEqual((code, err), (0, ""))
        self.assertIn("rag: disabled", out)

    def test_rebuild_reports_disabled(self) -> None:
        with rag_env():
            code, out, _err = self.run_both(
                cmd_rag_rebuild, self.project
            )

        self.assertEqual(code, 2)
        self.assertIn("rag: disabled", out)

    def test_status_reports_cold_for_an_enabled_project(self) -> None:
        with rag_env(**BASE_ENV):
            code, out, _err = self.run_both(
                cmd_rag_status, self.project
            )

        self.assertEqual(code, 0)
        self.assertIn("rag: cold", out)

    def test_status_reports_unavailable_without_an_endpoint(self) -> None:
        with rag_env(
            **dict(BASE_ENV, NOTEAGENT_RAG_EMBEDDING_ENDPOINT="")
        ):
            code, out, _err = self.run_both(
                cmd_rag_status, self.project
            )

        self.assertEqual(code, 2)
        self.assertIn("rag: unavailable", out)

    def test_rebuild_prints_the_report(self) -> None:
        backend = FakeBackend(
            report=SyncReport(
                added=1,
                updated=2,
                deleted=1,
                embedded_chunks=3,
            )
        )
        with rag_env(**BASE_ENV):
            with patch(
                "noteagent.rag.search.get_backend",
                return_value=backend,
            ):
                code, out, _err = self.run_both(
                    cmd_rag_rebuild, self.project
                )

        self.assertEqual(code, 0)
        self.assertIn("rag: ready", out)
        self.assertIn("added: 1", out)
        self.assertIn("embedded: 3", out)

    def test_a_failed_rebuild_exits_two(self) -> None:
        backend = FakeBackend(
            report=SyncReport(
                failures=(
                    SyncFailure("wiki:a", "a", "down"),
                )
            )
        )
        with rag_env(**BASE_ENV):
            with patch(
                "noteagent.rag.search.get_backend",
                return_value=backend,
            ):
                code, out, _err = self.run_both(
                    cmd_rag_rebuild, self.project
                )

        self.assertEqual(code, 2)
        self.assertIn("failed: wiki:a: down", out)


if __name__ == "__main__":
    unittest.main()
