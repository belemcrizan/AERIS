from aeris.core.enums import EventType, ExecutionState, HazardType, InterventionMode
from aeris.evaluation.harness import run_experiment, run_one
from aeris.evaluation.metrics import FlightMetrics, summarize
from aeris.policies.thresholds import ThresholdPolicy
from aeris.simulation.scenarios import get_scenario
from tests.conftest import run_scenario


async def test_fault_injection_is_timestamped_and_mttd_is_the_step_latency():
    director, flight = await run_scenario("latency_reroute")
    timeline = await director.recorder.timeline(flight.flight_id)
    faults = [event for event in timeline if event.event_type is EventType.FAULT_INJECTED]
    hazards = [event for event in timeline if event.event_type is EventType.HAZARD]
    assert faults
    assert hazards
    delta_ms = (hazards[0].timestamp - faults[0].timestamp).total_seconds() * 1000
    assert delta_ms == 5000


async def test_false_low_confidence_makes_aeris_worse_than_control():
    _, control = await run_scenario("false_low_confidence", mode=InterventionMode.CONTROL)
    _, aeris = await run_scenario("false_low_confidence", mode=InterventionMode.AERIS)
    assert control.state is ExecutionState.COMPLETED
    assert aeris.state is not ExecutionState.COMPLETED
    assert aeris.route_changes >= 1


async def test_false_high_confidence_does_not_hide_a_tool_failure():
    director, flight = await run_scenario("false_high_confidence")
    timeline = await director.recorder.timeline(flight.flight_id)
    types = [event.payload["type"] for event in timeline if event.event_type is EventType.HAZARD]
    assert HazardType.TOOL_FAILURE.value in types


async def test_detector_counts_and_undefined_rates():
    row = FlightMetrics(
        scenario_id="synthetic",
        mode="AERIS",
        flight_id="flt",
        success=True,
        terminal_state=ExecutionState.COMPLETED,
        latency_ms=1,
        retries=0,
        route_changes=0,
        human_interventions=0,
        hazard_count=0,
        injected_failure=False,
        recovered=False,
        true_positives=0,
        false_positives=0,
        true_negatives=1,
        false_negatives=0,
    )
    summary = summarize([row])
    assert summary.detector_precision is None
    assert summary.detector_recall is None
    assert summary.true_negative_count == 1
    assert summary.compensation_success_rate is None


async def test_experiment_reports_detection_and_time_metrics():
    report = await run_experiment(["latency_reroute", "happy_path"], repeats=1)
    assert report.aeris.mean_time_to_detect == 5000
    assert report.control.mean_time_to_intervention is None
    assert report.aeris.mean_time_to_intervention is not None
    assert report.statistics_note
    happy = next(row for row in report.rows if row.scenario_id == "happy_path" and row.mode == "AERIS")
    assert happy.true_negatives == 1
    assert happy.interventions == 0
    latency = next(
        row for row in report.rows if row.scenario_id == "latency_reroute" and row.mode == "AERIS"
    )
    assert latency.true_positives >= 1
    assert latency.mttd_ms == 5000


async def test_budget_blocks_a_reroute():
    scenario = get_scenario("persistent_tool_failure")
    policy = ThresholdPolicy(human_on_critical=False, hold_ms=0, max_cost=1.5, escalate_irreversible=False)
    row = await run_one(scenario, InterventionMode.AERIS, policy)
    assert row.success is False
    assert row.failure_reason == "budget_exhaustion"
    assert row.route_changes == 0


async def test_control_and_aeris_share_the_fault():
    control = await run_one(get_scenario("persistent_tool_failure"), InterventionMode.CONTROL)
    aeris = await run_one(get_scenario("persistent_tool_failure"), InterventionMode.AERIS)
    assert control.injected_failure is True
    assert aeris.injected_failure is True
    assert control.success is False
    assert aeris.success is True
    assert control.mttd_ms == aeris.mttd_ms
