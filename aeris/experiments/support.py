"""Paired CONTROL vs AERIS experiment on the enterprise support case.

Arms (see EXPERIMENT_PROTOCOL.md):
  CONTROL                    no ATC; the agent owns retries (sees tool errors)
  AERIS_FULL                 ATC on; contextual radar if baselines exist
  AERIS_NO_CONTEXT           static thresholds only
  AERIS_NO_DIVERSITY         planner ignores failure domains
  AERIS_NO_SIDE_EFFECT_GATE  retries/reroutes skip the side-effect guard

Seeds are split: calibration seeds learn baselines, validation seeds only
report the contextual false-alarm rate, test seeds produce results. The
three ranges never overlap.

Every record carries the configuration identity. Deterministic fixtures
(the scripted model) never get confidence intervals.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from aeris.adapters.llm import ChatModel
from aeris.adapters.llm_runtime import RUNTIME_ADAPTER_VERSION, LLMToolAgentRuntime
from aeris.cases.support import case
from aeris.cases.support.campaign import CaseScenario, campaign
from aeris.cases.support.evaluator import EVALUATOR_VERSION, evaluate
from aeris.cases.support.toolbox import SupportToolbox
from aeris.cases.support.world import TOOL_FIXTURE_VERSION, SandboxCreditLedger
from aeris.control.controller import ATCController
from aeris.control.director import FlightDirector
from aeris.core.clock import FakeClock
from aeris.core.enums import (
    ControlAction,
    EventType,
    ExecutionState,
    ExperimentArm,
    InterventionMode,
    InterventionOutcome,
    OperatorRole,
    RadarMode,
)
from aeris.core.ids import new_id
from aeris.core.models import Agent, Route
from aeris.evaluation.matching import match_timeline
from aeris.evaluation.pairing import TrialOutcome, TrialPair, classify_intervention, utility_rates
from aeris.experiments.stats import Interval, bootstrap_ci, ci_allowed, mean_or_none, median_or_none
from aeris.experiments.trace import format_trace
from aeris.policies.detector import HazardDetector
from aeris.policies.thresholds import ThresholdPolicy
from aeris.radar.baseline import BaselineKey, BaselineStore
from aeris.radar.engine import RadarEngine
from aeris.recorder.base import RecordedEvent
from aeris.recorder.memory import InMemoryRecorder
from aeris.routing.planner import RoutePlanner

EXPERIMENT_VERSION = "support-experiment/1.0.0"
HARNESS_OPERATOR = "experiment-harness"
RUNTIME_KIND = "llm-tool-agent"

CALIBRATION_SEEDS = range(10_000, 10_010)
VALIDATION_SEEDS = range(20_000, 20_010)

ModelFactory = Callable[[int], ChatModel]

DEFAULT_ARMS = (
    ExperimentArm.CONTROL,
    ExperimentArm.AERIS_FULL,
    ExperimentArm.AERIS_NO_CONTEXT,
    ExperimentArm.AERIS_NO_DIVERSITY,
    ExperimentArm.AERIS_NO_SIDE_EFFECT_GATE,
)


def experiment_policy(**overrides: Any) -> ThresholdPolicy:
    """No live human in the experiment: CRITICAL hazards do not wait for one."""
    values: dict[str, Any] = {
        "human_on_critical": False,
        "escalate_irreversible": False,
        "hold_ms": 0,
        "max_execution_time_ms": 180_000.0,
    }
    values.update(overrides)
    return ThresholdPolicy(**values)


class ArmSetup(BaseModel):
    arm: ExperimentArm
    mode: InterventionMode
    policy: ThresholdPolicy
    use_diversity: bool = True
    agent_owns_retries: bool = False
    contextual: bool = False


def arm_setup(arm: ExperimentArm, base: ThresholdPolicy, *, have_baselines: bool) -> ArmSetup:
    if arm is ExperimentArm.CONTROL:
        return ArmSetup(arm=arm, mode=InterventionMode.CONTROL, policy=base, agent_owns_retries=True)
    contextual = have_baselines and arm is not ExperimentArm.AERIS_NO_CONTEXT
    policy = base.model_copy(
        update={
            "radar_mode": RadarMode.CONTEXTUAL_THRESHOLD if contextual else RadarMode.STATIC_THRESHOLD,
            "avoid_shared_failure_domain": arm is not ExperimentArm.AERIS_NO_DIVERSITY,
            "side_effect_gate": arm is not ExperimentArm.AERIS_NO_SIDE_EFFECT_GATE,
        }
    )
    return ArmSetup(
        arm=arm,
        mode=InterventionMode.AERIS,
        policy=policy,
        use_diversity=arm is not ExperimentArm.AERIS_NO_DIVERSITY,
        contextual=contextual,
    )


class TrialConfig(BaseModel):
    experiment_version: str = EXPERIMENT_VERSION
    policy_version: str
    policy_hash: str
    planner_weights_hash: str
    route_config_hash: str
    agent_prompt_hash: str
    runtime_adapter_version: str = RUNTIME_ADAPTER_VERSION
    scenario_version: str = case.SCENARIO_VERSION
    tool_fixture_version: str = TOOL_FIXTURE_VERSION
    evaluator_version: str = EVALUATOR_VERSION
    baseline_fingerprint: str | None = None
    model: str
    model_versions: list[str] = Field(default_factory=list)
    stochastic_model: bool
    temperature: float
    seed: int
    started_at: str


class TrialRecord(BaseModel):
    trial_id: str
    pair_id: str
    scenario_id: str
    category: str
    arm: ExperimentArm
    seed: int
    flight_id: str
    terminal_state: ExecutionState
    completed: bool
    task_complete: bool
    task_correct: bool
    unsafe_action: bool
    duplicate_action: bool
    unsupported_claim: bool
    evaluator_checks: dict[str, bool] = Field(default_factory=dict)
    evaluator_notes: list[str] = Field(default_factory=list)
    credit_entries: int = 0
    idempotent_replays: int = 0
    failure_reason: str | None = None
    route_path: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    model_cost: float = 0.0
    tool_cost: float = 0.0
    total_cost: float = 0.0
    retries: int = 0
    route_changes: int = 0
    hazards: int = 0
    interventions: int = 0
    intervention_actions: list[str] = Field(default_factory=list)
    unnecessary_intervention: bool = False
    ground_truth_fault_manifested: bool = False
    recovered: bool = False
    faults_injected: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    duplicate_hazards: int = 0
    unmatched_hazards: int = 0
    unmatched_faults: int = 0
    late_detections: int = 0
    mttd_ms: float | None = None
    mtti_ms: float | None = None
    mttr_ms: float | None = None
    intervention_outcome: InterventionOutcome | None = None
    attribution_note: str = ""
    config: TrialConfig


class TrialRun(BaseModel):
    """A record plus what reports need but the JSONL row does not."""

    model_config = {"arbitrary_types_allowed": True}

    record: TrialRecord
    outcome: TrialOutcome
    timeline: list[RecordedEvent]
    route_names: dict[str, str]


async def run_trial(
    scenario: CaseScenario,
    arm: ExperimentArm,
    *,
    seed: int,
    model_factory: ModelFactory,
    base_policy: ThresholdPolicy | None = None,
    baselines: BaselineStore | None = None,
    temperature: float = 0.0,
    pair_id: str | None = None,
    routes_override: list[Route] | None = None,
) -> TrialRun:
    base_policy = base_policy or experiment_policy()
    setup = arm_setup(arm, base_policy, have_baselines=baselines is not None)
    clock = FakeClock()
    recorder = InMemoryRecorder()
    ledger = SandboxCreditLedger()
    toolbox = SupportToolbox(faults=copy.deepcopy(scenario.faults), ledger=ledger)
    model = model_factory(seed)
    runtime = LLMToolAgentRuntime(
        model,
        toolbox,
        system_prompt=case.SYSTEM_PROMPT,
        mission_prompt=case.MISSION_PROMPT,
        phases=case.PHASES,
        temperature=temperature,
        seed=seed,
        runtime_kind=RUNTIME_KIND,
        agent_owns_retries=setup.agent_owns_retries,
    )
    routes = routes_override or case.build_routes(
        scenario.route_set,
        keyed_credit=scenario.keyed_credit,
        model_provider="scripted" if not model.stochastic else "openai-compatible",
        model_family=model.name,
    )
    planner = RoutePlanner(use_diversity=setup.use_diversity)
    controller = ATCController(planner=planner, policy=setup.policy, clock=clock, mode=setup.mode)
    detector = HazardDetector(
        policy=setup.policy, clock=clock, baselines=baselines if setup.contextual else None
    )
    director = FlightDirector(
        recorder=recorder,
        runtime=runtime,
        planner=planner,
        radar=RadarEngine(clock=clock),
        detector=detector,
        controller=controller,
        policy=setup.policy,
        clock=clock,
    )
    started_at = datetime.now(UTC).isoformat()
    mission = director.create_mission(case.OBJECTIVE, case.SUCCESS_CRITERIA)
    await director.record_mission(mission)
    flight = director.create_flight(
        mission, agent=Agent(agent_id=new_id("agt"), name="support-agent", runtime_kind=RUNTIME_KIND)
    )
    await director.run(flight, routes)
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
    completed = flight.state == ExecutionState.COMPLETED
    score = evaluate(
        runtime.answers.get(flight.flight_id), flight_id=flight.flight_id, ledger=ledger, completed=completed
    )
    route_names = {route.route_id: route.name for route in routes}
    decisions = [event for event in timeline if event.event_type == EventType.DECISION]
    interventions = [
        event for event in decisions if event.payload.get("action") not in {None, ControlAction.CONTINUE.value}
    ]
    plan_event = next((event for event in timeline if event.event_type == EventType.PLAN_CREATED), None)
    path = [route_names.get(plan_event.payload.get("selected_route_id", ""), "?")] if plan_event else []
    for event in interventions:
        if event.payload.get("action") == ControlAction.REROUTE.value:
            path.append(route_names.get(event.payload.get("new_route", ""), "?"))

    truth_schedules = {fault.schedule_id for fault in scenario.faults if fault.ground_truth}
    manifested_truth = bool(truth_schedules & set(match.manifested_schedule_ids))
    triggered_by_truth = bool(truth_schedules & set(match.pre_intervention_schedule_ids))
    failure_reason = None
    if flight.result is not None and flight.result.failure_reason is not None:
        failure_reason = flight.result.failure_reason.value

    config = TrialConfig(
        policy_version=setup.policy.version,
        policy_hash=setup.policy.policy_hash(),
        planner_weights_hash=planner.weights_hash(),
        route_config_hash=case.route_config_hash(routes),
        agent_prompt_hash=runtime.prompt_hash(),
        baseline_fingerprint=baselines.fingerprint() if baselines is not None and setup.contextual else None,
        model=model.name,
        model_versions=sorted(runtime.model_versions),
        stochastic_model=model.stochastic,
        temperature=temperature,
        seed=seed,
        started_at=started_at,
    )
    record = TrialRecord(
        trial_id=new_id("trial"),
        pair_id=pair_id or new_id("pair"),
        scenario_id=scenario.scenario_id,
        category=scenario.category,
        arm=arm,
        seed=seed,
        flight_id=flight.flight_id,
        terminal_state=flight.state,
        completed=completed,
        task_complete=score.task_complete,
        task_correct=score.task_correct,
        unsafe_action=score.unsafe_action,
        duplicate_action=score.duplicate_action,
        unsupported_claim=score.unsupported_claim,
        evaluator_checks=score.checks,
        evaluator_notes=score.notes,
        credit_entries=score.credit_entries,
        idempotent_replays=score.idempotent_replays,
        failure_reason=failure_reason,
        route_path=path,
        latency_ms=flight.execution_time_ms,
        input_tokens=flight.input_tokens,
        output_tokens=flight.output_tokens,
        tool_calls=flight.tool_calls,
        model_cost=flight.model_cost,
        tool_cost=flight.tool_cost,
        total_cost=flight.model_cost + flight.tool_cost,
        retries=flight.retry_count,
        route_changes=flight.route_changes,
        hazards=flight.hazard_count,
        interventions=len(interventions),
        intervention_actions=[str(event.payload.get("action")) for event in interventions],
        unnecessary_intervention=bool(interventions) and not triggered_by_truth,
        ground_truth_fault_manifested=manifested_truth,
        recovered=manifested_truth and score.task_correct,
        faults_injected=len(match.faults),
        true_positives=match.true_positives,
        false_positives=match.false_positives,
        false_negatives=match.false_negatives,
        duplicate_hazards=match.duplicate_hazards,
        unmatched_hazards=match.unmatched_hazards,
        unmatched_faults=match.unmatched_faults,
        late_detections=match.late_detections,
        mttd_ms=mean_or_none(match.mttd_samples),
        mtti_ms=mean_or_none(match.mtti_samples),
        mttr_ms=mean_or_none(match.mttr_samples),
        config=config,
    )
    outcome = TrialOutcome(
        flight_id=flight.flight_id,
        arm=arm.value,
        acceptable=score.task_correct,
        completed=completed,
        interventions=len(interventions),
        unsafe_actions=int(score.unsafe_action),
        duplicate_side_effects=max(score.credit_entries - 1, 0),
        manifested_schedule_ids=match.manifested_schedule_ids,
        pre_intervention_schedule_ids=match.pre_intervention_schedule_ids,
    )
    return TrialRun(record=record, outcome=outcome, timeline=timeline, route_names=route_names)


# ------------------------------------------------------------ calibration


class CalibrationReport(BaseModel):
    seeds: list[int]
    baselines: list[dict[str, Any]]
    fingerprint: str
    validation_seeds: list[int] = Field(default_factory=list)
    validation_flights: int = 0
    validation_false_alarm_flights: int = 0
    validation_note: str = ""


async def calibrate(
    model_factory: ModelFactory,
    *,
    seeds: Sequence[int] = CALIBRATION_SEEDS,
    temperature: float = 0.0,
) -> tuple[BaselineStore, CalibrationReport]:
    """Learn per-(route, waypoint, tool) baselines from healthy CONTROL flights."""
    store = BaselineStore()
    healthy = campaign()["healthy"]
    for seed in seeds:
        for key in ("alpha", "bravo", "bravo_shared"):
            route = case.build_route(key)
            run = await run_trial(
                healthy,
                ExperimentArm.CONTROL,
                seed=seed,
                model_factory=model_factory,
                temperature=temperature,
                routes_override=[route],
            )
            waypoints = {waypoint.waypoint_id: waypoint for waypoint in route.waypoints}
            for event in run.timeline:
                if event.event_type != EventType.TELEMETRY:
                    continue
                waypoint = waypoints.get(event.waypoint_id or "")
                if waypoint is None:
                    continue
                metrics = event.payload.get("metrics", {})
                store.observe(
                    BaselineKey(
                        runtime=RUNTIME_KIND, route=route.name, waypoint=waypoint.name, tool=waypoint.tool or "*"
                    ),
                    float(metrics.get("step_latency_ms", 0.0)),
                    tokens=metrics.get("step_token_usage"),
                    cost=metrics.get("step_cost"),
                )
    store.freeze()
    report = CalibrationReport(
        seeds=list(seeds),
        baselines=[baseline.model_dump(mode="json") for baseline in store.all()],
        fingerprint=store.fingerprint(),
    )
    return store, report


async def validate(
    model_factory: ModelFactory,
    store: BaselineStore,
    report: CalibrationReport,
    *,
    seeds: Sequence[int] = VALIDATION_SEEDS,
    temperature: float = 0.0,
) -> CalibrationReport:
    """Healthy AERIS_FULL flights on held-out seeds. Reported, never used to tune."""
    healthy = campaign()["healthy"]
    alarms = 0
    for seed in seeds:
        run = await run_trial(
            healthy,
            ExperimentArm.AERIS_FULL,
            seed=seed,
            model_factory=model_factory,
            baselines=store,
            temperature=temperature,
        )
        alarms += int(run.record.interventions > 0)
    return report.model_copy(
        update={
            "validation_seeds": list(seeds),
            "validation_flights": len(seeds),
            "validation_false_alarm_flights": alarms,
            "validation_note": "healthy flights with at least one AERIS intervention (lower is better)",
        }
    )


# ------------------------------------------------------------ aggregation


class ArmSummary(BaseModel):
    arm: str
    n: int
    task_correct: int
    task_correct_rate: float | None
    completed: int
    terminal_failures: int
    unsafe_actions: int
    duplicate_side_effects: int
    faulted_flights: int
    recovered: int
    recovery_rate: float | None
    interventions: int
    unnecessary_interventions: int
    healthy_flights_changed: int
    reroutes: int
    retries: int
    median_latency_ms: float | None
    median_cost_usd: float | None
    total_cost_usd: float
    cost_per_successful_task: float | None
    event_precision: float | None
    event_recall: float | None
    false_positives: int
    false_negatives: int
    duplicate_hazards: int
    unmatched_hazards: int
    unmatched_faults: int
    late_detections: int
    mean_mttd_ms: float | None
    mean_mtti_ms: float | None
    mean_mttr_ms: float | None
    utility: dict[str, float | int | None] = Field(default_factory=dict)


class ArmDelta(BaseModel):
    arm: str
    pairs: int
    control_success_rate: float | None
    arm_success_rate: float | None
    absolute_success_delta_pp: float | None
    control_terminal_failures: int
    arm_terminal_failures: int
    relative_failure_reduction: float | None
    absolute_recovery_delta: int
    median_latency_overhead_ms: float | None
    median_cost_overhead_usd: float | None
    harmful_interventions: int
    intervened_flights: int
    duplicate_side_effects_avoided: int
    incremental_cost_per_recovered_task: float | None
    success_delta_ci: Interval | None = None
    latency_overhead_ci: Interval | None = None
    ci_note: str = ""


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize_arm(arm: str, records: list[TrialRecord]) -> ArmSummary:
    tp = sum(row.true_positives for row in records)
    fp = sum(row.false_positives for row in records)
    fn = sum(row.false_negatives for row in records)
    correct = [row for row in records if row.task_correct]
    faulted = [row for row in records if row.ground_truth_fault_manifested]
    total_cost = sum(row.total_cost for row in records)
    return ArmSummary(
        arm=arm,
        n=len(records),
        task_correct=len(correct),
        task_correct_rate=_rate(len(correct), len(records)),
        completed=sum(1 for row in records if row.completed),
        terminal_failures=sum(1 for row in records if not row.completed),
        unsafe_actions=sum(1 for row in records if row.unsafe_action),
        duplicate_side_effects=sum(max(row.credit_entries - 1, 0) for row in records),
        faulted_flights=len(faulted),
        recovered=sum(1 for row in faulted if row.recovered),
        recovery_rate=_rate(sum(1 for row in faulted if row.recovered), len(faulted)),
        interventions=sum(row.interventions for row in records),
        unnecessary_interventions=sum(1 for row in records if row.unnecessary_intervention),
        healthy_flights_changed=sum(
            1 for row in records if not row.ground_truth_fault_manifested and row.interventions > 0
        ),
        reroutes=sum(row.route_changes for row in records),
        retries=sum(row.retries for row in records),
        median_latency_ms=median_or_none([row.latency_ms for row in records]),
        median_cost_usd=median_or_none([row.total_cost for row in records]),
        total_cost_usd=total_cost,
        cost_per_successful_task=total_cost / len(correct) if correct else None,
        event_precision=_rate(tp, tp + fp),
        event_recall=_rate(tp, tp + fn),
        false_positives=fp,
        false_negatives=fn,
        duplicate_hazards=sum(row.duplicate_hazards for row in records),
        unmatched_hazards=sum(row.unmatched_hazards for row in records),
        unmatched_faults=sum(row.unmatched_faults for row in records),
        late_detections=sum(row.late_detections for row in records),
        mean_mttd_ms=mean_or_none([row.mttd_ms for row in records if row.mttd_ms is not None]),
        mean_mtti_ms=mean_or_none([row.mtti_ms for row in records if row.mtti_ms is not None]),
        mean_mttr_ms=mean_or_none([row.mttr_ms for row in records if row.mttr_ms is not None]),
        utility=utility_rates([row.intervention_outcome for row in records]),
    )


def paired_delta(
    arm: str,
    pairs: list[tuple[TrialRecord, TrialRecord]],
    *,
    stochastic: bool,
) -> ArmDelta:
    controls = [control for control, _ in pairs]
    arms = [treated for _, treated in pairs]
    control_ok = sum(1 for row in controls if row.task_correct)
    arm_ok = sum(1 for row in arms if row.task_correct)
    n = len(pairs)
    control_rate = _rate(control_ok, n)
    arm_rate = _rate(arm_ok, n)
    control_fail = sum(1 for row in controls if not row.task_correct)
    arm_fail = sum(1 for row in arms if not row.task_correct)
    control_recovered = sum(1 for row in controls if row.recovered)
    arm_recovered = sum(1 for row in arms if row.recovered)
    extra_cost = sum(row.total_cost for row in arms) - sum(row.total_cost for row in controls)
    recovery_gain = arm_recovered - control_recovered
    latency_diffs = [treated.latency_ms - control.latency_ms for control, treated in pairs]
    cost_diffs = [treated.total_cost - control.total_cost for control, treated in pairs]
    success_diffs = [float(treated.task_correct) - float(control.task_correct) for control, treated in pairs]
    allowed, note = ci_allowed(n, stochastic)
    success_ci = bootstrap_ci(success_diffs) if allowed else None
    latency_ci = bootstrap_ci(latency_diffs, statistic=lambda xs: float(median_or_none(xs) or 0.0)) if allowed else None
    return ArmDelta(
        arm=arm,
        pairs=n,
        control_success_rate=control_rate,
        arm_success_rate=arm_rate,
        absolute_success_delta_pp=(arm_rate - control_rate) * 100 if n else None,
        control_terminal_failures=control_fail,
        arm_terminal_failures=arm_fail,
        relative_failure_reduction=(control_fail - arm_fail) / control_fail if control_fail else None,
        absolute_recovery_delta=recovery_gain,
        median_latency_overhead_ms=median_or_none(latency_diffs),
        median_cost_overhead_usd=median_or_none(cost_diffs),
        harmful_interventions=sum(
            1 for row in arms if row.intervention_outcome is InterventionOutcome.HARMFUL
        ),
        intervened_flights=sum(1 for row in arms if row.intervention_outcome is not None),
        duplicate_side_effects_avoided=sum(max(row.credit_entries - 1, 0) for row in controls)
        - sum(max(row.credit_entries - 1, 0) for row in arms),
        incremental_cost_per_recovered_task=extra_cost / recovery_gain if recovery_gain > 0 else None,
        success_delta_ci=success_ci,
        latency_overhead_ci=latency_ci,
        ci_note=note,
    )


class CampaignResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    experiment_version: str = EXPERIMENT_VERSION
    model: str
    stochastic_model: bool
    seeds: list[int]
    arms: list[str]
    scenario_ids: list[str]
    records: list[TrialRecord]
    pairs: list[TrialPair]
    overall: dict[str, ArmSummary]
    by_scenario: dict[str, dict[str, ArmSummary]]
    deltas: dict[str, ArmDelta]
    calibration: CalibrationReport | None = None
    expectations: dict[str, str] = Field(default_factory=dict)
    sample_traces: dict[str, str] = Field(default_factory=dict)


async def run_campaign(
    model_factory: ModelFactory,
    *,
    scenario_ids: Sequence[str] | None = None,
    arms: Sequence[ExperimentArm] = DEFAULT_ARMS,
    seeds: Sequence[int] = (0,),
    temperature: float = 0.0,
    base_policy: ThresholdPolicy | None = None,
    contextual: bool = True,
    keep_traces: bool = True,
) -> CampaignResult:
    catalog = campaign()
    ids = list(scenario_ids or catalog)
    base_policy = base_policy or experiment_policy()
    store: BaselineStore | None = None
    calibration: CalibrationReport | None = None
    if contextual:
        store, calibration = await calibrate(model_factory, temperature=temperature)
        calibration = await validate(model_factory, store, calibration, temperature=temperature)
    arms = list(arms)
    if ExperimentArm.CONTROL not in arms:
        arms.insert(0, ExperimentArm.CONTROL)

    records: list[TrialRecord] = []
    pairs: list[TrialPair] = []
    traces: dict[str, str] = {}
    stochastic = False
    model_name = ""

    for scenario_id in ids:
        scenario = catalog[scenario_id]
        for seed in seeds:
            pair_id = new_id("pair")
            control_run = await run_trial(
                scenario,
                ExperimentArm.CONTROL,
                seed=seed,
                model_factory=model_factory,
                base_policy=base_policy,
                baselines=store,
                temperature=temperature,
                pair_id=pair_id,
            )
            stochastic = control_run.record.config.stochastic_model
            model_name = control_run.record.config.model
            records.append(control_run.record)
            if keep_traces and seed == seeds[0]:
                traces[f"{scenario_id}/CONTROL"] = format_trace(
                    control_run.timeline, route_names=control_run.route_names, include_telemetry=False
                )
            for arm in arms:
                if arm is ExperimentArm.CONTROL:
                    continue
                run = await run_trial(
                    scenario,
                    arm,
                    seed=seed,
                    model_factory=model_factory,
                    base_policy=base_policy,
                    baselines=store,
                    temperature=temperature,
                    pair_id=pair_id,
                )
                label, note = classify_intervention(control_run.outcome, run.outcome)
                run.record.intervention_outcome = label
                run.record.attribution_note = note
                records.append(run.record)
                pairs.append(
                    TrialPair(
                        trial_id=pair_id,
                        scenario_id=scenario_id,
                        seed=seed,
                        arm=arm.value,
                        control_flight_id=control_run.record.flight_id,
                        aeris_flight_id=run.record.flight_id,
                        same_fault_schedule=True,
                        same_model_input=True,
                        same_tool_fixture=True,
                        deterministic=not stochastic,
                        intervention_outcome=label,
                        attribution_note=note,
                    )
                )
                if keep_traces and seed == seeds[0] and arm is ExperimentArm.AERIS_FULL:
                    traces[f"{scenario_id}/{arm.value}"] = format_trace(
                        run.timeline, route_names=run.route_names, include_telemetry=False
                    )

    arm_names = [arm.value for arm in arms]
    overall = {name: summarize_arm(name, [row for row in records if row.arm.value == name]) for name in arm_names}
    by_scenario = {
        scenario_id: {
            name: summarize_arm(
                name, [row for row in records if row.arm.value == name and row.scenario_id == scenario_id]
            )
            for name in arm_names
        }
        for scenario_id in ids
    }
    by_pair: dict[str, dict[str, TrialRecord]] = {}
    for row in records:
        by_pair.setdefault(row.pair_id, {})[row.arm.value] = row
    deltas: dict[str, ArmDelta] = {}
    for name in arm_names:
        if name == ExperimentArm.CONTROL.value:
            continue
        matched = [
            (group[ExperimentArm.CONTROL.value], group[name])
            for group in by_pair.values()
            if ExperimentArm.CONTROL.value in group and name in group
        ]
        deltas[name] = paired_delta(name, matched, stochastic=stochastic)
    return CampaignResult(
        model=model_name,
        stochastic_model=stochastic,
        seeds=list(seeds),
        arms=arm_names,
        scenario_ids=ids,
        records=records,
        pairs=pairs,
        overall=overall,
        by_scenario=by_scenario,
        deltas=deltas,
        calibration=calibration,
        expectations={scenario_id: catalog[scenario_id].expectation for scenario_id in ids},
        sample_traces=traces,
    )
