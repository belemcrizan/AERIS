"""The console shows only actions the domain allows, and explains the rest."""

from __future__ import annotations

from fastapi.testclient import TestClient
from support_helpers import SupportRig

from aeris.api.app import create_app
from aeris.config import Settings
from aeris.core.enums import ControlAction, ExecutionState
from aeris.experiments.support import experiment_policy
from aeris.human.console import console_view


def _actions(view) -> dict[ControlAction, object]:
    return {item.action: item for item in view.actions}


async def test_retry_is_disabled_and_explained_after_an_irreversible_commit():
    rig = SupportRig(
        scenario="credit_lost_response_unkeyed",
        policy=experiment_policy(human_on_critical=True, escalate_irreversible=True),
    )
    await rig.run()
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
    view = console_view(rig.director, rig.flight)
    actions = _actions(view)
    assert not actions[ControlAction.RETRY].enabled
    assert actions[ControlAction.RETRY].reason == "Retry unavailable: an irreversible write has already committed."
    assert actions[ControlAction.ABORT].enabled
    assert view.side_effects and view.current_waypoint["name"] == "apply_credit"


async def test_waiting_flight_view_carries_hazards_alternatives_and_scores():
    rig = SupportRig(scenario="status_timeout", policy=experiment_policy(human_on_critical=True))
    await rig.run()
    view = console_view(rig.director, rig.flight)
    actions = _actions(view)
    assert actions[ControlAction.RETRY].enabled and actions[ControlAction.REROUTE].enabled
    assert view.hazards and view.recommended_action == ControlAction.ESCALATE_HUMAN.value
    alternative = view.alternatives[0]
    assert alternative["name"] == "bravo" and alternative["diversity"] is not None and "score" in alternative
    assert view.signal_provenance


async def test_terminal_flight_disables_every_action():
    rig = SupportRig()
    await rig.run()
    assert all(not item.enabled for item in console_view(rig.director, rig.flight).actions)


def test_console_page_and_view_are_served(tmp_path):
    settings = Settings(db_path=tmp_path / "aeris.db", otel_enabled=False, hold_ms=0, human_on_critical=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/console")
        assert page.status_code == 200 and "AERIS" in page.text
        flight = client.post("/flights", json={"scenario_id": "timeout_critical"}).json()
        view = client.get(f"/flights/{flight['flight_id']}/console")
        assert view.status_code == 200
        assert {item["action"] for item in view.json()["actions"]} == {"CONTINUE", "HOLD", "RETRY", "REROUTE", "ABORT"}
