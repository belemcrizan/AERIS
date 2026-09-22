"""Human intervention requests. The operator sees evidence, not a bare enum."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aeris.core.enums import ControlAction, ExecutionState, OperatorRole
from aeris.core.models import Flight
from aeris.human.authorization import allowed_actions


class HumanActionRequest(BaseModel):
    operator: str = "human"
    role: OperatorRole
    reason: str = "human controller action"
    route_id: str | None = None
    action: ControlAction | None = None
    expected_version: int | None = None


class HumanInterventionRequest(BaseModel):
    request_id: str
    flight_id: str
    mission_id: str
    current_route_id: str | None
    waypoint_id: str | None
    state: ExecutionState
    hazards: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    recent_telemetry: list[dict[str, Any]] = Field(default_factory=list)
    side_effect_state: dict[str, Any] = Field(default_factory=dict)
    recommended_action: ControlAction
    allowed_actions: list[ControlAction] = Field(default_factory=list)
    timestamp: datetime
    reason: str
    control_version: int = 0


def side_effect_state(flight: Flight) -> dict[str, Any]:
    return {
        "open": [effect.model_dump(mode="json") for effect in flight.open_effects()],
        "incidents": flight.side_effect_incidents,
        "compensations": flight.compensation_count,
        "compensation_failures": flight.compensation_failures,
    }


def request_allowed_actions(flight: Flight, role: OperatorRole = OperatorRole.CONTROLLER) -> list[ControlAction]:
    """Role permissions, further reduced when a committed effect makes an action unsafe."""

    from aeris.control.safety import assess_action

    permitted: list[ControlAction] = []
    waypoint = None
    if flight.plan is not None and flight.current_route_id is not None:
        try:
            waypoint = flight.current_waypoint()
        except Exception:
            waypoint = None
    for action in allowed_actions(role):
        assessment = assess_action(flight, action, waypoint)
        if assessment.allowed or assessment.compensation_required:
            permitted.append(action)
    return permitted
