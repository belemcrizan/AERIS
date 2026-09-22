from aeris.core.enums import ControlAction, EventType, ExecutionState
from tests.conftest import run_scenario


async def test_human_can_reroute_a_critical_flight():
    director, flight = await run_scenario("timeout_critical", human_on_critical=True)
    assert flight.state is ExecutionState.WAITING_HUMAN
    bravo = next(route for route in flight.plan.routes if route.name == "bravo")
    updated = await director.apply_human_action(
        flight.flight_id,
        ControlAction.REROUTE,
        reason="controller diverts to bravo",
        operator="atc-1",
        route_id=bravo.route_id,
    )
    assert updated.state is ExecutionState.COMPLETED
    assert updated.human_interventions == 1
    assert updated.current_route().name == "bravo"
    timeline = await director.recorder.timeline(flight.flight_id)
    assert any(event.event_type is EventType.HUMAN_INTERVENTION for event in timeline)


async def test_human_abort():
    director, flight = await run_scenario("timeout_critical", human_on_critical=True)
    updated = await director.apply_human_action(
        flight.flight_id,
        ControlAction.ABORT,
        reason="mission no longer valid",
    )
    assert updated.state is ExecutionState.ABORTED
    assert updated.result is not None
    assert updated.result.success is False
