"""Compare CONTROL vs AERIS on the same deterministic simulator scenarios.

The harness does not declare a winner. It reports rates that can falsify
the hypothesis that AERIS increases recovery under injected failures.
The live stochastic case study lives in ``aeris.experiments``.
"""

from __future__ import annotations

from pydantic import BaseModel

from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import (
    ControlAction,
    EventType,
    ExecutionState,
    InterventionMode,
    OperatorRole,
)
from aeris.evaluation.matching import match_timeline
from aeris.evaluation.metrics import AggregateMetrics, FlightMetrics, summarize
from aeris.evaluation.pairing import TrialOutcome, classify_intervention
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.engine import RadarEngine
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner
from aeris.simulation.scenarios import Scenario, builtin_scenarios

HARNESS_OPERATOR = "experiment-harness"


class ExperimentReport(BaseModel):
    hypothesis: str
    notes: str
    control: AggregateMetrics
    aeris: AggregateMetrics
    delta_recovery_rate: float | None
    rows: list[FlightMetrics]
    statistics_note: str
    policy_hash: str


async def run_one(scenario: Scenario, mode: InterventionMode, policy: ThresholdPolicy | None = None) -> FlightMetrics:
    metrics, _ = await _run_one(scenario, mode, policy)
    return metrics


async def _run_one(
    scenario: Scenario, mode: InterventionMode, policy: ThresholdPolicy | None = None
) -> tuple[FlightMetrics, TrialOutcome]:
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
        detector=HazardDetector(policy=policy, clock=clock),
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
            role=OperatorRole.ADMIN,
            operator=HARNESS_OPERATOR,
        )
    timeline = await recorder.timeline(flight.flight_id)
    match = match_timeline(timeline)
    hazards = [event for event in timeline if event.event_type == EventType.HAZARD]
    telemetry = [event for event in timeline if event.event_type == EventType.TELEMETRY]
    decisions = [event for event in timeline if event.event_type == EventType.DECISION]
    latency = 0.0
    if telemetry:
        latency = float(telemetry[-1].payload.get("metrics", {}).get("execution_time_ms", 0.0))
    success = flight.state == ExecutionState.COMPLETED
    recovered = bool(scenario.injects_failure and success)
    ground_truth_faults = [fault for fault in match.faults if fault.ground_truth]
    true_negatives = 1 if not ground_truth_faults and not hazards else 0
    interventions = [
        event for event in decisions if event.payload.get("action") not in {None, ControlAction.CONTINUE.value}
    ]
    operational = bool(ground_truth_faults) or scenario.injects_failure
    useful = len(interventions) if operational else 0
    unnecessary = 0 if operational else len(interventions)
    failure_reason = None
    if flight.result is not None and flight.result.failure_reason is not None:
        failure_reason = flight.result.failure_reason.value
    samples = match.mttd_samples
    mtti = match.mtti_samples
    mttr = match.mttr_samples
    metrics = FlightMetrics(
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
        true_positives=match.true_positives,
        false_positives=match.false_positives,
        true_negatives=true_negatives,
        false_negatives=match.false_negatives,
        duplicate_hazards=match.duplicate_hazards,
        unmatched_hazards=match.unmatched_hazards,
        unmatched_faults=match.unmatched_faults,
        late_detections=match.late_detections,
        faults_injected=len(match.faults),
        mttd_ms=sum(samples) / len(samples) if samples else None,
        mtti_ms=sum(mtti) / len(mtti) if mtti else None,
        mttr_ms=sum(mttr) / len(mttr) if mttr else None,
        interventions=len(interventions),
        useful_interventions=useful,
        unnecessary_interventions=unnecessary,
        side_effect_incidents=flight.side_effect_incidents,
        compensations=flight.compensation_count,
        compensation_failures=flight.compensation_failures,
        failure_reason=failure_reason,
        total_cost=flight.accumulated_cost,
    )
    outcome = TrialOutcome(
        flight_id=flight.flight_id,
        arm=mode.value,
        acceptable=success,
        completed=success,
        interventions=len(interventions),
        duplicate_side_effects=flight.side_effect_incidents,
        manifested_schedule_ids=match.manifested_schedule_ids,
        pre_intervention_schedule_ids=match.pre_intervention_schedule_ids,
    )
    return metrics, outcome


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
    effective_policy = policy or ThresholdPolicy(human_on_critical=False, hold_ms=0)
    rows: list[FlightMetrics] = []
    for scenario_id in ids:
        for _ in range(repeats):
            fresh = catalog[scenario_id]
            control, control_outcome = await _run_one(fresh, InterventionMode.CONTROL, effective_policy)
            aeris, aeris_outcome = await _run_one(fresh, InterventionMode.AERIS, effective_policy)
            label, _ = classify_intervention(control_outcome, aeris_outcome)
            aeris.intervention_outcome = label
            rows.extend([control, aeris])
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
            "Simulator fixtures are deterministic. Repeating them does not increase statistical "
            "power, so no confidence interval or p-value is reported here. Seeded stochastic "
            "repetitions belong to the live case study (aeris.experiments)."
        ),
        policy_hash=effective_policy.policy_hash(),
    )
