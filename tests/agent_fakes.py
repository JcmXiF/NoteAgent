"""Deterministic stand-ins for the kernel seams.

Not named ``test_*``, so discovery treats it as a helper rather than a suite.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone

from noteagent.agent.events import DoneEvent, ObservationEvent, StatusEvent, TokenEvent
from noteagent.agent.jobs import JobResult
from noteagent.agent.model import (
    AssistantMessage,
    Message,
    ModelDoneEvent,
    ModelRequest,
    ModelStartEvent,
    ModelTextDelta,
    TextBlock,
    collect,
)
from noteagent.agent.runtime import RunResult
from noteagent.agent.store import SessionSnapshot, SessionSummary
from noteagent.agent.tools import ToolContext
from noteagent.agent.wait import WaitState
from noteagent.agent.wait_store import wait_status_for


def old_chat_payload(
    conversation_id: str = "c-1",
    *,
    title: str = "Entropy",
    with_cards: bool = True,
) -> dict[str, object]:
    """A conversation as the old ``chats/*.json`` files stored it."""
    cards = (
        [
            {
                "tool": "wiki_read",
                "title": "Entropy",
                "slug": "concepts/entropy",
            }
        ]
        if with_cards
        else []
    )
    assistant: dict[str, object] = {
        "id": "m2",
        "role": "assistant",
        "content": "熵衡量不确定性。",
        "createdAt": "2026-11-09T10:00:02Z",
    }
    if cards:
        assistant["cards"] = cards
    return {
        "version": 1,
        "id": conversation_id,
        "title": title,
        "updatedAt": "2026-11-09T10:00:02Z",
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "content": "什么是熵？",
                "createdAt": "2026-11-09T10:00:01Z",
            },
            assistant,
        ],
    }


def seed_long_history(store, session_id: str = "s1", pairs: int = 4) -> None:
    """A session long enough that compaction has something to cut."""
    store.append(
        session_id,
        {
            "type": "system_section",
            "sections": {"identity": "You are NoteAgent."},
        },
    )
    for index in range(pairs):
        store.append(
            session_id,
            {
                "type": "message",
                "role": "user",
                "content": f"old question {index} " + "x" * 300,
            },
        )
        store.append(
            session_id,
            {
                "type": "message",
                "role": "assistant",
                "content": f"old answer {index} " + "y" * 300,
            },
        )


class FixedClock:
    """A clock that only moves when a test says so."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now


class SequentialIds:
    """Ids a test can name in advance, counted per prefix."""

    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def new_id(self, prefix: str = "") -> str:
        number = self._next.get(prefix, 0) + 1
        self._next[prefix] = number
        return f"{prefix}-{number}" if prefix else str(number)


class GatedModel:
    """Streams one piece, holds the stream open, then finishes when let go.

    A test uses it to stop a run mid-stream: the first piece proves the run is
    underway, and ``release`` lets the stream end after a cancel was asked for.
    """

    def __init__(self, first: str, rest: str = "") -> None:
        self.first = first
        self.rest = rest
        self.release = threading.Event()
        self.requests: list[ModelRequest] = []

    def stream(self, request):
        self.requests.append(request)
        yield ModelStartEvent("faux")
        yield ModelTextDelta(self.first)
        self.release.wait(5)
        if self.rest:
            yield ModelTextDelta(self.rest)
        yield ModelDoneEvent(
            AssistantMessage(
                blocks=(TextBlock(self.first + self.rest),), stop_reason="stop"
            )
        )


class FakeModelClient:
    """A model that answers with one fixed message and records its requests."""

    def __init__(self, text: str = "answer") -> None:
        self.text = text
        self.requests = []

    def stream(self, request):
        self.requests.append(request)
        yield ModelDoneEvent(AssistantMessage(blocks=(TextBlock(self.text),)))


class FakeRuntime:
    """A run that exercises the model and tools it was given, then ends.

    It is deliberately smaller than the real loop: enough to prove that the
    parts a test injected are reachable through the runtime seam.
    """

    def __init__(self, model=None, tools=None) -> None:
        self.model = model
        self.tools = tools
        self.cancelled: list[str] = []
        self.running: set[str] = set()

    def execute(self, request, sink):
        sink(StatusEvent("thinking"))
        if self.model is not None:
            message = collect(
                self.model.stream(
                    ModelRequest(
                        model="fake",
                        messages=(Message("user", getattr(request, "content", "")),),
                    )
                )
            )
            sink(TokenEvent(message.content))
        if self.tools is not None:
            tool = self.tools.require("wiki_read")
            result = tool.handler(
                {"slug": "concepts/entropy"},
                ToolContext(request.session_id),
            )
            sink(ObservationEvent(result.meta))
        sink(DoneEvent({"id": request.session_id}))
        return RunResult(request.session_id, "run-1", "completed")

    def cancel(self, session_id: str) -> None:
        self.cancelled.append(session_id)

    def wait_state(self, session_id: str) -> WaitState | None:
        return None

    def is_busy(self, session_id: str) -> bool:
        return session_id in self.running

    def compact(self, session_id: str, instructions: str = "") -> bool:
        return False


class FakeSessionStore:
    """Sessions kept in a dictionary, for tests that do not care about bytes."""

    def __init__(self) -> None:
        self.entries: dict[str, list[dict[str, object]]] = {}
        self.titles: dict[str, str] = {}

    def create(self, session_id: str, title: str = "") -> SessionSnapshot:
        self.entries.setdefault(session_id, [])
        self.titles[session_id] = title
        return self.load(session_id)

    def load(self, session_id: str) -> SessionSnapshot:
        if session_id not in self.entries:
            raise FileNotFoundError(session_id)
        return SessionSnapshot(
            session_id,
            self.titles.get(session_id, ""),
            tuple(self.entries[session_id]),
        )

    def append(self, session_id: str, entry: dict[str, object]) -> None:
        self.entries.setdefault(session_id, []).append(dict(entry))

    def list(self) -> tuple[SessionSummary, ...]:
        return tuple(
            SessionSummary(
                session_id,
                self.titles.get(session_id, ""),
                "",
                len(entries),
            )
            for session_id, entries in self.entries.items()
        )

    def rename(self, session_id: str, title: str) -> None:
        self.titles[session_id] = title

    def delete(self, session_id: str) -> None:
        self.entries.pop(session_id, None)
        self.titles.pop(session_id, None)


class FakeWaitStore:
    """At most one wait per session, held in memory."""

    def __init__(self) -> None:
        self.waits: dict[str, WaitState] = {}

    def current(self, session_id: str) -> WaitState | None:
        return self.waits.get(session_id)

    def propose(self, session_id: str, wait: WaitState) -> None:
        self.waits[session_id] = wait

    def enter(self, session_id: str, wait_id: str, job_id: str) -> None:
        wait = self.waits[session_id]
        self.waits[session_id] = replace(
            wait, status="entered", job_id=job_id
        )

    def finish(self, session_id: str, wait_id: str, result: JobResult) -> None:
        wait = self.waits[session_id]
        self.waits[session_id] = replace(
            wait,
            status=wait_status_for(result),
            result_summary=result.summary,
            artifacts=result.artifacts,
            error=result.error,
        )

    def cancel(self, session_id: str, wait_id: str, reason: str = "") -> None:
        wait = self.waits[session_id]
        self.waits[session_id] = replace(wait, status="cancelled")


class FakeJobClient:
    """Jobs are whatever a test put in the dictionary."""

    def __init__(self) -> None:
        self.results: dict[str, JobResult] = {}
        self.cancelled: list[str] = []

    def get(self, job_id: str) -> JobResult | None:
        return self.results.get(job_id)

    def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)
