from pathlib import Path

from fastapi.testclient import TestClient

from aeris.api.app import create_app
from aeris.config import Settings
from aeris.core.enums import ControlAction, OperatorRole
from aeris.core.errors import UnauthorizedIntervention, UnsafeRetry
from aeris.human.authorization import authorize
from tests.conftest import run_scenario


def test_observer_cannot_abort_and_controller_cannot_override():
    try:
        authorize(OperatorRole.OBSERVER, ControlAction.ABORT)
        raise AssertionError("observer must not abort")
    except UnauthorizedIntervention:
        pass
    try:
        authorize(OperatorRole.CONTROLLER, ControlAction.ABORT)
        raise AssertionError("controller must not abort")
    except UnauthorizedIntervention:
        pass
    authorize(OperatorRole.ADMIN, ControlAction.ABORT)
    authorize(OperatorRole.CONTROLLER, ControlAction.HOLD)


async def test_human_request_has_context_and_unsafe_retry_is_rejected():
    director, flight = await run_scenario("unsafe_irreversible_retry")
    request = director.human_requests[flight.flight_id]
    assert request.mission_id == flight.mission_id
    assert request.hazards
    assert request.recent_telemetry
    assert request.alternatives
    assert request.recommended_action is ControlAction.ESCALATE_HUMAN
    try:
        await director.apply_human_action(
            flight.flight_id,
            ControlAction.RETRY,
            reason="try again",
            role=OperatorRole.ADMIN,
        )
        raise AssertionError("irreversible retry must be rejected")
    except UnsafeRetry:
        pass


def test_api_rejects_observer_abort_and_exposes_integrity(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "aeris.db", otel_enabled=False, hold_ms=0, human_on_critical=True)
    with TestClient(create_app(settings)) as client:
        flight = client.post("/flights", json={"scenario_id": "timeout_critical", "human_on_critical": True})
        flight_id = flight.json()["flight_id"]
        denied = client.post(
            f"/flights/{flight_id}/control/abort",
            json={"reason": "no", "role": "OBSERVER"},
        )
        assert denied.status_code == 403
        integrity = client.get(f"/flights/{flight_id}/integrity")
        assert integrity.status_code == 200
        assert integrity.json()["ok"] is True
        request = client.get(f"/flights/{flight_id}/human-request")
        assert request.status_code == 200
        assert request.json()["flight_id"] == flight_id
        decisions = client.get(f"/flights/{flight_id}/decisions")
        assert decisions.status_code == 200
        assert decisions.json()["events"]
