import sqlite3
from pathlib import Path

from aeris.core.clock import FakeClock
from aeris.core.enums import EventType
from aeris.recorder.integrity import verify_events
from aeris.recorder.memory import InMemoryRecorder
from aeris.recorder.sqlite import SqliteFlightRecorder


async def test_memory_chain_verifies_and_detects_payload_tampering():
    clock = FakeClock()
    recorder = InMemoryRecorder()
    await recorder.append("flt", EventType.FLIGHT_CREATED, {"state": "CREATED"}, timestamp=clock.now())
    clock.advance(5)
    await recorder.append("flt", EventType.HAZARD, {"type": "TOOL_FAILURE"}, timestamp=clock.now())
    timeline = await recorder.timeline("flt")
    assert verify_events(timeline).ok is True
    timeline[1].payload["type"] = "TAMPERED"
    report = verify_events(timeline)
    assert report.ok is False
    assert report.broken_seq == timeline[1].seq


async def test_sqlite_detects_a_direct_history_edit(tmp_path: Path):
    clock = FakeClock()
    path = tmp_path / "aeris.db"
    recorder = SqliteFlightRecorder(path)
    await recorder.append(
        "flt",
        EventType.FLIGHT_CREATED,
        {"state": "CREATED"},
        timestamp=clock.now(),
        mission_id="msn",
    )
    clock.advance(5)
    await recorder.append(
        "flt",
        EventType.DECISION,
        {"action": "CONTINUE"},
        timestamp=clock.now(),
        mission_id="msn",
    )
    timeline = await recorder.timeline("flt")
    assert verify_events(timeline).ok is True
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE events SET payload = ? WHERE event_type = ?",
            ('{"action": "ABORT"}', "DECISION"),
        )
        conn.commit()
    tampered = await recorder.timeline("flt")
    report = verify_events(tampered)
    assert report.ok is False
    assert "event_hash" in report.reason


async def test_deleting_a_middle_event_breaks_the_chain():
    clock = FakeClock()
    recorder = InMemoryRecorder()
    for kind in (EventType.FLIGHT_CREATED, EventType.PLAN_CREATED, EventType.HAZARD):
        await recorder.append("flt", kind, {"kind": kind.value}, timestamp=clock.now())
        clock.advance(1)
    timeline = await recorder.timeline("flt")
    shortened = [timeline[0], timeline[2]]
    assert verify_events(shortened).ok is False
