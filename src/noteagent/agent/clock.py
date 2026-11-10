"""Time and identity, so a run can be replayed by giving it fixed ones."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from uuid import uuid4


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        ...


@runtime_checkable
class Ids(Protocol):
    def new_id(self, prefix: str = "") -> str:
        ...


class SystemClock:
    """The wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def iso_timestamp(clock: Clock) -> str:
    """The clock's answer as the log writes times: UTC, seconds, ``Z``."""
    moment = clock.now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class UuidIds:
    """Ids no other session can collide with."""

    def new_id(self, prefix: str = "") -> str:
        raw = uuid4().hex
        return f"{prefix}-{raw}" if prefix else raw
