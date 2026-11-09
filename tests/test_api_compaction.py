"""Compaction through the interface: manual, threshold, and the view after it."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_fakes import FixedClock, SequentialIds, seed_long_history
from noteagent.agent.compaction import CompactionLimits
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime, RunRegistry
from noteagent.project.layout import ProjectLayout
from noteagent.sessions.jsonl import JsonlSessionStore

try:
    from fastapi.testclient import TestClient

    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


LIMITS = CompactionLimits(
    context_tokens=100_000,
    reserve_tokens=0,
    keep_recent_tokens=20,
    max_summary_tokens=20,
    chars_per_token=4,
)


def _seed(store: JsonlSessionStore) -> None:
    seed_long_history(store)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class CompactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = JsonlSessionStore(
            ProjectLayout(Path(self._tmp.name)),
            clock=FixedClock(),
            ids=SequentialIds(),
        )
        self.store.create("s1")

    def app(self, model=None):
        self.model = model or FauxModelClient.text("summary of earlier turns")
        self.runs = RunRegistry()
        runtime = KernelRuntime(
            model=self.model,
            sessions=self.store,
            clock=FixedClock(),
            ids=SequentialIds(),
            registry=self.runs,
            compaction=LIMITS,
        )
        return TestClient(
            create_app(
                AppDeps(
                    sessions=self.store,
                    runtime=runtime,
                    model=self.model,
                    runs=self.runs,
                    clock=FixedClock(),
                    ids=SequentialIds(),
                )
            )
        )

    def test_a_manual_compaction_writes_the_entry_and_the_view_reads_it(
        self,
    ) -> None:
        _seed(self.store)
        client = self.app()

        response = client.post("/api/v1/sessions/s1/compact", json={})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["compacted"])
        self.assertEqual(
            body["derived"]["summary"], "summary of earlier turns"
        )
        compaction = next(
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "compaction"
        )
        self.assertEqual(compaction["summary"], "summary of earlier turns")
        self.assertEqual(len(self.model.requests), 1)

    def test_a_short_session_has_nothing_to_compact(self) -> None:
        self.store.append(
            "s1", {"type": "message", "role": "user", "content": "only"}
        )
        client = self.app()

        response = client.post("/api/v1/sessions/s1/compact", json={})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["compacted"])
        self.assertEqual(self.model.requests, [])

    def test_a_running_session_is_not_compacted(self) -> None:
        _seed(self.store)
        client = self.app()
        self.runs.claim("s1")
        try:
            response = client.post("/api/v1/sessions/s1/compact", json={})
        finally:
            self.runs.release("s1")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "busy")
        self.assertFalse(
            any(
                entry["type"] == "compaction"
                for entry in self.store.load("s1").entries
            )
        )
