from aeris.core.enums import ControlAction, EventType, ExecutionState, FailureReason
from aeris.simulation.scenarios import get_scenario
from tests.conftest import run_scenario


async def test_healthy_flight_does_not_intervene():
    director, flight = await run_scenario("healthy_noisy_telemetry")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.route_changes == 0
    assert flight.retry_count == 0
    timeline = await director.recorder.timeline(flight.flight_id)
    actions = [
        event.payload["action"]
        for event in timeline
        if event.event_type is EventType.DECISION
    ]
    assert actions
    assert set(actions) == {ControlAction.CONTINUE.value}


async def test_idempotent_retry_does_not_duplicate_the_write():
    director, flight = await run_scenario("idempotent_retry")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.retry_count >= 1
    runtime = director.runtime
    assert sum(runtime.commits.values()) == 1
    timeline = await director.recorder.timeline(flight.flight_id)
    assert any(event.event_type is EventType.SIDE_EFFECT for event in timeline)


async def test_irreversible_write_blocks_automatic_retry():
    director, flight = await run_scenario("unsafe_irreversible_retry")
    assert flight.state is ExecutionState.WAITING_HUMAN
    assert sum(director.runtime.commits.values()) == 1
    timeline = await director.recorder.timeline(flight.flight_id)
    actions = [
        event.payload["action"]
        for event in timeline
        if event.event_type is EventType.DECISION
    ]
    assert ControlAction.RETRY.value not in actions
    assert ControlAction.ESCALATE_HUMAN.value in actions
    request = director.human_requests[flight.flight_id]
    assert request.flight_id == flight.flight_id
    assert request.side_effect_state["open"]
    assert ControlAction.RETRY not in request.allowed_actions


async def test_compensation_success_then_reroute():
    _, flight = await run_scenario("compensation_success")
    assert flight.state is ExecutionState.COMPLETED
    assert flight.compensation_count >= 1
    assert flight.current_route().name == "bravo"
    assert flight.result is not None
    assert flight.result.failure_reason is None


async def test_compensation_failure_is_explicit():
    _, flight = await run_scenario("compensation_failure")
    assert flight.state is ExecutionState.FAILED
    assert flight.route_changes == 0
    assert flight.result is not None
    assert flight.result.failure_reason is FailureReason.COMPENSATION_FAILURE
    assert flight.compensation_failures >= 1


def test_unprotected_repeat_commits_twice_in_the_simulator():
    scenario = get_scenario("unsafe_irreversible_retry")
    runtime = scenario.runtime()

    async def _twice():
        from aeris.adapters.runtime import ExecutionContext
        from aeris.control.director import FlightDirector
        from aeris.core.clock import FakeClock
        from aeris.policies.thresholds import ThresholdPolicy
        from aeris.recorder.memory import InMemoryRecorder

        clock = FakeClock()
        director = FlightDirector(
            recorder=InMemoryRecorder(),
            runtime=runtime,
            policy=ThresholdPolicy(human_on_critical=False, hold_ms=0),
            clock=clock,
        )
        mission = director.create_mission("x", "y")
        flight = director.create_flight(mission)
        flight.plan = None
        from aeris.routing.planner import RoutePlanner

        plan = RoutePlanner().plan(mission.mission_id, scenario.routes)
        flight.plan = plan
        flight.current_route_id = plan.selected_route_id
        waypoint = flight.current_waypoint()
        context = ExecutionContext(flight=flight, waypoint=waypoint, attempt=0)
        first = await runtime.execute_waypoint(context)
        second = await runtime.execute_waypoint(context)
        return first, second

    import asyncio

    first, second = asyncio.run(_twice())
    assert first.side_effect_committed is True
    assert second.side_effect_committed is True
    assert sum(runtime.commits.values()) == 2
