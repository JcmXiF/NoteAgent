"""A local provider proxy that fails the first N requests, then forwards.

R6 needs a provider failure that is real to the model client but deterministic
for the eval. The proxy sits on loopback, answers the first *fail_first*
requests with a 500 before any SSE delta, and streams every later request
through to the configured provider unchanged.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx

__all__ = ["FailureCounter", "FailingProxy"]


@dataclass(slots=True)
class FailureCounter:
    """How many requests still fail before the proxy starts forwarding."""

    remaining: int
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self) -> bool:
        with self._lock:
            if self.remaining <= 0:
                return False
            self.remaining -= 1
            return True


class FailingProxy:
    """A loopback reverse proxy in front of the configured provider."""

    def __init__(
        self,
        upstream_url: str,
        *,
        fail_first: int = 2,
        timeout_s: float = 180.0,
    ) -> None:
        self.upstream_url = upstream_url
        self.failures = FailureCounter(fail_first)
        self.timeout_s = timeout_s
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._forwarded = 0
        self._forward_lock = threading.Lock()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("the proxy is not running")
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """The OpenAI-compatible base URL a client should point at."""
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def requests(self) -> int:
        with self._forward_lock:
            return self.failures.remaining + self._forwarded

    def note_forwarded(self) -> None:
        with self._forward_lock:
            self._forwarded += 1

    def start(self) -> "FailingProxy":
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        setattr(server, "proxy", self)
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FailingProxy":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        proxy: FailingProxy = getattr(self.server, "proxy")
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        if proxy.failures.take():
            payload = b'{"error":{"message":"injected eval failure"}}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() in {"authorization", "content-type", "accept"}
        }
        with httpx.Client(timeout=httpx.Timeout(proxy.timeout_s)) as client:
            with client.stream(
                "POST",
                proxy.upstream_url,
                headers=headers,
                content=body,
            ) as response:
                proxy.note_forwarded()
                self.send_response(response.status_code)
                content_type = response.headers.get("content-type")
                if content_type:
                    self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for chunk in response.iter_raw():
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

    def log_message(self, format: str, *args: Any) -> None:
        return
