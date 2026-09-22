from aeris.core.enums import EventType, ExecutionState, InterventionMode
from tests.conftest import run_scenario


async def test_normal_successful_flight():
    director, flight = await run_scenario("happy_path")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.result is not None
    assert flight.result.success is True
    assert flight.route_changes == 0


async def test_high_latency_detection_and_rerouting():
    director, flight = await run_scenario("latency_reroute")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.route_changes == 1
    assert flight.current_route().name == "bravo"
    timeline = await director.recorder.timeline(flight.flight_id)
    types = [event.event_type for event in timeline]
    assert EventType.HAZARD in types
    assert EventType.DECISION in types
    hazards = [event.payload["type"] for event in timeline if event.event_type is EventType.HAZARD]
    assert "HIGH_LATENCY" in hazards
    states = [
        event.payload["to_state"]
        for event in timeline
        if event.event_type is EventType.STATE_TRANSITION
    ]
    assert "REROUTING" in states
    assert "DEGRADED" in states


async def test_tool_failure_retries_then_succeeds():
    director, flight = await run_scenario("tool_failure_retry")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.retry_count >= 1
    timeline = await director.recorder.timeline(flight.flight_id)
    hazards = [event.payload["type"] for event in timeline if event.event_type is EventType.HAZARD]
    assert "TOOL_FAILURE" in hazards


async def test_persistent_tool_failure_reroutes():
    _, flight = await run_scenario("persistent_tool_failure")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.route_changes >= 1
    assert flight.current_route().name == "bravo"


async def test_control_arm_fails_when_tool_fails():
    _, flight = await run_scenario("persistent_tool_failure", mode=InterventionMode.CONTROL)
    assert flight.state is ExecutionState.FAILED
    assert flight.route_changes == 0


async def test_abort_via_unrecoverable_budget_when_human_disabled():
    _, flight = await run_scenario("budget_risk", human_on_critical=False)
    assert flight.state in {ExecutionState.ABORTED, ExecutionState.WAITING_HUMAN, ExecutionState.COMPLETED}


async def test_critical_timeout_escalates_when_policy_requires_human():
    _, flight = await run_scenario("timeout_critical", human_on_critical=True)
    assert flight.state is ExecutionState.WAITING_HUMAN


async def test_all_routes_fail_does_not_claim_success():
    _, flight = await run_scenario("all_routes_fail", human_on_critical=False)
    assert flight.state in {ExecutionState.FAILED, ExecutionState.ABORTED, ExecutionState.WAITING_HUMAN}
    assert flight.state is not ExecutionState.COMPLETED
