"""Minimal operator roles. This is a domain check, not OAuth.

OBSERVER can read. CONTROLLER can steer a flight when the action is safe.
ADMIN can abort and approve irreversible overrides. A later auth system
can replace ``authorize`` without changing the flight loop.
"""

from __future__ import annotations

from aeris.core.enums import ControlAction, OperatorRole
from aeris.core.errors import UnauthorizedIntervention

_OBSERVER: frozenset[ControlAction] = frozenset()
_CONTROLLER: frozenset[ControlAction] = frozenset(
    {
        ControlAction.CONTINUE,
        ControlAction.HOLD,
        ControlAction.RETRY,
        ControlAction.REROUTE,
        ControlAction.APPROVE_COMPENSATION,
        ControlAction.DENY_COMPENSATION,
    }
)
_ADMIN: frozenset[ControlAction] = _CONTROLLER | frozenset(
    {
        ControlAction.ABORT,
        ControlAction.COMPENSATE,
        ControlAction.ESCALATE_HUMAN,
    }
)

PERMISSIONS: dict[OperatorRole, frozenset[ControlAction]] = {
    OperatorRole.OBSERVER: _OBSERVER,
    OperatorRole.CONTROLLER: _CONTROLLER,
    OperatorRole.ADMIN: _ADMIN,
}


def authorize(role: OperatorRole, action: ControlAction) -> None:
    allowed = PERMISSIONS.get(role, _OBSERVER)
    if action not in allowed:
        raise UnauthorizedIntervention(
            f"role {role.value} cannot apply {action.value}"
        )


def allowed_actions(role: OperatorRole) -> list[ControlAction]:
    return list(PERMISSIONS.get(role, _OBSERVER))
