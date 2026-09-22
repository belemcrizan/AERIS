from datetime import UTC, datetime

from aeris.control.controller import ATCController
from aeris.core.enums import (
    ControlAction,
    ExecutionState,
    HazardSeverity,
    HazardType,
    InterventionMode,
)
from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight, FlightPlan, Hazard, Route, Waypoint
from aeris.policies.thresholds import ThresholdPolicy


def _flight(routes: list[Route], selected: str) -> Flight:
    now = datetime.now(UTC)
    return Flight(
        flight_id=new_id("flt"),
        mission_id=new_id("msn"),
        agent=Agent(agent_id="agt", name="sim"),
        state=ExecutionState.DEGRADED,
        plan=FlightPlan(
            plan_id="plan_1",
            mission_id="msn",
            routes=routes,
            selected_route_id=selected,
            rationale=["test"],
        ),
        current_route_id=selected,
        created_at=now,
        updated_at=now,
    )


def _routes() -> list[Route]:
    wp = [Waypoint(waypoint_id="wp", name="step", index=0)]
    return [
        Route(route_id="alpha", name="alpha", description="a", waypoints=wp, estimated_reliability=0.9),
        Route(route_id="bravo", name="bravo", description="b", waypoints=wp, estimated_reliability=0.8),
    ]


def test_control_arm_never_intervenes():
    controller = ATCController(mode=InterventionMode.CONTROL)
    flight = _flight(_routes(), "alpha")
    hazards = [
        Hazard(
            hazard_id="haz_1",
            flight_id=flight.flight_id,
            type=HazardType.TOOL_FAILURE,
            severity=HazardSeverity.WARNING,
            timestamp=datetime.now(UTC),
            recommended_action=ControlAction.RETRY,
        )
    ]
    decision = controller.decide(flight, hazards)
    assert decision.action is ControlAction.CONTINUE
    assert "CONTROL" in decision.reason


def test_warning_latency_reroutes_with_reason():
    controller = ATCController(policy=ThresholdPolicy(human_on_critical=False))
    flight = _flight(_routes(), "alpha")
    hazards = [
        Hazard(
            hazard_id="haz_1",
            flight_id=flight.flight_id,
            type=HazardType.HIGH_LATENCY,
            severity=HazardSeverity.WARNING,
            timestamp=datetime.now(UTC),
            recommended_action=ControlAction.REROUTE,
        )
    ]
    decision = controller.decide(flight, hazards)
    assert decision.action is ControlAction.REROUTE
    assert decision.new_route == "bravo"
    assert decision.previous_route == "alpha"
    assert decision.reason
    assert "selected_route" in decision.evidence
