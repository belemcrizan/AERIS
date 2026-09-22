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
    latency = 0.0
    if telemetry:
        latency = float(telemetry[-1].payload.get("metrics", {}).get("execution_time_ms", 0.0))
    success = flight.state == ExecutionState.COMPLETED
    recovered = bool(scenario.injects_failure and success)
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
    )


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
    ]
    rows: list[FlightMetrics] = []
    for scenario_id in ids:
        scenario = catalog[scenario_id]
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
            "A zero or negative delta_recovery_rate would falsify it on this fixture."
        ),
        control=control,
        aeris=aeris,
        delta_recovery_rate=delta,
        rows=rows,
    )
