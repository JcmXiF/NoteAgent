"""The writing task's change stream: every write publishes its revision.

The broker is the in-process fan-out behind ``GET /api/v1/task/events``. The one
write seam (``TaskWriter``) is where the event comes from, so these tests hold
the writer to publishing exactly what it saved: a created task, a content
commit's new revision, and a log commit's repeated revision.
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_fakes import FixedClock, SequentialIds
from noteagent.project.init import init_project
from noteagent.task.events import TaskEvents
from noteagent.task.model import SearchCard, Task
from noteagent.task.store import TaskStore
from noteagent.task.writer import TaskWriter
from noteagent.util.tempdir import remove_tree

try:
    from fastapi.testclient import TestClient

    import noteagent.api.projects as projects_mod
    from noteagent.api.app import create_app
    from noteagent.api.deps import AppDeps
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[misc, assignment]
    projects_mod = None  # type: ignore[misc, assignment]
    create_app = None  # type: ignore[misc, assignment]
    AppDeps = None  # type: ignore[misc, assignment]


def _frames(lines):
    """The (event, data) pairs in an SSE line stream, skipping comments."""
    kind = ""
    for line in lines:
        if line.startswith("event: "):
            kind = line[len("event: ") :]
        elif line.startswith("data: "):
            yield kind, json.loads(line[len("data: ") :])


def _search_cards() -> tuple[SearchCard, ...]:
    return (
        SearchCard(
            title="Entropy",
            url="https://example.com/entropy",
            snippet="熵衡量不确定性。",
            site="example.com",
        ),
    )


class FakeSearch:
    """A pure search node that answers with one card and never touches a network."""

    def __call__(self, query: str):
        return list(_search_cards())


class GatedSearch(FakeSearch):
    """A search node held open, so an op stays running for a cancel."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.entered = threading.Event()

    def __call__(self, query: str):
        self.entered.set()
        self.gate.wait(5)
        return super().__call__(query)


class _StreamReader:
    """One ``/api/v1/task/events`` response read on its own thread.

    Frames are queued as they arrive; comments (keepalives) are read and
    discarded, but they are what lets the thread notice ``close`` promptly.
    """

    def __init__(self, client, url: str = "/api/v1/task/events") -> None:
        self.client = client
        self.url = url
        self.frames: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.opened = threading.Event()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            with self.client.stream(
                "GET", self.url, timeout=10
            ) as response:
                if response.status_code != 200:
                    self.frames.put(
                        ("__status__", {"status": response.status_code})
                    )
                    return
                self.opened.set()
                kind = ""
                for line in response.iter_lines():
                    if self._stop.is_set():
                        return
                    if line.startswith("event: "):
                        kind = line[len("event: ") :]
                    elif line.startswith("data: "):
                        self.frames.put(
                            (kind, json.loads(line[len("data: ") :]))
                        )
        except Exception as err:  # noqa: BLE001 - the test reports what it saw
            self.frames.put(("__error__", {"detail": str(err)}))

    def next(self, timeout: float = 2.0) -> tuple[str, dict]:
        return self.frames.get(timeout=timeout)

    def close(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise AssertionError("the event stream did not close")


class _MemoryStore:
    """A task store that keeps the last saved task and never touches a file."""

    def __init__(self) -> None:
        self.saved: Task | None = None

    def save(self, task: Task) -> None:
        self.saved = task


def _task() -> Task:
    return Task(
        id="task-1",
        created_at="2026-11-10T09:00:00Z",
        updated_at="2026-11-10T09:00:00Z",
    )


class TaskWriterPublishTests(unittest.TestCase):
    def test_create_and_both_commits_publish_the_revision_they_saved(self) -> None:
        events = TaskEvents()
        subscription = events.subscribe("project")
        self.addCleanup(subscription.close)
        store = _MemoryStore()
        writer = TaskWriter(
            store, FixedClock(), events=events, project_key="project"
        )

        task = _task()
        writer.create(task)
        content = writer.commit_content(task, {})
        writer.commit_log(content, {})

        # Create has no revision to move, a content commit moves it once, and a
        # log commit keeps it: every saved shape is announced, even when the
        # revision repeats, because the panel must see op state changes too.
        self.assertEqual(
            [subscription.get(0.2) for _ in range(3)], [0, 1, 1]
        )

    def test_projects_are_isolated_and_a_closed_listener_is_dropped(self) -> None:
        events = TaskEvents()
        one = events.subscribe("one")
        two = events.subscribe("two")
        self.addCleanup(one.close)
        self.addCleanup(two.close)

        events.publish("one", 5)

        self.assertEqual(one.get(0.2), 5)
        with self.assertRaises(queue.Empty):
            two.get(0.05)
        self.assertEqual(events.subscriber_count("one"), 1)
        self.assertEqual(events.subscriber_count("two"), 1)

        one.close()
        self.assertEqual(events.subscriber_count("one"), 0)
        self.assertEqual(events.subscriber_count("two"), 1)


@unittest.skipIf(TestClient is None, "fastapi not installed")
class TaskStreamApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(remove_tree, self.repo)
        self.layout = init_project(self.repo / "projects" / "demo")
        patcher = patch.object(projects_mod, "REPO_ROOT", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        projects_mod.use_project("demo")
        self.key = str(self.layout.root.resolve())
        # A test must not wait out the production keepalive to close a stream;
        # the stream still sends one, just often enough to observe.
        keepalive = patch(
            "noteagent.api.task_events.KEEPALIVE_SECONDS", 0.05
        )
        keepalive.start()
        self.addCleanup(keepalive.stop)

    def client(
        self,
        *,
        search=None,
        wait_s: float = 15.0,
        spawn=None,
    ) -> "TestClient":
        self.deps = AppDeps(
            clock=FixedClock(),
            ids=SequentialIds(),
            task_search=search if search is not None else FakeSearch(),
            # The op runs before the route answers, so a content write's
            # revision is known without a poll or a race. A test that needs to
            # cancel a running op passes a real thread instead.
            task_spawn=spawn if spawn is not None else (lambda work: work()),
            task_wait_s=wait_s,
            # A human's search has no session, so nothing can ask for a resume;
            # leaving the callback out keeps a test from starting one anyway.
            task_resume=None,
        )
        client = TestClient(create_app(self.deps))
        self.addCleanup(client.close)
        return client

    def write_a_card(self, client, expected_rev: int = 0) -> int:
        response = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": expected_rev},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["rev"]

    def drain(self, subscription) -> list[int]:
        revs: list[int] = []
        while True:
            try:
                revs.append(subscription.get(0.05))
            except queue.Empty:
                return revs

    def wait_for(self, predicate, *, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = predicate()
            if found:
                return found
            threading.Event().wait(0.01)
        self.fail("the condition was not reached")

    def read_until(self, reader: _StreamReader, rev: int) -> list[dict]:
        """Every frame up to and including the one that names *rev*."""
        seen: list[dict] = []
        while True:
            kind, data = reader.next()
            self.assertEqual(kind, "task", data)
            seen.append(data)
            if data.get("rev") == rev:
                return seen

    def test_a_content_write_publishes_the_revision_it_saved(self) -> None:
        client = self.client()
        subscription = self.deps.task_events.subscribe(self.key)
        self.addCleanup(subscription.close)

        rev = self.write_a_card(client)

        stored = TaskStore(self.layout).load()
        self.assertIsNotNone(stored)
        self.assertEqual(rev, stored.rev)
        published = self.drain(subscription)
        self.assertIn(stored.rev, published)
        self.assertEqual(published[-1], stored.rev)

    def test_the_stream_starts_with_the_current_task_revision(self) -> None:
        client = self.client()
        rev = self.write_a_card(client)

        with client.stream(
            "GET", "/api/v1/task/events", timeout=10
        ) as response:
            self.assertEqual(response.status_code, 200)
            first = next(_frames(response.iter_lines()))

        self.assertEqual(first, ("task", {"rev": rev}))

    def test_a_log_write_publishes_the_same_revision(self) -> None:
        search = GatedSearch()
        client = self.client(
            search=search,
            wait_s=0.01,
            spawn=lambda work: threading.Thread(
                target=work, daemon=True
            ).start(),
        )
        self.addCleanup(search.gate.set)
        self.assertEqual(
            client.get("/api/v1/task?view=summary").json()["rev"], 0
        )
        subscription = self.deps.task_events.subscribe(self.key)
        self.addCleanup(subscription.close)

        started = client.post(
            "/api/v1/task/search",
            json={"query": "entropy", "expectedRev": 0},
        )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertIn(started.json()["status"], ("requested", "running"))
        before = TaskStore(self.layout).load()
        self.assertIsNotNone(before)
        self.drain(subscription)

        cancelled = client.post(
            f"/api/v1/task/ops/{started.json()['opId']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertTrue(cancelled.json()["cancelled"])

        after = TaskStore(self.layout).load()
        self.assertIsNotNone(after)
        self.assertEqual(after.rev, before.rev)
        # The op state changed but the task content did not: the event repeats
        # the revision so the panel still learns that the op is now cancelled.
        published = self.drain(subscription)
        self.assertIn(after.rev, published)
        self.assertEqual({item for item in published}, {after.rev})

    def test_every_subscriber_receives_the_published_revision(self) -> None:
        client = self.client()
        first_rev = self.write_a_card(client)
        first = _StreamReader(client)
        second = _StreamReader(client)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        self.assertEqual(first.next()[1], {"rev": first_rev})
        self.assertEqual(second.next()[1], {"rev": first_rev})

        next_rev = self.write_a_card(client, expected_rev=first_rev)

        self.assertIn({"rev": next_rev}, self.read_until(first, next_rev))
        self.assertIn({"rev": next_rev}, self.read_until(second, next_rev))

    def test_a_disconnected_stream_is_unsubscribed(self) -> None:
        client = self.client()
        rev = self.write_a_card(client)
        reader = _StreamReader(client)
        try:
            self.assertEqual(reader.next()[1], {"rev": rev})
            self.wait_for(
                lambda: self.deps.task_events.subscriber_count(self.key) == 1
            )
            reader.close()
            self.wait_for(
                lambda: self.deps.task_events.subscriber_count(self.key) == 0
            )
        finally:
            reader.close()

    def test_the_stream_does_not_replay_an_earlier_revision(self) -> None:
        client = self.client()
        rev = self.write_a_card(client)
        # A revision announced before anyone listened is not kept: connecting
        # starts from the task as it stands, not from an event history.
        self.deps.task_events.publish(self.key, 99)
        reader = _StreamReader(client)
        self.addCleanup(reader.close)

        self.assertEqual(reader.next()[1], {"rev": rev})
        try:
            replayed = reader.frames.get(timeout=0.2)
        except queue.Empty:
            replayed = None
        self.assertIsNone(replayed, f"the stream replayed {replayed}")

        next_rev = self.write_a_card(client, expected_rev=rev)
        seen = self.read_until(reader, next_rev)
        self.assertNotIn({"rev": 99}, seen)

    def test_the_stream_keeps_the_connection_alive_when_nothing_changes(
        self,
    ) -> None:
        client = self.client()
        self.write_a_card(client)

        saw_task = False
        saw_keepalive = False
        with client.stream(
            "GET", "/api/v1/task/events", timeout=10
        ) as response:
            for line in response.iter_lines():
                if line.startswith("event: task"):
                    saw_task = True
                elif line.startswith(": keepalive"):
                    saw_keepalive = True
                    break

        self.assertTrue(saw_task)
        self.assertTrue(saw_keepalive)


if __name__ == "__main__":
    unittest.main()
