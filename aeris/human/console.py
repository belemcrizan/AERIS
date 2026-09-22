"""Read model for the human ATC console.

The console page renders this view. It is not the source of truth: the
director re-checks role, state, version, and side-effect policy on every
action. The view only explains in advance what the domain will refuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from aeris.control.safety import assess_action
from aeris.core.enums import ControlAction, ExecutionState, OperatorRole
from aeris.core.models import Flight

if TYPE_CHECKING:
    from aeris.control.director import FlightDirector

CONSOLE_ACTIONS = (
    ControlAction.CONTINUE,
    ControlAction.HOLD,
    ControlAction.RETRY,
    ControlAction.REROUTE,
    ControlAction.ABORT,
)

_BLOCK_MESSAGES = {
    "irreversible write already committed": "an irreversible write has already committed.",
    "irreversible side effect is committed on this route": (
        "an irreversible write on this route has already committed."
    ),
    "idempotent write has no idempotency key": "the committed write has no idempotency key.",
    "reversible write cannot be compensated": "the committed write cannot be compensated.",
    "runtime cannot retry": "the runtime does not support retry.",
}


class ActionAvailability(BaseModel):
    action: ControlAction
    enabled: bool
    required_role: OperatorRole
    reason: str = ""


class ConsoleView(BaseModel):
    mission: dict[str, Any] = Field(default_factory=dict)
    flight_id: str
    state: ExecutionState
    control_version: int
    current_route: dict[str, Any] | None = None
    current_waypoint: dict[str, Any] | None = None
    hazards: list[dict[str, Any]] = Field(default_factory=list)
    signal_provenance: dict[str, str] = Field(default_factory=dict)
    recent_telemetry: list[dict[str, Any]] = Field(default_factory=list)
    side_effects: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    recommended_action: str | None = None
    recommended_reason: str | None = None
    actions: list[ActionAvailability] = Field(default_factory=list)
    cancel_status: str | None = None
    cancel_outcome: str | None = None


def action_availability(flight: Flight, *, has_alternative: bool) -> list[ActionAvailability]:
    result: list[ActionAvailability] = []
    terminal = flight.state in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.ABORTED}
    try:
        waypoint = flight.current_waypoint()
    except Exception:
        waypoint = None
    for action in CONSOLE_ACTIONS:
        role = OperatorRole.ADMIN if action is ControlAction.ABORT else OperatorRole.CONTROLLER
        label = action.value.capitalize()
        if terminal:
            result.append(ActionAvailability(
                action=action, enabled=False, required_role=role,
                reason=f"{label} unavailable: flight is {flight.state.value}.",
            ))
            continue
        if flight.cancel_requested:
            result.append(ActionAvailability(
                action=action, enabled=False, required_role=role,
                reason=f"{label} unavailable: a cancel is pending.",
            ))
            continue
        if action is ControlAction.ABORT:
            if flight.state == ExecutionState.WAITING_HUMAN:
                reason = "Abort ends the flight now."
            elif flight.runtime_can_cancel:
                reason = "Abort is sent as a cancel; the runtime can stop at a checkpoint."
            else:
                reason = (
                    "Abort is sent as a cancel; the runtime cannot interrupt the current "
                    "waypoint, so the flight stops at the next boundary."
                )
            result.append(ActionAvailability(action=action, enabled=True, required_role=role, reason=reason))
            continue
        if flight.state != ExecutionState.WAITING_HUMAN:
            result.append(ActionAvailability(
                action=action, enabled=False, required_role=role,
                reason=f"{label} unavailable: ATC is flying ({flight.state.value}); human control requires WAITING_HUMAN.",
            ))
            continue
        if action is ControlAction.REROUTE and not has_alternative:
            result.append(ActionAvailability(
                action=action, enabled=False, required_role=role,
                reason="Reroute unavailable: no alternative route remains.",
            ))
            continue
        assessment = assess_action(flight, action, waypoint)
        if assessment.allowed:
            result.append(ActionAvailability(action=action, enabled=True, required_role=role))
            continue
        if assessment.compensation_required:
            reason = f"{label} unavailable: the committed write must be compensated first (approve compensation)."
        else:
            detail = _BLOCK_MESSAGES.get(assessment.blocked_reason or "", assessment.detail)
            reason = f"{label} unavailable: {detail}"
        result.append(ActionAvailability(action=action, enabled=False, required_role=role, reason=reason))
    return result


def console_view(director: FlightDirector, flight: Flight) -> ConsoleView:
    mission = director.missions.get(flight.mission_id)
    route = None
    waypoint = None
    try:
        current = flight.current_route()
        route = {
            "route_id": current.route_id,
            "name": current.name,
            "dependencies": current.dependencies.model_dump() if current.dependencies else None,
        }
        wp = flight.current_waypoint()
        if wp is not None:
            waypoint = {
                "waypoint_id": wp.waypoint_id,
                "name": wp.name,
                "tool": wp.tool,
                "side_effect": wp.side_effect.value,
            }
    except Exception:
        pass
    hazards = director.recent_hazards.get(flight.flight_id, [])
    alternatives = [
        {
            "route_id": scored.route.route_id,
            "name": scored.route.name,
            "score": round(scored.score, 3),
            "diversity": scored.diversity,
            "shared_failed_dependencies": scored.shared_failed_dependencies,
            "terms": scored.terms,
        }
        for scored in director.planner.score_alternatives(flight, [])
        if scored.route.route_id not in set(flight.failed_route_ids)
    ]
    telemetry = director._recent_telemetry.get(flight.flight_id, [])
    provenance = telemetry[-1].get("provenance", {}) if telemetry else {}
    decision = director.last_decisions.get(flight.flight_id)
    return ConsoleView(
        mission=mission.model_dump(mode="json") if mission else {},
        flight_id=flight.flight_id,
        state=flight.state,
        control_version=flight.control_version,
        current_route=route,
        current_waypoint=waypoint,
        hazards=hazards,
        signal_provenance={key: str(value) for key, value in provenance.items()},
        recent_telemetry=telemetry,
        side_effects=[effect.model_dump(mode="json") for effect in flight.committed_effects],
        alternatives=alternatives,
        recommended_action=decision.action.value if decision else None,
        recommended_reason=decision.reason if decision else None,
        actions=action_availability(flight, has_alternative=bool(alternatives)),
        cancel_status=flight.cancel_status.value if flight.cancel_status else None,
        cancel_outcome=flight.cancel_outcome.value if flight.cancel_outcome else None,
    )
