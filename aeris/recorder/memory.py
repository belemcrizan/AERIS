"""In-memory append-only recorder for tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from aeris.core.enums import EventType
from aeris.recorder.base import RecordedEvent


class InMemoryRecorder:
    def __init__(self) -> None:
        self._events: list[RecordedEvent] = []
        self._lock = asyncio.Lock()

    async def append(
        self,
        flight_id: str,
        event_type: EventType | str,
        payload: dict[str, Any],
        *,
        timestamp: datetime,
        mission_id: str | None = None,
        route_id: str | None = None,
        waypoint_id: str | None = None,
    ) -> RecordedEvent:
        async with self._lock:
            event = RecordedEvent(
                seq=len(self._events) + 1,
                flight_id=flight_id,
                event_type=EventType(event_type),
                timestamp=timestamp,
                payload=payload,
                mission_id=mission_id,
                route_id=route_id,
                waypoint_id=waypoint_id,
            )
            self._events.append(event)
            return event

    async def timeline(self, flight_id: str) -> list[RecordedEvent]:
        return [event for event in self._events if event.flight_id == flight_id]

    async def list_flight_ids(self) -> list[str]:
        seen: list[str] = []
        for event in self._events:
            if event.flight_id not in seen:
                seen.append(event.flight_id)
        return seen
