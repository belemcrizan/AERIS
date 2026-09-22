from datetime import UTC, datetime

from aeris.core.enums import ControlAction, HazardSeverity, HazardType
from aeris.core.ids import new_id
from aeris.core.models import Agent, Flight, TelemetryEvent, TelemetryMetrics
from aeris.policies.detector import HazardDetector


def _event(metrics: TelemetryMetrics) -> tuple[Flight, TelemetryEvent]:
    now = datetime.now(UTC)
    flight = Flight(
        flight_id=new_id("flt"),
        mission_id=new_id("msn"),
        agent=Agent(agent_id="agt_1", name="sim"),
        created_at=now,
        updated_at=now,
    )
    telemetry = TelemetryEvent(
        event_id=new_id("tel"),
        flight_id=flight.flight_id,
        mission_id=flight.mission_id,
        timestamp=now,
        metrics=metrics,
    )
    return flight, telemetry


def test_high_latency_warning_recommends_reroute():
    detector = HazardDetector()
    flight, telemetry = _event(TelemetryMetrics(step_latency_ms=5000, latency_ms=5000))
    hazards = detector.detect(flight, telemetry)
    types = {h.type for h in hazards}
    assert HazardType.HIGH_LATENCY in types
    hazard = next(h for h in hazards if h.type is HazardType.HIGH_LATENCY)
    assert hazard.severity is HazardSeverity.WARNING
    assert hazard.recommended_action is ControlAction.REROUTE
    assert hazard.evidence["step_latency_ms"] == 5000


def test_tool_failure_and_timeout_and_budget():
    detector = HazardDetector()
    flight, telemetry = _event(
        TelemetryMetrics(tool_error=True, tool_error_count=1, step_success=False)
    )
    hazards = detector.detect(flight, telemetry)
    assert any(h.type is HazardType.TOOL_FAILURE for h in hazards)

    flight, telemetry = _event(TelemetryMetrics(timed_out=True, step_success=False, step_latency_ms=9000))
    hazards = detector.detect(flight, telemetry)
    assert any(h.type is HazardType.TIMEOUT_RISK for h in hazards)
    assert max(h.severity for h in hazards) is HazardSeverity.CRITICAL

    flight, telemetry = _event(TelemetryMetrics(token_usage=120_000))
    hazards = detector.detect(flight, telemetry)
    budget = next(h for h in hazards if h.type is HazardType.BUDGET_RISK)
    assert budget.severity is HazardSeverity.CRITICAL
    assert budget.recommended_action is ControlAction.ABORT
