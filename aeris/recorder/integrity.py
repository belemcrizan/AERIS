"""Tamper-evident hash chain for the flight recorder.

Guarantees:
- Given an unmodified timeline, verification succeeds.
- Changing a stored payload, timestamp, or previous_hash without
  recomputing every later hash makes verification fail.
- Deleting or reordering an event in the middle breaks the chain.

Non-guarantees:
- This is not a digital signature. There is no secret key.
- An attacker who can rewrite the chain from the altered event to the
  tail can produce a consistent history.
- Deleting the tail (the last events) is not detected, because the
  remaining prefix still hashes. V0 does not store an external anchor.
- This is not distributed consensus and not a blockchain.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from aeris.core.enums import EventType

GENESIS_HASH = "0" * 64


def canonical_event(
    *,
    flight_id: str,
    event_type: EventType | str,
    timestamp: datetime,
    payload: dict[str, Any],
    mission_id: str | None,
    route_id: str | None,
    waypoint_id: str | None,
) -> str:
    body = {
        "event_type": EventType(event_type).value,
        "flight_id": flight_id,
        "mission_id": mission_id,
        "payload": payload,
        "route_id": route_id,
        "timestamp": timestamp.isoformat(),
        "waypoint_id": waypoint_id,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def chain_hash(previous_hash: str, canonical: str) -> str:
    material = f"{canonical}{previous_hash}".encode()
    return hashlib.sha256(material).hexdigest()


class IntegrityReport(BaseModel):
    ok: bool
    checked: int
    broken_seq: int | None = None
    reason: str | None = None


def verify_events(events: list[Any]) -> IntegrityReport:
    """Verify a per-flight timeline. ``events`` must already be ordered."""

    previous = GENESIS_HASH
    for event in events:
        if event.previous_hash != previous:
            return IntegrityReport(
                ok=False,
                checked=event.seq,
                broken_seq=event.seq,
                reason="previous_hash does not match the prior event",
            )
        canonical = canonical_event(
            flight_id=event.flight_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            payload=event.payload,
            mission_id=event.mission_id,
            route_id=event.route_id,
            waypoint_id=event.waypoint_id,
        )
        expected = chain_hash(previous, canonical)
        if event.event_hash != expected:
            return IntegrityReport(
                ok=False,
                checked=event.seq,
                broken_seq=event.seq,
                reason="event_hash does not match canonical payload",
            )
        previous = event.event_hash
    return IntegrityReport(ok=True, checked=len(events))
