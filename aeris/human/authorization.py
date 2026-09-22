"""Minimal operator roles. This is a domain check, not OAuth.

OBSERVER can read. CONTROLLER can steer a flight when the action is safe.
ADMIN can additionally abort or cancel. A later auth system can replace
``authorize`` without changing the flight loop.

Absence of authorization data never increases privilege: a missing or
unknown role is rejected, it is not mapped to a default.
"""

from __future__ import annotations

from typing import Any

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

ADMIN_ONLY: frozenset[ControlAction] = _ADMIN - _CONTROLLER


def resolve_role(raw: Any) -> OperatorRole:
    """Turn caller input into a role, or refuse. Never guesses upward."""

    if raw is None or raw == "":
        raise UnauthorizedIntervention("operator role is required")
    if isinstance(raw, OperatorRole):
        return raw
    try:
        return OperatorRole(str(raw).upper())
    except ValueError as exc:
        raise UnauthorizedIntervention(f"unknown operator role {raw!r}") from exc


def authorize(role: OperatorRole | None, action: ControlAction) -> None:
    resolved = resolve_role(role)
    allowed = PERMISSIONS.get(resolved, _OBSERVER)
    if action not in allowed:
        raise UnauthorizedIntervention(
            f"role {resolved.value} cannot apply {action.value}"
        )


def allowed_actions(role: OperatorRole) -> list[ControlAction]:
    return sorted(PERMISSIONS.get(role, _OBSERVER), key=lambda action: action.value)
