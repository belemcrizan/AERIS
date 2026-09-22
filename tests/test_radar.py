from datetime import UTC, datetime

from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight, StepObservation
from aeris.radar.engine import RadarEngine


def test_radar_normalizes_latency_and_counters():
    radar = RadarEngine()
    now = datetime.now(UTC)
    flight = Flight(
        flight_id=new_id("flt"),
        mission_id=new_id("msn"),
        agent=Agent(agent_id="agt_1", name="sim"),
        retry_count=1,
        route_changes=2,
        created_at=now,
        updated_at=now,
    )
    observation = StepObservation(
        flight_id=flight.flight_id,
        mission_id=flight.mission_id,
        route_id="rte_a",
        waypoint_id="wp_1",
        waypoint_index=1,
        latency_ms=5120,
        tool_error=True,
        success=False,
        confidence=0.2,
        data_freshness_s=9000,
        progress=0.3,
        action="lookup",
        token_usage=40,
    )
    event = radar.observe(flight, observation)
    assert event.flight_id == flight.flight_id
    assert event.metrics.step_latency_ms == 5120
    assert event.metrics.tool_error_count == 1
    assert event.metrics.retry_count == 1
    assert event.metrics.route_changes == 2
    assert event.metrics.confidence == 0.2
    assert event.metrics.token_usage == 40
    assert event.metrics.step_success is False
