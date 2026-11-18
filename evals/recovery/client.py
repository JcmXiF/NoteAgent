"""A thin HTTP/SSE client for the eval: real requests, real disconnects.

The eval must be able to hang up mid-stream as a browser tab would, so the
query and resume calls are context managers over ``httpx``'s streaming
response. Everything else is a one-line wrapper over the routes the app
already exposes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

__all__ = [
    "Client",
    "Event",
    "RequestFailed",
    "SseStream",
    "iter_sse",
]


@dataclass(frozen=True, slots=True)
class Event:
    """One SSE block: its event name and decoded data."""

    kind: str
    data: dict[str, Any]


class RequestFailed(RuntimeError):
    """A non-200 response where the stream expected a 200."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def iter_sse(lines: Iterable[str]) -> Iterator[Event]:
    """Yield events as the lines of a live stream arrive."""
    kind = ""
    data: list[str] = []
    for line in lines:
        if not line:
            if kind:
                try:
                    payload = json.loads("\n".join(data))
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    yield Event(kind, payload)
            kind = ""
            data = []
            continue
        if line.startswith("event:"):
            kind = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].lstrip())


class SseStream:
    """The events of one open response, read until it ends."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def __iter__(self) -> Iterator[Event]:
        return iter_sse(self._response.iter_lines())


class Client:
    """The eval's view of one running server."""

    def __init__(self, base_url: str, *, timeout_s: float = 180.0) -> None:
        self._client = httpx.Client(
            base_url=base_url, timeout=httpx.Timeout(timeout_s)
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- plain routes -----------------------------------------------------

    def health(self) -> dict[str, Any]:
        response = self._client.get("/api/v1/health")
        response.raise_for_status()
        return response.json()

    def create_session(self, title: str = "eval") -> str:
        response = self._client.post(
            "/api/v1/sessions", json={"title": title}
        )
        response.raise_for_status()
        return str(response.json()["id"])

    def view(self, session_id: str) -> dict[str, Any]:
        response = self._client.get(f"/api/v1/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    def get(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> httpx.Response:
        return self._client.get(path, params=params)

    def post(
        self, path: str, body: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        return self._client.post(path, json=dict(body or {}))

    def abandon(self, session_id: str, run_id: str) -> dict[str, Any]:
        response = self._client.post(
            f"/api/v1/sessions/{session_id}/abandon",
            json={"runId": run_id},
        )
        response.raise_for_status()
        return response.json()

    def restore_preview(
        self, project: str, run_id: str
    ) -> dict[str, Any]:
        response = self._client.get(
            f"/api/projects/{project}/effects/restore/preview",
            params={"runId": run_id},
        )
        response.raise_for_status()
        return response.json()

    def restore(
        self,
        project: str,
        run_id: str,
        pages: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        response = self._client.post(
            f"/api/projects/{project}/effects/restore",
            json={"runId": run_id, "pages": list(pages)},
        )
        response.raise_for_status()
        return response.json()

    # -- streams ----------------------------------------------------------

    @contextmanager
    def stream_query(
        self, session_id: str, content: str
    ) -> Iterator[SseStream]:
        path = f"/api/v1/sessions/{session_id}/query"
        with self._client.stream(
            "POST", path, json={"content": content}
        ) as response:
            _expect_stream(response)
            yield SseStream(response)

    @contextmanager
    def stream_resume(
        self, session_id: str, run_id: str
    ) -> Iterator[SseStream]:
        path = f"/api/v1/sessions/{session_id}/resume"
        with self._client.stream(
            "POST", path, json={"runId": run_id}
        ) as response:
            _expect_stream(response)
            yield SseStream(response)

    def query(self, session_id: str, content: str) -> tuple[Event, ...]:
        with self.stream_query(session_id, content) as stream:
            return tuple(stream)

    def resume(self, session_id: str, run_id: str) -> tuple[Event, ...]:
        with self.stream_resume(session_id, run_id) as stream:
            return tuple(stream)


def _expect_stream(response: httpx.Response) -> None:
    if response.status_code == 200:
        return
    response.read()
    raise RequestFailed(response.status_code, response.text)
