"""Strongly typed AERIS domain models.

These types are the contract between packages. Implementations may change;
this vocabulary should not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from aeris.core.enums import (
    ControlAction,
    ExecutionState,
    FailureReason,
    HazardScope,
    HazardSeverity,
    HazardType,
    OperatorRole,
    SideEffectClass,
    SignalProvenance,
)
from aeris.core.errors import NoRouteAvailable
from aeris.core.ids import new_id


class Agent(BaseModel):
    agent_id: str
    name: str
    runtime_kind: str = "simulator"


class Mission(BaseModel):
    mission_id: str
    objective: str
    success_criteria: str
    created_at: datetime


class Waypoint(BaseModel):
    waypoint_id: str
    name: str
    index: int
    description: str = ""
    side_effect: SideEffectClass = SideEffectClass.READ_ONLY
    idempotency_key: str | None = None
    supports_idempotency: bool = False
    compensation_name: str | None = None


class Route(BaseModel):
    route_id: str
    name: str
    description: str
    waypoints: list[Waypoint]
    estimated_reliability: float = 0.8
    estimated_latency_ms: float = 1000.0
    estimated_cost: float = 1.0
    side_effect_risk: float = 0.0
    is_human: bool = False


class FlightPlan(BaseModel):
    plan_id: str
    mission_id: str
    routes: list[Route]
    selected_route_id: str
    rationale: list[str] = Field(default_factory=list)

    def route_by_id(self, route_id: str) -> Route:
        for route in self.routes:
            if route.route_id == route_id:
                return route
        raise KeyError(route_id)

    def current_route(self) -> Route:
        return self.route_by_id(self.selected_route_id)


class TelemetryMetrics(BaseModel):
    latency_ms: float = 0.0
    step_latency_ms: float = 0.0
    retry_count: int = 0
    tool_error_count: int = 0
    route_changes: int = 0
    token_usage: int | None = None
    confidence: float | None = None
    data_freshness_s: float | None = None
    execution_time_ms: float = 0.0
    progress: float = 0.0
    repeated_action_count: int = 0
    steps_without_progress: int = 0
    last_action: str | None = None
    step_success: bool = True
    timed_out: bool = False
    tool_error: bool = False


class TelemetryEvent(BaseModel):
    event_id: str
    flight_id: str
    mission_id: str
    route_id: str | None = None
    waypoint_id: str | None = None
    timestamp: datetime
    metrics: TelemetryMetrics
    provenance: dict[str, SignalProvenance] = Field(default_factory=dict)


class StepObservation(BaseModel):
    """Raw runtime signal. Radar normalizes this; it is not a TelemetryEvent."""

    flight_id: str
    mission_id: str
    route_id: str
    waypoint_id: str
    waypoint_index: int
    latency_ms: float
    success: bool = True
    tool_error: bool = False
    timeout: bool = False
    token_usage: int | None = None
    confidence: float | None = None
    data_freshness_s: float | None = None
    progress: float = 0.0
    action: str = ""
    output: str | None = None
    error: str | None = None
    repeat_signal: bool = False
    side_effect_class: SideEffectClass = SideEffectClass.READ_ONLY
    side_effect_committed: bool = False
    idempotency_key: str | None = None
    duplicate_suppressed: bool = False
    compensation_available: bool = False
    fault_injected: bool = False
    fault_kind: str | None = None
    cancelled: bool = False


class CommittedEffect(BaseModel):
    waypoint_id: str
    waypoint_name: str
    route_id: str
    side_effect: SideEffectClass
    idempotency_key: str | None = None
    compensation_name: str | None = None
    compensated: bool = False


class Hazard(BaseModel):
    hazard_id: str
    flight_id: str
    type: HazardType
    severity: HazardSeverity
    timestamp: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    recommended_action: ControlAction
    scope: HazardScope = HazardScope.ROUTE_LOCAL
    auto_clear: bool = True
    occurrence: int = 1


class ScoredRoute(BaseModel):
    route: Route
    score: float
    reasons: list[str]


class ControlDecision(BaseModel):
    decision_id: str
    flight_id: str
    action: ControlAction
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    previous_route: str | None = None
    new_route: str | None = None
    timestamp: datetime
    compensation_required: bool = False
    follow_up: ControlAction | None = None
    blocked_reason: str | None = None


class HumanIntervention(BaseModel):
    intervention_id: str
    flight_id: str
    action: ControlAction
    operator: str = "human"
    role: OperatorRole = OperatorRole.ADMIN
    reason: str
    route_id: str | None = None
    timestamp: datetime


class StateTransition(BaseModel):
    flight_id: str
    from_state: ExecutionState
    to_state: ExecutionState
    reason: str
    timestamp: datetime


class FlightResult(BaseModel):
    success: bool
    terminal_state: ExecutionState
    summary: str
    waypoint_index: int = 0
    retries: int = 0
    route_changes: int = 0
    human_interventions: int = 0
    hazard_count: int = 0
    failure_reason: FailureReason | None = None
    side_effect_incidents: int = 0
    compensations: int = 0
    compensation_failures: int = 0


class Flight(BaseModel):
    flight_id: str
    mission_id: str
    agent: Agent
    state: ExecutionState = ExecutionState.CREATED
    plan: FlightPlan | None = None
    current_route_id: str | None = None
    current_waypoint_index: int = 0
    retry_count: int = 0
    consecutive_retries: int = 0
    route_changes: int = 0
    failed_route_ids: list[str] = Field(default_factory=list)
    hold_count: int = 0
    human_interventions: int = 0
    created_at: datetime
    updated_at: datetime
    result: FlightResult | None = None
    accumulated_cost: float = 0.0
    execution_time_ms: float = 0.0
    committed_effects: list[CommittedEffect] = Field(default_factory=list)
    failure_reason: FailureReason | None = None
    compensation_count: int = 0
    compensation_failures: int = 0
    side_effect_incidents: int = 0
    hazard_count: int = 0
    cancel_requested: bool = False
    runtime_can_compensate: bool = False
    runtime_supports_idempotency: bool = False
    runtime_can_retry: bool = True
    runtime_can_cancel: bool = False

    def current_route(self) -> Route:
        if self.plan is None or self.current_route_id is None:
            raise NoRouteAvailable("flight has no current route")
        return self.plan.route_by_id(self.current_route_id)

    def current_waypoint(self) -> Waypoint | None:
        route = self.current_route()
        if self.current_waypoint_index >= len(route.waypoints):
            return None
        return route.waypoints[self.current_waypoint_index]

    def open_effects(self, route_id: str | None = None) -> list[CommittedEffect]:
        effects = [effect for effect in self.committed_effects if not effect.compensated]
        if route_id is None:
            return effects
        return [effect for effect in effects if effect.route_id == route_id]


def make_decision(
    flight_id: str,
    action: ControlAction,
    reason: str,
    timestamp: datetime,
    *,
    previous_route: str | None = None,
    new_route: str | None = None,
    evidence: dict[str, Any] | None = None,
    compensation_required: bool = False,
    follow_up: ControlAction | None = None,
    blocked_reason: str | None = None,
) -> ControlDecision:
    return ControlDecision(
        decision_id=new_id("dec"),
        flight_id=flight_id,
        action=action,
        reason=reason,
        evidence=evidence or {},
        previous_route=previous_route,
        new_route=new_route,
        timestamp=timestamp,
        compensation_required=compensation_required,
        follow_up=follow_up,
        blocked_reason=blocked_reason,
    )
