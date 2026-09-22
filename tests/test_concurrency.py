"""One logical decision must not produce multiple physical side effects.

Concurrent human commands are bound to the ``control_version`` the caller
saw. The per-flight lock serializes them; the version check rejects the
losers with ControlConflict.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from support_helpers import GatedModel, SupportRig

from aeris.api.app import create_app
from aeris.config import Settings
from aeris.core.enums import ControlAction, EventType, ExecutionState, OperatorRole
from aeris.core.errors import ControlConflict
from aeris.experiments.support import experiment_policy
from aeris.policies.thresholds import ThresholdPolicy
from tests.conftest import run_scenario

ADMIN = OperatorRole.ADMIN


async def _waiting_timeout_flight() -> SupportRig:
    rig = SupportRig(scenario="status_timeout", policy=experiment_policy(human_on_critical=True))
    await rig.run()
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
    return rig


def _conflicts(results) -> list[BaseException]:
    return [item for item in results if isinstance(item, ControlConflict)]


async def _both(rig: SupportRig, first, second):
    version = rig.flight.control_version
    return await asyncio.gather(
        rig.director.apply_human_action(rig.flight.flight_id, expected_version=version, **first),
        rig.director.apply_human_action(rig.flight.flight_id, expected_version=version, **second),
        return_exceptions=True,
    )


async def test_two_retries_apply_once():
    rig = await _waiting_timeout_flight()
    results = await _both(
        rig,
        {"action": ControlAction.RETRY, "reason": "a", "role": ADMIN},
        {"action": ControlAction.RETRY, "reason": "b", "role": ADMIN},
    )
    assert len(_conflicts(results)) == 1
    assert rig.flight.retry_count == 1
    assert len(await rig.events(EventType.HUMAN_INTERVENTION)) == 1


async def test_abort_vs_retry_one_wins():
    rig = await _waiting_timeout_flight()
    results = await _both(
        rig,
        {"action": ControlAction.ABORT, "reason": "stop", "role": ADMIN},
        {"action": ControlAction.RETRY, "reason": "again", "role": ADMIN},
    )
    assert len(_conflicts(results)) == 1
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.retry_count == 0


async def test_abort_vs_reroute_one_wins():
    rig = await _waiting_timeout_flight()
    results = await _both(
        rig,
        {"action": ControlAction.ABORT, "reason": "stop", "role": ADMIN},
        {"action": ControlAction.REROUTE, "reason": "divert", "role": ADMIN},
    )
    assert len(_conflicts(results)) == 1
    assert rig.flight.route_changes == 0
    assert rig.flight.state is ExecutionState.ABORTED


async def test_two_reroutes_divert_once():
    rig = await _waiting_timeout_flight()
    results = await _both(
        rig,
        {"action": ControlAction.REROUTE, "reason": "a", "role": ADMIN},
        {"action": ControlAction.REROUTE, "reason": "b", "role": ADMIN},
    )
    assert len(_conflicts(results)) == 1
    assert rig.flight.route_changes == 1
    assert rig.flight.state is ExecutionState.COMPLETED
    assert len(rig.toolbox.ledger.entries) == 1


async def test_two_compensation_approvals_compensate_once():
    director, flight = await run_scenario(
        "compensation_success", policy=ThresholdPolicy(human_on_critical=True, hold_ms=0)
    )
    assert flight.state is ExecutionState.WAITING_HUMAN
    version = flight.control_version
    results = await asyncio.gather(
        *[
            director.apply_human_action(
                flight.flight_id,
                ControlAction.APPROVE_COMPENSATION,
                reason=f"approve {index}",
                role=OperatorRole.CONTROLLER,
                expected_version=version,
            )
            for index in range(2)
        ],
        return_exceptions=True,
    )
    assert len(_conflicts(results)) == 1
    timeline = await director.recorder.timeline(flight.flight_id)
    assert sum(1 for event in timeline if event.event_type is EventType.COMPENSATION) == 1


async def test_human_action_while_atc_is_flying_is_rejected():
    model = GatedModel(pause_on={1})
    rig = SupportRig(model=model)
    task = rig.start()
    await model.reached.wait()
    try:
        assert rig.flight.state is ExecutionState.RUNNING
        try:
            await rig.director.apply_human_action(
                rig.flight.flight_id, ControlAction.RETRY, reason="meddle", role=ADMIN
            )
        except ControlConflict as exc:
            assert "WAITING_HUMAN" in str(exc)
        else:
            raise AssertionError("human RETRY on a RUNNING flight must conflict")
    finally:
        model.release.set()
        await task
    assert rig.flight.state is ExecutionState.COMPLETED
    assert rig.flight.retry_count == 0


async def test_stale_version_is_rejected_even_when_alone():
    rig = await _waiting_timeout_flight()
    stale = rig.flight.control_version - 1
    try:
        await rig.director.apply_human_action(
            rig.flight.flight_id, ControlAction.RETRY, reason="late", role=ADMIN, expected_version=stale
        )
    except ControlConflict:
        pass
    else:
        raise AssertionError("stale expected_version must conflict")
    assert rig.flight.retry_count == 0


async def test_cancel_while_a_reroute_is_being_decided_stops_before_the_new_route():
    model = GatedModel(pause_on={2})
    rig = SupportRig(model=model, scenario="status_timeout")
    task = rig.start()
    await model.reached.wait()
    await rig.director.cancel(rig.flight.flight_id, role=ADMIN, reason="operator stop")
    model.release.set()
    await task
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.route_changes == 0
    started = await rig.events(EventType.WAYPOINT_STARTED)
    assert {event.route_id for event in started} == {"route-alpha"}


def test_duplicate_http_control_calls_conflict(tmp_path):
    settings = Settings(db_path=tmp_path / "aeris.db", otel_enabled=False, hold_ms=0, human_on_critical=True)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/flights", json={"scenario_id": "timeout_critical", "human_on_critical": True}
        )
        assert created.status_code == 201, created.text
        flight = created.json()
        assert flight["state"] == ExecutionState.WAITING_HUMAN.value
        body = {"role": "ADMIN", "reason": "go", "expected_version": flight["control_version"]}
        first = client.post(f"/flights/{flight['flight_id']}/control/retry", json=body)
        second = client.post(f"/flights/{flight['flight_id']}/control/retry", json=body)
        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
