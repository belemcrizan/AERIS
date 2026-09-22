"""SQLite append-only flight recorder. Inserts only; never UPDATE/DELETE events."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from aeris.core.enums import EventType
from aeris.recorder.base import RecordedEvent
from aeris.recorder.integrity import GENESIS_HASH, canonical_event, chain_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    mission_id TEXT,
    route_id TEXT,
    waypoint_id TEXT,
    previous_hash TEXT NOT NULL DEFAULT '',
    event_hash TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_flight ON events(flight_id, seq);
"""


class SqliteFlightRecorder:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            if "previous_hash" not in columns:
                conn.execute(
                    "ALTER TABLE events ADD COLUMN previous_hash TEXT NOT NULL DEFAULT ''"
                )
            if "event_hash" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN event_hash TEXT NOT NULL DEFAULT ''")
            conn.commit()

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
        kind = EventType(event_type)
        async with self._lock:
            return await asyncio.to_thread(
                self._insert,
                flight_id,
                kind,
                payload,
                timestamp,
                mission_id,
                route_id,
                waypoint_id,
            )

    def _insert(
        self,
        flight_id: str,
        event_type: EventType,
        payload: dict[str, Any],
        timestamp: datetime,
        mission_id: str | None,
        route_id: str | None,
        waypoint_id: str | None,
    ) -> RecordedEvent:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT event_hash FROM events
                WHERE flight_id = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (flight_id,),
            ).fetchone()
            previous = row["event_hash"] if row and row["event_hash"] else GENESIS_HASH
            canonical = canonical_event(
                flight_id=flight_id,
                event_type=event_type,
                timestamp=timestamp,
                payload=payload,
                mission_id=mission_id,
                route_id=route_id,
                waypoint_id=waypoint_id,
            )
            digest = chain_hash(previous, canonical)
            cursor = conn.execute(
                """
                INSERT INTO events (
                    flight_id, event_type, timestamp, payload, mission_id, route_id,
                    waypoint_id, previous_hash, event_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    flight_id,
                    event_type.value,
                    timestamp.isoformat(),
                    json.dumps(payload),
                    mission_id,
                    route_id,
                    waypoint_id,
                    previous,
                    digest,
                ),
            )
            conn.commit()
            return RecordedEvent(
                seq=int(cursor.lastrowid),
                flight_id=flight_id,
                event_type=event_type,
                timestamp=timestamp,
                payload=payload,
                mission_id=mission_id,
                route_id=route_id,
                waypoint_id=waypoint_id,
                previous_hash=previous,
                event_hash=digest,
            )

    async def timeline(self, flight_id: str) -> list[RecordedEvent]:
        async with self._lock:
            return await asyncio.to_thread(self._timeline_sync, flight_id)

    def _timeline_sync(self, flight_id: str) -> list[RecordedEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE flight_id = ? ORDER BY seq ASC",
                (flight_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    async def list_flight_ids(self) -> list[str]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT flight_id, MIN(seq) AS first_seq FROM events GROUP BY flight_id ORDER BY first_seq"
            ).fetchall()
        return [str(row["flight_id"]) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RecordedEvent:
        keys = row.keys()
        return RecordedEvent(
            seq=int(row["seq"]),
            flight_id=row["flight_id"],
            event_type=EventType(row["event_type"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            payload=json.loads(row["payload"]),
            mission_id=row["mission_id"],
            route_id=row["route_id"],
            waypoint_id=row["waypoint_id"],
            previous_hash=row["previous_hash"] if "previous_hash" in keys else "",
            event_hash=row["event_hash"] if "event_hash" in keys else "",
        )
