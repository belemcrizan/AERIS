from aeris.core.enums import EventType, ExecutionState
from tests.conftest import run_scenario


async def test_timeline_reconstructs_latency_diversion_history():
    director, flight = await run_scenario("latency_reroute")
    timeline = await director.recorder.timeline(flight.flight_id)
    kinds = [event.event_type for event in timeline]
    assert kinds[0] in {EventType.FLIGHT_CREATED, EventType.MISSION_CREATED}
    assert EventType.PLAN_CREATED in kinds
    assert EventType.TELEMETRY in kinds
    assert EventType.HAZARD in kinds
    assert EventType.DECISION in kinds
    assert EventType.STATE_TRANSITION in kinds
    assert EventType.FLIGHT_COMPLETED in kinds
    assert flight.state is ExecutionState.COMPLETED
    seqs = [event.seq for event in timeline]
    assert seqs == sorted(seqs)
