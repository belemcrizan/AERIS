"""Contextual baselines: robust statistics, auditable evidence, mode switch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aeris.core.clock import FakeClock
from aeris.core.enums import HazardType, RadarMode
from aeris.core.models import (
    Agent,
    Flight,
    FlightPlan,
    Route,
    TelemetryEvent,
    TelemetryMetrics,
    Waypoint,
)
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.baseline import BaselineKey, BaselineStore, ContextBaseline, mad, robust_z

KEY = BaselineKey(runtime="rt", route="alpha", waypoint="step", tool="search")


def test_robust_z_matches_the_documented_formula():
    assert mad([1, 2, 3, 4, 100]) == 1
    z = robust_z(4780, 910, 120, min_spread=25)
    assert z == pytest.approx((4780 - 910) / (1.4826 * 120))
    assert round(z, 1) == 21.8


def test_min_spread_prevents_division_by_a_zero_mad():
    assert robust_z(110, 100, 0, min_spread=25) == pytest.approx(10 / (1.4826 * 25))


def test_store_learns_then_freezes():
    store = BaselineStore()
    for latency in (900, 910, 920, 1000, 880):
        store.observe(KEY, latency, tokens=500)
    store.freeze()
    baseline = store.lookup(KEY)
    assert baseline.latency_median_ms == 910 and baseline.n == 5 and baseline.source == "learned"
    assert store.lookup(KEY.model_copy(update={"tool": "other"})) is None
    with pytest.raises(RuntimeError):
        store.observe(KEY, 1.0)
    assert store.fingerprint() == store.fingerprint()


def _flight() -> tuple[Flight, TelemetryEvent]:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    waypoint = Waypoint(waypoint_id="w", name="step", index=0, tool="search")
    route = Route(route_id="r", name="alpha", description="a", waypoints=[waypoint])
    flight = Flight(
        flight_id="f", mission_id="m", agent=Agent(agent_id="a", name="a", runtime_kind="rt"),
        plan=FlightPlan(plan_id="p", mission_id="m", routes=[route], selected_route_id="r"),
        current_route_id="r", created_at=now, updated_at=now,
    )
    telemetry = TelemetryEvent(
        event_id="e", flight_id="f", mission_id="m", route_id="r", waypoint_id="w", timestamp=now,
        metrics=TelemetryMetrics(step_latency_ms=4780, step_token_usage=500),
    )
    return flight, telemetry


def _store() -> BaselineStore:
    store = BaselineStore()
    store.declare(ContextBaseline(key=KEY, latency_median_ms=910, latency_mad_ms=120, token_min=400, token_max=600))
    return store


def test_contextual_hazard_explains_observed_baseline_deviation_threshold_and_decision():
    flight, telemetry = _flight()
    policy = ThresholdPolicy(radar_mode=RadarMode.CONTEXTUAL_THRESHOLD, robust_z_threshold=5)
    hazards = HazardDetector(policy=policy, clock=FakeClock(), baselines=_store()).detect(flight, telemetry)
    latency = next(h for h in hazards if h.type is HazardType.HIGH_LATENCY)
    evidence = latency.evidence
    assert evidence["observed_latency_ms"] == 4780
    assert evidence["route_median_ms"] == 910 and evidence["route_mad_ms"] == 120
    assert evidence["robust_z"] == pytest.approx(21.75, abs=0.01)
    assert evidence["threshold"] == 5
    assert "HIGH_LATENCY" in evidence["decision"]


def test_contextual_mode_tolerates_a_slow_but_normal_tool_that_static_mode_flags():
    flight, telemetry = _flight()
    telemetry.metrics.step_latency_ms = 3000
    slow_normal = BaselineStore()
    slow_normal.declare(ContextBaseline(key=KEY, latency_median_ms=2900, latency_mad_ms=150))
    contextual = HazardDetector(
        policy=ThresholdPolicy(radar_mode=RadarMode.CONTEXTUAL_THRESHOLD), clock=FakeClock(), baselines=slow_normal
    ).detect(flight, telemetry)
    static = HazardDetector(policy=ThresholdPolicy(), clock=FakeClock(), baselines=slow_normal).detect(flight, telemetry)
    assert not any(h.type is HazardType.HIGH_LATENCY for h in contextual)
    assert any(h.type is HazardType.HIGH_LATENCY for h in static)


def test_token_usage_above_the_calibrated_range_is_flagged_as_caution():
    flight, telemetry = _flight()
    telemetry.metrics.step_latency_ms = 900
    telemetry.metrics.step_token_usage = 2000
    policy = ThresholdPolicy(radar_mode=RadarMode.CONTEXTUAL_THRESHOLD)
    hazards = HazardDetector(policy=policy, clock=FakeClock(), baselines=_store()).detect(flight, telemetry)
    budget = next(h for h in hazards if h.type is HazardType.BUDGET_RISK)
    assert budget.evidence["expected_token_range"] == [400, 600]
