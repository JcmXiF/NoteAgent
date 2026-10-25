"""Where the local server is, and how it is started and stopped.

There is no service manager here: the server is a process somebody started, and
this module is how the other processes find it again. Two facts are written down
about a running server — the process id and the port it actually listens on —
because those two answer every question the command line and the desktop shell
ask: "is it up", "stop it", "which address do I load".

The port is decided by the process that binds it, not by whoever started it: only
the binder can tell whether the port was free at the moment it took it. A server
that was asked for one port and had to take another says so in its record, and
callers read the record instead of guessing a list of candidates.

Records live under one directory in the temp dir, one file per process, named
after the process id. Naming by pid rather than by port is what makes two servers
safe to run at once: the record of the server that had to fall back cannot be
mistaken for the one on the preferred port.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from noteagent.api.guard import LOOPBACK_HOSTS

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

RECORDS_DIR_NAME = "noteagent-serve"


class PortBusy(RuntimeError):
    """The requested port belongs to something else."""

    def __init__(self, port: int, pids: list[int]) -> None:
        self.port = port
        self.pids = list(pids)
        if self.pids:
            owners = ", ".join(str(pid) for pid in self.pids)
            detail = f"port {port} is already in use (pid {owners})"
        else:
            detail = f"port {port} is already in use"
        super().__init__(detail)


class AlreadyServing(RuntimeError):
    """The requested port belongs to another noteagent server."""

    def __init__(self, port: int, pids: list[int]) -> None:
        self.port = port
        self.pids = list(pids)
        owners = ", ".join(str(pid) for pid in self.pids)
        super().__init__(f"already serving on port {port} (pid {owners})")


@dataclass(frozen=True)
class ServerRecord:
    """One running server, as written down by itself."""

    pid: int
    host: str
    port: int
    #: The port the caller asked for; differs from *port* after a fallback.
    requested_port: int
    started: float = 0.0

    @property
    def url(self) -> str:
        name = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{name}:{self.port}"


def records_dir() -> Path:
    return Path(tempfile.gettempdir()) / RECORDS_DIR_NAME


def record_path(pid: int) -> Path:
    return records_dir() / f"{pid}.json"


def write_record(record: ServerRecord) -> None:
    path = record_path(record.pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": record.pid,
        "host": record.host,
        "port": record.port,
        "requested_port": record.requested_port,
        "started": record.started,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_record(pid: int) -> ServerRecord | None:
    """The record for *pid*, or ``None`` if it is absent or unreadable."""
    path = record_path(pid)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        port = int(payload["port"])
        host = str(payload["host"])
        requested_port = int(payload.get("requested_port", port))
        started = float(payload.get("started") or 0.0)
        recorded_pid = int(payload.get("pid", pid))
    except (KeyError, TypeError, ValueError):
        return None
    if recorded_pid != pid or not host or not 0 < port < 65536:
        return None
    return ServerRecord(
        pid=recorded_pid,
        host=host,
        port=port,
        requested_port=requested_port,
        started=started,
    )


def read_records() -> list[ServerRecord]:
    """Every readable record, newest first."""
    try:
        names = [entry.name for entry in records_dir().iterdir() if entry.suffix == ".json"]
    except OSError:
        return []
    found: list[ServerRecord] = []
    for name in names:
        try:
            pid = int(Path(name).stem)
        except ValueError:
            continue
        record = read_record(pid)
        if record is not None:
            found.append(record)
    found.sort(key=lambda record: record.started, reverse=True)
    return found


def clear_record(pid: int) -> None:
    try:
        record_path(pid).unlink()
    except OSError:
        pass


def prune_records() -> None:
    """Drop records left behind by processes that are gone.

    A record is removed on the way out, but a process that was killed does not get
    that chance, and Windows has no signal handler to catch it. A dead record is
    only noise — a new server writing a reused pid overwrites the name — but the
    temp dir should not collect them forever.
    """
    for record in read_records():
        if not process_alive(record.pid):
            clear_record(record.pid)


def _listen(host: str, port: int) -> socket.socket:
    """Bind and listen, or raise ``OSError``. The caller owns the socket."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    family, socktype, proto, _canonical, address = infos[0]
    sock = socket.socket(family, socktype, proto)
    # On Windows SO_REUSEADDR lets two sockets take the same port silently, which
    # would turn "the port is taken" into "two servers, one unreachable".
    if sys.platform != "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(address)
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def bind_listener(host: str, port: int, *, fallback: bool = False) -> socket.socket:
    """Take *port* for a server, or a free one when *fallback* is allowed.

    The socket is bound here, before the server starts, so the port this returns
    is the port the server will serve on — no window in which somebody else can
    take it in between.
    """
    try:
        return _listen(host, port)
    except OSError as err:
        if not fallback:
            holders = pids_listening(port)
            ours = [pid for pid in holders if looks_like_noteagent(pid)]
            if ours:
                raise AlreadyServing(port, ours) from err
            raise PortBusy(port, holders) from err
    return _listen(host, 0)


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = (result.stdout or "") + (result.stderr or "")
        # When the pid is gone, tasklist prints "No tasks are running ..." — in
        # the language Windows is set to, so both wordings are matched.
        return str(pid) in out and "No tasks" not in out and "没有运行" not in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
    deadline = time.time() + 3
    while time.time() < deadline and process_alive(pid):
        time.sleep(0.1)
    if process_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def parse_listening_pids(netstat_text: str, port: int) -> list[int]:
    """Parse ``netstat -ano`` / ``ss`` lines for LISTEN on *port*."""
    found: list[int] = []
    for raw in netstat_text.splitlines():
        line = raw.strip()
        upper = line.upper()
        if "LISTENING" not in upper and not re.search(r"\bLISTEN\b", upper):
            continue
        if not re.search(rf":{port}(?:\s|$|\])", line):
            continue
        pid_token = line.split()[-1]
        pid_token = pid_token.split("/")[0]
        try:
            pid = int(pid_token)
        except ValueError:
            continue
        if pid > 0 and pid not in found:
            found.append(pid)
    return found


def pids_listening(port: int) -> list[int]:
    if sys.platform == "win32":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        return parse_listening_pids(result.stdout or "", port)
    result = subprocess.run(
        ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and (result.stdout or "").strip():
        pids: list[int] = []
        for token in result.stdout.split():
            try:
                pid = int(token)
            except ValueError:
                continue
            if pid > 0 and pid not in pids:
                pids.append(pid)
        return pids
    result = subprocess.run(
        ["ss", "-ltnp", f"sport = :{port}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_listening_pids(result.stdout or "", port)


def process_command_line(pid: int) -> str:
    if pid <= 0:
        return ""
    if sys.platform == "win32":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or "").strip()
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return ""


def looks_like_noteagent(pid: int) -> bool:
    """Whether *pid* is this server, judged by what it was asked to run.

    Not by the word appearing in its path: the development environment itself is
    called "noteagent", so every process started from it would match — including
    the test run that is asking this question.
    """
    cmd = process_command_line(pid).lower().replace("\\", "/")
    if not cmd:
        return False
    markers = ("-m noteagent", "noteagent.api", "noteagent.exe", "/noteagent serve")
    return any(marker in cmd for marker in markers)


def wait_port_free(port: int, *, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pids_listening(port):
            return True
        time.sleep(0.15)
    return not pids_listening(port)


def _servers_for(host: str, port: int) -> list[ServerRecord]:
    """Live records that answer for the server asked about at *host*:*port*.

    A server that fell back to another port still answers here, because what the
    caller names is the port it asked for. The two loopback names are the same
    machine, so ``--host localhost`` finds a server started with ``127.0.0.1``.
    """
    return [
        record
        for record in read_records()
        if record.requested_port == port
        and _same_machine(record.host, host)
        and process_alive(record.pid)
    ]


def _same_machine(first: str, second: str) -> bool:
    if first == second:
        return True
    return first.lower() in LOOPBACK_HOSTS and second.lower() in LOOPBACK_HOSTS


def found_servers(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[ServerRecord]:
    """The live servers that answer at *host*:*port*, whether they got that port."""
    prune_records()
    return _servers_for(host, port)


def stop_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> tuple[int, str]:
    """Stop the server that answers at *host*:*port*, returning ``(exit code, message)``."""
    killed: list[int] = []
    prune_records()
    for record in _servers_for(host, port):
        if not looks_like_noteagent(record.pid):
            continue
        terminate_pid(record.pid)
        clear_record(record.pid)
        killed.append(record.pid)
    for pid in pids_listening(port):
        if pid in killed or pid == os.getpid():
            continue
        if looks_like_noteagent(pid):
            terminate_pid(pid)
            clear_record(pid)
            killed.append(pid)
    wait_port_free(port)
    leftover = [pid for pid in pids_listening(port) if pid != os.getpid()]
    stopped = ", ".join(str(pid) for pid in killed)
    if leftover:
        pids = ", ".join(str(pid) for pid in leftover)
        if killed:
            return 1, (
                f"stopped pid {stopped} ({host}:{port}); "
                f"port {port} is still in use by pid {pids} (not this server)"
            )
        return 1, f"port {port} is in use by pid {pids} (not a noteagent server?)"
    if not killed:
        return 0, f"no noteagent serve on {host}:{port}"
    return 0, f"stopped pid {stopped} ({host}:{port})"


def status_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> tuple[int, str]:
    prune_records()
    listeners = pids_listening(port)
    servers = _servers_for(host, port)
    lines = [f"host={host} port={port}", f"records={records_dir()}"]
    if servers:
        for record in servers:
            lines.append(
                f"running pid={record.pid} url={record.url}"
                + (
                    ""
                    if record.port == port
                    else f" (port {port} was taken, served {record.port})"
                )
            )
    else:
        lines.append("running=(none)")
    lines.append(f"listening={listeners or '(none)'}")
    running = bool(servers) or any(process_alive(pid) for pid in listeners)
    return (0 if running else 1), "\n".join(lines)


def record_server(*, host: str, port: int, requested: int) -> ServerRecord:
    """Write down this process as the server, and remove the record on exit."""
    record = ServerRecord(
        pid=os.getpid(),
        host=host,
        port=port,
        requested_port=requested,
        started=time.time(),
    )
    write_record(record)
    atexit.register(clear_record, record.pid)
    return record


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "AlreadyServing",
    "PortBusy",
    "ServerRecord",
    "bind_listener",
    "clear_record",
    "found_servers",
    "looks_like_noteagent",
    "parse_listening_pids",
    "pids_listening",
    "process_alive",
    "prune_records",
    "read_record",
    "read_records",
    "record_path",
    "record_server",
    "records_dir",
    "status_server",
    "stop_server",
    "terminate_pid",
    "wait_port_free",
    "write_record",
]
