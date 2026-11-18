"""Child processes for the eval: a real server, a real kill, and its log.

The eval's whole point is process death, so the server runs in its own child,
the port is chosen before it starts, and a kill is a real kill. Everything here
is stdlib: the server is the only thing that knows noteagent.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

__all__ = [
    "Child",
    "REPO_ROOT",
    "ServerHandle",
    "free_port",
    "server_env",
    "spawn",
    "start_server",
    "wait_ready",
]

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class Child:
    """One spawned process and the log it writes to."""

    process: subprocess.Popen[bytes]
    log: IO[bytes]

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        return self.process.pid

    def kill(self) -> None:
        """SIGKILL on POSIX, TerminateProcess on Windows: no cleanup path."""
        if self.alive:
            self.process.kill()
        self._finish()

    def stop(self) -> None:
        """A graceful stop, for a restart that is not the crash under test."""
        if self.alive:
            self.process.terminate()
        self._finish()

    def _finish(self) -> None:
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=15)
        if not self.log.closed:
            self.log.close()


@dataclass(frozen=True, slots=True)
class ServerHandle:
    """A running eval server: the child, its port and its base URL."""

    child: Child
    port: int

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def kill(self) -> None:
        self.child.kill()

    def stop(self) -> None:
        self.child.stop()


def free_port() -> int:
    """A loopback port the OS says is free right now.

    The eval server is an ephemeral child, not the core process: the parent
    needs the port before the child starts, so it accepts the small bind race
    the core's own bind-the-socket approach avoids.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def wait_ready(port: int, *, timeout_s: float = 30.0) -> bool:
    """True once the child's socket accepts a connection."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(
                ("127.0.0.1", port), timeout=0.25
            ):
                return True
        except OSError:
            time.sleep(0.02)
    return False


def server_env(
    project: Path,
    extra: dict[str, str] | None = None,
    *,
    home: Path | None = None,
) -> dict[str, str]:
    """The environment a server child needs: one project, this source tree.

    ``NOTEAGENT_HOME`` is the directory whose ``projects/`` the named project
    routes resolve under; the active project is pinned separately so both
    views agree.
    """
    env = dict(os.environ)
    env["NOTEAGENT_PROJECT"] = str(project)
    env["NOTEAGENT_HOME"] = str(home) if home is not None else ""
    parts = [str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = os.pathsep.join(part for part in parts if part)
    if extra:
        env.update(extra)
    return env


def spawn(
    args: list[str],
    *,
    log_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Child:
    """Start *args* with its output going to *log_path*."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    try:
        process = subprocess.Popen(
            args,
            cwd=str(cwd or REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    except BaseException:
        log.close()
        raise
    return Child(process=process, log=log)


def start_server(
    project: Path,
    *,
    artifacts: Path,
    home: Path | None = None,
    delay_ms: float = 300.0,
    extra_env: dict[str, str] | None = None,
) -> ServerHandle:
    """Start the eval server on a free port and wait for it to listen."""
    port = free_port()
    child = spawn(
        [
            sys.executable,
            "-m",
            "evals.recovery.server",
            "--project",
            str(project),
            "--port",
            str(port),
            "--tool-delay-ms",
            str(delay_ms),
        ],
        log_path=Path(artifacts) / "server.log",
        env=server_env(project, extra_env, home=home),
    )
    if not wait_ready(port):
        child.kill()
        raise RuntimeError(f"the eval server did not listen on port {port}")
    return ServerHandle(child=child, port=port)
