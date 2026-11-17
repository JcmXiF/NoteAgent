"""Retry a model call before its first content delta, and only then.

A provider that failed before it wrote anything can be asked again; a stream
that already wrote text cannot, because retrying would repeat it. The wrapper
keeps the budget, the backoff and the visible attempt, and passes every other
error through unchanged. Tool calls are not replayed by this layer.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from noteagent.agent.model import (
    ModelClient,
    ModelErrorEvent,
    ModelEvent,
    ModelRequest,
    ModelRetryEvent,
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCallDelta,
    ModelToolCallEnd,
)

__all__ = ["RetryingModel"]

#: Errors that must not be retried even when the adapter marks them retryable.
_NO_RETRY_CODES = frozenset(
    {
        "authentication_failed",
        "cancelled",
        "context_overflow",
        "insufficient_quota",
        "invalid_request",
        "permission_denied",
        "tool_call_leak",
        "usage_limit",
    }
)

_CONTENT_EVENTS = (
    ModelTextDelta,
    ModelThinkingDelta,
    ModelToolCallDelta,
    ModelToolCallEnd,
)


class RetryingModel:
    """A ``ModelClient`` that retries a failed call before its first delta."""

    def __init__(
        self,
        model: ModelClient,
        *,
        max_retries: int = 5,
        max_retries_cap: int = 10,
        base_delay: float = 0.5,
        max_delay: float = 32.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._max_retries = min(
            max(0, max_retries), max(0, max_retries_cap)
        )
        self._base_delay = max(0.0, base_delay)
        self._max_delay = max(0.0, max_delay)
        self._sleep = sleep

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]:
        for attempt in range(self._max_retries + 1):
            emitted = False
            error: ModelErrorEvent | None = None
            for event in self._model.stream(request):
                if isinstance(event, ModelErrorEvent):
                    error = event
                    break
                if isinstance(event, _CONTENT_EVENTS):
                    emitted = True
                yield event
            if error is None:
                return
            if not self._retryable(error, emitted) or attempt >= self._max_retries:
                yield error
                return
            delay = self._delay(error, attempt)
            yield ModelRetryEvent(
                attempt + 1,
                self._max_retries,
                delay,
                error.code,
            )
            if request.cancel is not None and request.cancel.is_set():
                yield ModelErrorEvent(
                    "cancelled",
                    "the run was cancelled",
                    retryable=True,
                )
                return
            if request.pause is not None and request.pause.is_set():
                return
            self._sleep(delay)
            if request.cancel is not None and request.cancel.is_set():
                yield ModelErrorEvent(
                    "cancelled",
                    "the run was cancelled",
                    retryable=True,
                )
                return
            if request.pause is not None and request.pause.is_set():
                return

    @staticmethod
    def _retryable(error: ModelErrorEvent, emitted: bool) -> bool:
        if emitted or not error.retryable:
            return False
        return error.code not in _NO_RETRY_CODES

    def _delay(self, error: ModelErrorEvent, attempt: int) -> float:
        if error.retry_after_s is not None and error.retry_after_s > 0:
            # The provider's own instruction wins over the local backoff cap.
            return error.retry_after_s
        return min(self._max_delay, self._base_delay * (2**attempt))
