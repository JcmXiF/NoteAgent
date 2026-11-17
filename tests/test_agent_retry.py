"""Retrying a model call before its first delta, and never after one."""

from __future__ import annotations

import threading
import unittest

from agent_fakes import FakeSessionStore, FixedClock, SequentialIds
from noteagent.agent.events import StatusEvent
from noteagent.agent.faux import FauxModelClient
from noteagent.agent.kernel import KernelRuntime
from noteagent.agent.model import (
    ModelErrorEvent,
    ModelRequest,
    ModelRetryEvent,
    ModelStartEvent,
    ModelTextDelta,
    ModelToolCallEnd,
    ToolCall,
)
from noteagent.agent.retry import RetryingModel
from noteagent.agent.runtime import UserInput
from noteagent.api.sessions import session_payload


def _request() -> ModelRequest:
    return ModelRequest(model="faux", messages=())


def _error(
    message: str = "boom",
    *,
    code: str = "model_error",
    retryable: bool = True,
    retry_after_s: float | None = None,
) -> list:
    return [
        ModelStartEvent("faux"),
        ModelErrorEvent(
            code,
            message,
            retryable=retryable,
            retry_after_s=retry_after_s,
        ),
    ]


def _text(text: str = "答案") -> list:
    return FauxModelClient.text(text).script


class RetryingModelTests(unittest.TestCase):
    def test_a_transient_error_is_retried_once(self) -> None:
        inner = FauxModelClient(_error(), _text("答案"))
        sleeps: list[float] = []
        model = RetryingModel(inner, max_retries=2, sleep=sleeps.append)

        events = list(model.stream(_request()))

        retries = [event for event in events if isinstance(event, ModelRetryEvent)]
        self.assertEqual(len(retries), 1)
        self.assertEqual(retries[0].attempt, 1)
        self.assertEqual(retries[0].max_retries, 2)
        self.assertEqual(retries[0].delay_s, 0.5)
        self.assertEqual(len(inner.requests), 2)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(
            "".join(
                event.text for event in events if isinstance(event, ModelTextDelta)
            ),
            "答案",
        )

    def test_the_budget_is_exhausted_and_the_last_error_passes_through(self) -> None:
        inner = FauxModelClient(_error(), _error(), _error())
        sleeps: list[float] = []
        model = RetryingModel(inner, max_retries=2, sleep=sleeps.append)

        events = list(model.stream(_request()))

        self.assertEqual(
            len([event for event in events if isinstance(event, ModelRetryEvent)]),
            2,
        )
        self.assertIsInstance(events[-1], ModelErrorEvent)
        self.assertEqual(len(inner.requests), 3)
        self.assertEqual(sleeps, [0.5, 1.0])

    def test_a_spend_limit_is_not_retried(self) -> None:
        inner = FauxModelClient(
            _error("quota exceeded", code="usage_limit"), _text()
        )
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 1)
        self.assertEqual(
            [event for event in events if isinstance(event, ModelRetryEvent)], []
        )
        self.assertIsInstance(events[-1], ModelErrorEvent)

    def test_a_429_rate_limit_is_retried(self) -> None:
        inner = FauxModelClient(_error("HTTP 429 rate limit"), _text())
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 2)
        self.assertEqual(
            len([event for event in events if isinstance(event, ModelRetryEvent)]),
            1,
        )

    def test_a_529_overload_is_retried(self) -> None:
        inner = FauxModelClient(_error("HTTP 529 overloaded"), _text())
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 2)
        self.assertEqual(
            len([event for event in events if isinstance(event, ModelRetryEvent)]),
            1,
        )

    def test_a_tool_call_delta_is_never_replayed(self) -> None:
        script = [
            ModelStartEvent("faux"),
            ModelToolCallEnd(ToolCall("c1", "wiki_read", "{}")),
            ModelErrorEvent("model_error", "connection cut", retryable=True),
        ]
        inner = FauxModelClient(script)
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 1)
        self.assertTrue(
            any(isinstance(event, ModelToolCallEnd) for event in events)
        )
        self.assertIsInstance(events[-1], ModelErrorEvent)

    def test_an_auth_error_is_not_retried(self) -> None:
        inner = FauxModelClient(
            _error("unauthorized", code="authentication_failed"), _text()
        )
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 1)
        self.assertIsInstance(events[-1], ModelErrorEvent)

    def test_a_mid_stream_error_is_not_retried(self) -> None:
        script = [
            ModelStartEvent("faux"),
            ModelTextDelta("部分"),
            ModelErrorEvent("model_error", "connection cut", retryable=True),
        ]
        inner = FauxModelClient(script)
        model = RetryingModel(inner, max_retries=2, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 1)
        self.assertEqual(
            [event.text for event in events if isinstance(event, ModelTextDelta)],
            ["部分"],
        )
        self.assertIsInstance(events[-1], ModelErrorEvent)

    def test_retry_after_overrides_the_backoff(self) -> None:
        inner = FauxModelClient(_error(retry_after_s=0.25), _text())
        sleeps: list[float] = []
        model = RetryingModel(inner, max_retries=1, sleep=sleeps.append)

        list(model.stream(_request()))

        self.assertEqual(sleeps, [0.25])

    def test_the_default_budget_is_five_retries(self) -> None:
        inner = FauxModelClient(*[_error() for _ in range(6)])
        model = RetryingModel(inner, sleep=lambda _s: None)

        events = list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 6)
        self.assertEqual(
            len([event for event in events if isinstance(event, ModelRetryEvent)]),
            5,
        )

    def test_the_budget_cap_is_ten_retries(self) -> None:
        inner = FauxModelClient(*[_error() for _ in range(11)])
        model = RetryingModel(inner, max_retries=99, sleep=lambda _s: None)

        list(model.stream(_request()))

        self.assertEqual(len(inner.requests), 11)

    def test_a_cancel_during_backoff_stops_the_retry(self) -> None:
        cancel = threading.Event()

        def sleep(_delay: float) -> None:
            cancel.set()

        inner = FauxModelClient(_error(), _text())
        request = ModelRequest(model="faux", messages=(), cancel=cancel)
        model = RetryingModel(inner, max_retries=2, sleep=sleep)

        events = list(model.stream(request))

        self.assertEqual(len(inner.requests), 1)
        self.assertIsInstance(events[-1], ModelErrorEvent)
        self.assertEqual(events[-1].code, "cancelled")


class RetryInTheLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeSessionStore()
        self.store.create("s1")

    def runtime(self, model) -> KernelRuntime:
        return KernelRuntime(
            model=model,
            sessions=self.store,
            clock=FixedClock(),
            ids=SequentialIds(),
            model_name="faux",
        )

    def test_the_retry_is_visible_and_the_answer_follows(self) -> None:
        inner = FauxModelClient(_error(), _text("答案"))
        model = RetryingModel(inner, max_retries=1, sleep=lambda _s: None)
        events = []

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "completed")
        self.assertTrue(
            any(
                isinstance(event, StatusEvent)
                and "retrying 1/1" in event.message
                and "in 0.5s" in event.message
                for event in events
            )
        )
        view = session_payload(self.store, "s1")
        self.assertEqual(view["messages"][-1]["content"], "答案")

    def test_a_mid_stream_failure_keeps_an_incomplete_partial(self) -> None:
        script = [
            ModelStartEvent("faux"),
            ModelTextDelta("部分"),
            ModelErrorEvent("model_error", "connection cut", retryable=True),
        ]
        inner = FauxModelClient(script)
        sleeps: list[float] = []
        model = RetryingModel(inner, max_retries=2, sleep=sleeps.append)
        events = []

        result = self.runtime(model).execute(
            UserInput("s1", "问题"), events.append
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(sleeps, [])
        messages = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "message"
        ]
        self.assertEqual(messages[-1]["content"], "部分")
        self.assertEqual(messages[-1]["stopReason"], "incomplete")
        markers = [
            entry
            for entry in self.store.load("s1").entries
            if entry["type"] == "run_marker"
        ]
        self.assertEqual(markers[-1]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
