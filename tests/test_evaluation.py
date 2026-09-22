from aeris.core.enums import InterventionMode
from aeris.evaluation.harness import run_experiment


async def test_experiment_is_falsifiable_and_reports_both_arms():
    report = await run_experiment(
        ["happy_path", "persistent_tool_failure", "all_routes_fail"],
        repeats=1,
    )
    assert report.hypothesis
    assert report.control.n == 3
    assert report.aeris.n == 3
    control_persistent = next(
        row
        for row in report.rows
        if row.scenario_id == "persistent_tool_failure" and row.mode == InterventionMode.CONTROL.value
    )
    aeris_persistent = next(
        row
        for row in report.rows
        if row.scenario_id == "persistent_tool_failure" and row.mode == InterventionMode.AERIS.value
    )
    assert control_persistent.success is False
    assert aeris_persistent.success is True
    both_fail = [
        row for row in report.rows if row.scenario_id == "all_routes_fail"
    ]
    assert all(row.success is False for row in both_fail)
    assert report.delta_recovery_rate is not None
