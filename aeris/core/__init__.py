"""Domain models, identifiers, clocks, and the flight state machine."""

from aeris.core.clock import Clock, FakeClock, SystemClock
from aeris.core.enums import (
    ControlAction,
    EventType,
    ExecutionState,
    HazardSeverity,
    HazardType,
    InterventionMode,
)
from aeris.core.ids import new_id
from aeris.core.models import (
    Agent,
    ControlDecision,
    Flight,
    FlightPlan,
    FlightResult,
    Hazard,
    HumanIntervention,
    Mission,
    Route,
    ScoredRoute,
    StateTransition,
    StepObservation,
    TelemetryEvent,
    TelemetryMetrics,
    Waypoint,
)
from aeris.core.state_machine import InvalidTransition, StateMachine

__all__ = [
    "Agent",
    "Clock",
    "ControlAction",
    "ControlDecision",
    "EventType",
    "ExecutionState",
    "FakeClock",
    "Flight",
    "FlightPlan",
    "FlightResult",
    "Hazard",
    "HazardSeverity",
    "HazardType",
    "HumanIntervention",
    "InterventionMode",
    "InvalidTransition",
    "Mission",
    "Route",
    "ScoredRoute",
    "StateMachine",
    "StateTransition",
    "StepObservation",
    "SystemClock",
    "TelemetryEvent",
    "TelemetryMetrics",
    "Waypoint",
    "new_id",
]
