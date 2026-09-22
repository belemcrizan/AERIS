"""Recorder interface. SQLite is the V0 implementation; Kafka can wait."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from aeris.core.enums import EventType


class RecordedEvent(BaseModel):
    seq: int
    flight_id: str
    event_type: EventType
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    mission_id: str | None = None
    route_id: str | None = None
    waypoint_id: str | None = None
    previous_hash: str = ""
    event_hash: str = ""


@runtime_checkable
class FlightRecorder(Protocol):
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
    ) -> RecordedEvent: ...

    async def timeline(self, flight_id: str) -> list[RecordedEvent]: ...

    async def list_flight_ids(self) -> list[str]: ...
