from pathlib import Path

from aeris.core.clock import FakeClock
from aeris.core.enums import EventType
from aeris.recorder.replay import reconstruct_from_events
from aeris.recorder.sqlite import SqliteFlightRecorder


async def test_sqlite_recorder_is_append_only_and_replayable(tmp_path: Path):
    clock = FakeClock()
    recorder = SqliteFlightRecorder(tmp_path / "aeris.db")
    await recorder.append(
        "flt_1",
        EventType.FLIGHT_CREATED,
        {"state": "CREATED", "flight_id": "flt_1"},
        timestamp=clock.now(),
        mission_id="msn_1",
    )
    clock.advance(10)
    await recorder.append(
        "flt_1",
        EventType.STATE_TRANSITION,
        {"from_state": "CREATED", "to_state": "PLANNED"},
        timestamp=clock.now(),
        mission_id="msn_1",
    )
    clock.advance(10)
    await recorder.append(
        "flt_1",
        EventType.HAZARD,
        {"type": "HIGH_LATENCY"},
        timestamp=clock.now(),
        mission_id="msn_1",
    )
    timeline = await recorder.timeline("flt_1")
    assert [event.event_type for event in timeline] == [
        EventType.FLIGHT_CREATED,
        EventType.STATE_TRANSITION,
        EventType.HAZARD,
    ]
    assert timeline[0].seq < timeline[1].seq < timeline[2].seq
    snapshot = reconstruct_from_events("flt_1", timeline)
    assert snapshot["state"] == "PLANNED"
    assert snapshot["hazard_count"] == 1
    assert snapshot["event_count"] == 3
