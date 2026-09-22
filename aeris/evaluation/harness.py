"""Compare CONTROL vs AERIS on the same seeded scenarios.

The harness does not declare a winner. It reports rates that can falsify
the hypothesis that AERIS increases recovery under injected failures.
"""

from __future__ import annotations

from pydantic import BaseModel

from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import ControlAction, EventType, ExecutionState, InterventionMode
from aeris.evaluation.metrics import AggregateMetrics, FlightMetrics, summarize
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner
from aeris.simulation.scenarios import Scenario, builtin_scenarios


class ExperimentReport(BaseModel):
    hypothesis: str
    notes: str
    control: AggregateMetrics
    aeris: AggregateMetrics
    delta_recovery_rate: float | None
    rows: list[FlightMetrics]
    statistics_note: str


async def run_one(scenario: Scenario, mode: InterventionMode, policy: ThresholdPolicy | None = None) -> FlightMetrics:
    policy = policy or ThresholdPolicy(human_on_critical=False, hold_ms=0)
    clock = FakeClock()
    recorder = InMemoryRecorder()
    runtime = scenario.runtime()
    planner = RoutePlanner()
    controller = ATCController(planner=planner, policy=policy, clock=clock, mode=mode)
    director = FlightDirector(
        recorder=recorder,
        runtime=runtime,
        planner=planner,
        radar=RadarEngine(clock=clock),
        controller=controller,
        policy=policy,
        clock=clock,
    )
    mission = director.create_mission(scenario.objective, scenario.success_criteria)
    await director.record_mission(mission)
    flight = director.create_flight(mission)
    await director.run(flight, scenario.routes)
    if flight.state == ExecutionState.WAITING_HUMAN:
        await director.apply_human_action(
            flight.flight_id,
            action=ControlAction.ABORT,
            reason="experiment has no live human; abort waiting flights",
        )
    timeline = await recorder.timeline(flight.flight_id)
    hazards = [event for event in timeline if event.event_type == EventType.HAZARD]
    telemetry = [event for event in timeline if event.event_type == EventType.TELEMETRY]
    decisions = [event for event in timeline if event.event_type == EventType.DECISION]
    latency = 0.0
    if telemetry:
        latency = float(telemetry[-1].payload.get("metrics", {}).get("execution_time_ms", 0.0))
    success = flight.state == ExecutionState.COMPLETED
    recovered = bool(scenario.injects_failure and success)
    detected = {event.payload.get("type") for event in hazards}
    expected = {hazard.value for hazard in scenario.ground_truth_hazards}
    true_positives = len(expected & detected)
    false_negatives = len(expected - detected)
    false_positives = len(detected - expected)
    true_negatives = 1 if not expected and not detected else 0
    interventions = [
        event for event in decisions if event.payload.get("action") not in {None, ControlAction.CONTINUE.value}
    ]
    operational = bool(expected) or scenario.injects_failure
    useful = len(interventions) if operational else 0
    unnecessary = 0 if operational else len(interventions)
    failure_reason = None
    if flight.result is not None and flight.result.failure_reason is not None:
        failure_reason = flight.result.failure_reason.value
    return FlightMetrics(
        scenario_id=scenario.scenario_id,
        mode=mode.value,
        flight_id=flight.flight_id,
        success=success,
        terminal_state=flight.state,
        latency_ms=latency,
        retries=flight.retry_count,
        route_changes=flight.route_changes,
        human_interventions=flight.human_interventions,
        hazard_count=len(hazards),
        injected_failure=scenario.injects_failure,
        recovered=recovered,
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=true_negatives,
        false_negatives=false_negatives,
        mttd_ms=_mttd(timeline),
        mtti_ms=_mtti(timeline),
        mttr_ms=_mttr(timeline, success=success),
        interventions=len(interventions),
        useful_interventions=useful,
        unnecessary_interventions=unnecessary,
        side_effect_incidents=flight.side_effect_incidents,
        compensations=flight.compensation_count,
        compensation_failures=flight.compensation_failures,
        failure_reason=failure_reason,
    )


def _delta_ms(start, end) -> float:
    return (end - start).total_seconds() * 1000


def _mttd(timeline) -> float | None:
    """Pair each fault with the next hazard in recorder order.

    Timestamps can coincide across steps, so sequence is the pairing key.
    Mean time is undefined when a fault is never followed by a hazard.
    """

    samples: list[float] = []
    pending = None
    for event in timeline:
        if event.event_type == EventType.FAULT_INJECTED:
            pending = event
        elif event.event_type == EventType.HAZARD and pending is not None:
            samples.append(_delta_ms(pending.timestamp, event.timestamp))
            pending = None
    if not samples:
        return None
    return sum(samples) / len(samples)


def _mtti(timeline) -> float | None:
    hazards = [event for event in timeline if event.event_type == EventType.HAZARD]
    decisions = [
        event
        for event in timeline
        if event.event_type == EventType.DECISION
        and event.payload.get("action") not in {None, ControlAction.CONTINUE.value}
    ]
    if not hazards or not decisions:
        return None
    return _delta_ms(hazards[0].timestamp, decisions[0].timestamp)


def _mttr(timeline, *, success: bool) -> float | None:
    if not success:
        return None
    faults = [event for event in timeline if event.event_type == EventType.FAULT_INJECTED]
    completed = [event for event in timeline if event.event_type == EventType.FLIGHT_COMPLETED]
    if not faults or not completed:
        return None
    return _delta_ms(faults[0].timestamp, completed[-1].timestamp)


async def run_experiment(
    scenario_ids: list[str] | None = None,
    *,
    repeats: int = 1,
    policy: ThresholdPolicy | None = None,
) -> ExperimentReport:
    catalog = builtin_scenarios()
    ids = scenario_ids or [
        "happy_path",
        "latency_reroute",
        "tool_failure_retry",
        "persistent_tool_failure",
        "all_routes_fail",
        "timeout_critical",
        "false_low_confidence",
        "healthy_noisy_telemetry",
    ]
    rows: list[FlightMetrics] = []
    for scenario_id in ids:
        for _ in range(repeats):
            # Rebuild routes per trial so route_ids stay unique per flight.
            fresh = catalog[scenario_id]
            rows.append(await run_one(fresh, InterventionMode.CONTROL, policy))
            rows.append(await run_one(fresh, InterventionMode.AERIS, policy))
    control = summarize([row for row in rows if row.mode == InterventionMode.CONTROL.value])
    aeris = summarize([row for row in rows if row.mode == InterventionMode.AERIS.value])
    delta = None
    if control.recovery_rate is not None and aeris.recovery_rate is not None:
        delta = aeris.recovery_rate - control.recovery_rate
    return ExperimentReport(
        hypothesis="AERIS increases recovery rate under injected runtime failures.",
        notes=(
            "This report does not accept or reject the hypothesis. "
            "A zero or negative delta_recovery_rate would falsify it on this fixture. "
            "task_success_rate can fall when AERIS intervenes on an untrusted signal, "
            "as in false_low_confidence. "
            "Deterministic repeats are not independent samples; p-values are not computed."
        ),
        control=control,
        aeris=aeris,
        delta_recovery_rate=delta,
        rows=rows,
        statistics_note=(
            "V0 fixtures are deterministic. Repeating them does not increase statistical "
            "power. Seeded stochastic repetitions belong to a later runtime experiment."
        ),
    )
