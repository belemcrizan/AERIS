"""Injectable clocks so tests and experiments stay deterministic."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def advance(self, milliseconds: float) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Wall clock plus a virtual offset.

    ``advance`` moves recorded time without sleeping. Holds still use
    ``sleep``, which does wait. Experiments should inject a FakeClock.
    """

    def __init__(self) -> None:
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self._offset

    def advance(self, milliseconds: float) -> datetime:
        self._offset += timedelta(milliseconds=milliseconds)
        return self.now()

    async def sleep(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds)


class FakeClock:
    """Monotonic clock that never waits on the wall clock."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, milliseconds: float) -> datetime:
        self._now += timedelta(milliseconds=milliseconds)
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds * 1000)
