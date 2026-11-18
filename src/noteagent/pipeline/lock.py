"""One project, one writer, across processes.

The core is normally the only process pointing at a project, but a development
server and a packaged one can be, and a second writer would rewrite the same
state from underneath the first. The lock is a file beside that state: whoever
creates it owns the project, and the recorded process id says whether the owner
is still there. A heartbeat is kept for a holder that records no id.

Liveness is read from the process, not from the heartbeat alone: a stage can run
for minutes (a model loop, a fetch), and a live owner must not be treated as
gone just because it has not written recently.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from noteagent.agent.clock import Clock, iso_timestamp
from noteagent.io.atomic import write_text_atomic

#: How long a holder without a usable process id may go without a heartbeat.
DEFAULT_STALE_AFTER_S = 120.0


class ProjectBusy(RuntimeError):
    """Another process holds this project's lock."""

    def __init__(self, holder: Mapping[str, object] | None) -> None:
        self.holder = dict(holder or {})
        owner = str(self.holder.get("owner") or "another process")
        super().__init__(f"this project is already being written by {owner}")


class ProjectLock:
    """The lock file one project's writers take turns holding."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Clock,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
        alive: Callable[[int], bool] | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._stale_after_s = stale_after_s
        self._alive = alive or process_alive

    def read(self) -> dict[str, object] | None:
        """Who holds it, or ``None`` when nobody does."""
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return dict(raw) if isinstance(raw, Mapping) else None

    def is_stale(self, holder: Mapping[str, object] | None) -> bool:
        """Whether the holder is gone, not merely quiet."""
        if holder is None:
            return True
        pid = holder.get("pid")
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            return not self._alive(pid)
        beat = _moment(str(holder.get("heartbeatAt") or ""))
        if beat is None:
            return True
        age = (self._now() - beat).total_seconds()
        return age > self._stale_after_s

    def acquire(self, owner: str) -> None:
        """Take the project for *owner*, or raise :class:`ProjectBusy`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(self._holder(owner), ensure_ascii=False) + "\n"
        for _ in range(3):
            if self._claim(body):
                return
            holder = self.read()
            if holder is None:
                # A name that exists but cannot be read is not a holder this
                # process can judge: somebody may still be publishing it, or it
                # is not ours to take. Refusing is the safe answer.
                raise ProjectBusy(None)
            if not self.is_stale(holder):
                raise ProjectBusy(holder)
            # A gone owner's file is not a lock. Removing it and trying again is
            # how the next process takes over.
            try:
                self._path.unlink()
            except OSError:
                pass
        raise ProjectBusy(self.read())

    def _claim(self, body: str) -> bool:
        """Publish the lock with its content already written, or say it is taken.

        A hard link makes the name appear with the whole file behind it, so a
        second process never sees the empty window between create and write.
        """
        temporary = self._path.parent / f".{self._path.name}.{token_hex(6)}.tmp"
        try:
            with open(temporary, "x", encoding="utf-8") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self._path)
            except FileExistsError:
                return False
            except OSError:
                # A filesystem without hard links: create the name exclusively
                # and accept the short window while the content is written.
                return self._claim_exclusive(body)
            return True
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def _claim_exclusive(self, body: str) -> bool:
        try:
            handle = os.open(
                self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
        except FileExistsError:
            return False
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def heartbeat(self, owner: str) -> bool:
        """Refresh the heartbeat, if *owner* still holds the lock."""
        holder = self.read()
        if holder is None or str(holder.get("owner") or "") != owner:
            return False
        body = dict(holder)
        body["heartbeatAt"] = iso_timestamp(self._clock)
        write_text_atomic(self._path, json.dumps(body, ensure_ascii=False) + "\n")
        return True

    def release(self, owner: str) -> bool:
        """Let go, if *owner* still holds the lock."""
        holder = self.read()
        if holder is None or str(holder.get("owner") or "") != owner:
            return False
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _holder(self, owner: str) -> dict[str, object]:
        now = iso_timestamp(self._clock)
        return {
            "owner": owner,
            "pid": os.getpid(),
            "acquiredAt": now,
            "heartbeatAt": now,
        }

    def _now(self) -> datetime:
        moment = self._clock.now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)


def process_alive(pid: int) -> bool:
    """Whether *pid* names a live process.

    Signal 0 is not a harmless probe on Windows: ``os.kill`` there terminates the
    process. The Windows branch asks the process table instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, which is still a process.
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A process we may not query still exists; anything else is gone.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _moment(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "ProjectBusy",
    "ProjectLock",
    "process_alive",
]
