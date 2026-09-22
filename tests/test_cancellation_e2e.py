"""Cancellation from operator to recorder, including races.

AERIS must never report CANCELLED for work the runtime actually finished.
"""

from __future__ import annotations

from support_helpers import GatedModel, HookedToolbox, SupportRig

from aeris.core.enums import CancelOutcome, CancelStatus, EventType, ExecutionState, OperatorRole

ADMIN = OperatorRole.ADMIN


async def _resolution(rig: SupportRig) -> dict:
    resolved = await rig.events(EventType.CANCEL_RESOLVED)
    assert len(resolved) == 1
    return resolved[0].payload


async def test_cancel_before_start():
    rig = SupportRig()
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN)
    await rig.run()
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_BEFORE_START
    assert rig.toolbox.calls == []
    assert (await _resolution(rig))["status"] == CancelStatus.CANCELLED.value


async def test_cancel_during_waypoint_stops_at_the_checkpoint():
    model = GatedModel(pause_on={1})
    rig = SupportRig(model=model)
    task = rig.start()
    await model.reached.wait()
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN, reason="stop now")
    assert rig.flight.cancel_status is CancelStatus.CANCEL_ACKNOWLEDGED
    model.release.set()
    await task
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_DURING_WAYPOINT
    assert rig.flight.cancel_status is CancelStatus.CANCELLED
    requested = await rig.events(EventType.CANCEL_REQUESTED)
    assert len(requested) == 1 and requested[0].payload["active_waypoint_id"]


async def test_unsupported_cancel_is_reported_truthfully():
    model = GatedModel(pause_on={1})
    rig = SupportRig(model=model, can_cancel=False)
    task = rig.start()
    await model.reached.wait()
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN)
    assert rig.flight.cancel_status is CancelStatus.CANCEL_UNSUPPORTED
    model.release.set()
    await task
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_NOT_SUPPORTED
    assert rig.flight.cancel_status is CancelStatus.CANCEL_UNSUPPORTED
    completed = await rig.events(EventType.WAYPOINT_COMPLETED)
    assert completed and completed[0].payload.get("under_cancel") is True
    assert rig.flight.state is ExecutionState.ABORTED


async def test_cancel_after_side_effect_commit_is_too_late_not_cancelled():
    rig_holder: dict = {}

    async def cancel_mid_write(call, result):
        await rig_holder["rig"].director.cancel(call.flight_id, role=ADMIN, reason="stop the credit")

    toolbox = HookedToolbox(tool="apply_service_credit", hook=cancel_mid_write)
    rig = SupportRig(toolbox=toolbox)
    rig_holder["rig"] = rig
    await rig.run()
    assert len(toolbox.ledger.entries) == 1
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_AFTER_SIDE_EFFECT_COMMIT
    assert rig.flight.cancel_status is CancelStatus.CANCEL_TOO_LATE
    assert rig.flight.state is ExecutionState.ABORTED
    detail = (await _resolution(rig))["detail"]
    assert "not undone" in detail


async def test_cancel_racing_the_final_step_reports_completion():
    model = GatedModel(pause_on={6})
    rig = SupportRig(model=model)
    task = rig.start()
    await model.reached.wait()
    assert rig.flight.current_waypoint().name == "respond"
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN)
    model.release.set()
    await task
    assert rig.flight.state is ExecutionState.COMPLETED
    assert rig.flight.cancel_status is CancelStatus.CANCEL_TOO_LATE
    assert rig.flight.cancel_status is not CancelStatus.CANCELLED


async def test_cancel_after_terminal_and_repeated_cancel_are_idempotent():
    rig = SupportRig()
    await rig.run()
    assert rig.flight.state is ExecutionState.COMPLETED
    for _ in range(3):
        await rig.director.cancel(rig.flight.flight_id, role=ADMIN)
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_AFTER_TERMINAL
    assert rig.flight.state is ExecutionState.COMPLETED
    assert len(await rig.events(EventType.CANCEL_REQUESTED)) == 1
    assert len(await rig.events(EventType.CANCEL_RESOLVED)) == 1


async def test_cancel_on_waiting_flight_aborts_it():
    from aeris.experiments.support import experiment_policy

    rig = SupportRig(scenario="status_timeout", policy=experiment_policy(human_on_critical=True))
    await rig.run()
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN)
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.cancel_outcome is CancelOutcome.CANCEL_BETWEEN_WAYPOINTS
