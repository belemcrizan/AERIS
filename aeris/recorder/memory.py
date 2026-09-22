"""In-memory append-only recorder for tests and experiments."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from aeris.core.enums import EventType
from aeris.recorder.base import RecordedEvent
from aeris.recorder.integrity import (
    GENESIS_HASH,
    Checkpoint,
    canonical_event,
    chain_hash,
    make_checkpoint,
)
from aeris.recorder.signing import NoOpSigner


class InMemoryRecorder:
    def __init__(self, signer=None) -> None:
        self._events: list[RecordedEvent] = []
        self._lock = asyncio.Lock()
        self._tails: dict[str, str] = {}
        self._checkpoints: dict[str, list[Checkpoint]] = {}
        self.signer = signer or NoOpSigner()

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
            kind = EventType(event_type)
            previous = self._tails.get(flight_id, GENESIS_HASH)
            canonical = canonical_event(
                flight_id=flight_id,
                event_type=kind,
                timestamp=timestamp,
                payload=payload,
                mission_id=mission_id,
                route_id=route_id,
                waypoint_id=waypoint_id,
            )
            event = RecordedEvent(
                seq=len(self._events) + 1,
                flight_id=flight_id,
                event_type=kind,
                timestamp=timestamp,
                payload=payload,
                mission_id=mission_id,
                route_id=route_id,
                waypoint_id=waypoint_id,
                previous_hash=previous,
                event_hash=chain_hash(previous, canonical),
            )
            self._events.append(event)
            self._tails[flight_id] = event.event_hash
            return event

    async def timeline(self, flight_id: str) -> list[RecordedEvent]:
        return [event for event in self._events if event.flight_id == flight_id]

    async def list_flight_ids(self) -> list[str]:
        seen: list[str] = []
        for event in self._events:
            if event.flight_id not in seen:
                seen.append(event.flight_id)
        return seen

    async def checkpoint(self, flight_id: str, *, timestamp: datetime) -> Checkpoint:
        events = await self.timeline(flight_id)
        async with self._lock:
            checkpoint = make_checkpoint(flight_id, events, timestamp, self.signer)
            self._checkpoints.setdefault(flight_id, []).append(checkpoint)
            return checkpoint

    async def checkpoints(self, flight_id: str) -> list[Checkpoint]:
        return list(self._checkpoints.get(flight_id, []))

    def _drop_tail_for_test(self, flight_id: str, count: int) -> None:
        """Simulate an attacker deleting the last ``count`` events of a flight."""

        indices = [index for index, event in enumerate(self._events) if event.flight_id == flight_id]
        for index in reversed(indices[-count:]):
            del self._events[index]
