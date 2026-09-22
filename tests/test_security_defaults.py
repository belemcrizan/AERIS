"""Absence of authorization data must never increase privilege."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from support_helpers import SupportRig

from aeris.core.enums import ControlAction, ExecutionState, OperatorRole
from aeris.core.errors import UnauthorizedIntervention, UnsafeRetry
from aeris.core.models import HumanIntervention
from aeris.experiments.support import experiment_policy
from aeris.human.authorization import ADMIN_ONLY, authorize, resolve_role


@pytest.mark.parametrize("raw", [None, "", "superuser", "root", 3])
def test_missing_or_unknown_role_is_rejected_not_promoted(raw):
    with pytest.raises(UnauthorizedIntervention):
        resolve_role(raw)


def test_role_parsing_is_explicit_and_case_insensitive():
    assert resolve_role("controller") is OperatorRole.CONTROLLER
    assert resolve_role(OperatorRole.OBSERVER) is OperatorRole.OBSERVER


def test_human_intervention_requires_a_role():
    with pytest.raises(ValidationError):
        HumanIntervention(
            intervention_id="h", flight_id="f", action=ControlAction.RETRY, operator="x", reason="r",
            timestamp="2026-01-01T00:00:00Z",
        )


@pytest.mark.parametrize("action", [ControlAction.RETRY, ControlAction.REROUTE, ControlAction.HOLD, ControlAction.ABORT])
def test_observer_cannot_steer(action):
    with pytest.raises(UnauthorizedIntervention):
        authorize(OperatorRole.OBSERVER, action)


@pytest.mark.parametrize("action", sorted(ADMIN_ONLY, key=lambda item: item.value))
def test_controller_cannot_perform_admin_only_operations(action):
    with pytest.raises(UnauthorizedIntervention):
        authorize(OperatorRole.CONTROLLER, action)


async def _waiting_after_lost_credit() -> SupportRig:
    rig = SupportRig(
        scenario="credit_lost_response_unkeyed",
        policy=experiment_policy(human_on_critical=True, escalate_irreversible=True),
    )
    await rig.run()
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
    return rig


async def test_omitted_role_changes_nothing_on_a_waiting_flight():
    rig = await _waiting_after_lost_credit()
    version = rig.flight.control_version
    with pytest.raises(UnauthorizedIntervention):
        await rig.director.apply_human_action(
            rig.flight.flight_id, ControlAction.ABORT, reason="no role", role=None
        )
    with pytest.raises(UnauthorizedIntervention):
        await rig.director.cancel(rig.flight.flight_id, role=None)
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
    assert rig.flight.control_version == version
    assert rig.flight.cancel_requested is False


async def test_admin_cannot_override_an_irreversible_commit():
    rig = await _waiting_after_lost_credit()
    assert len(rig.toolbox.ledger.entries) == 1
    with pytest.raises(UnsafeRetry):
        await rig.director.apply_human_action(
            rig.flight.flight_id, ControlAction.RETRY, reason="force it", role=OperatorRole.ADMIN
        )
    assert len(rig.toolbox.ledger.entries) == 1
    assert rig.flight.state is ExecutionState.WAITING_HUMAN
