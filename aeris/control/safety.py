"""Side-effect guard used by ATC before a retry or reroute is issued.

Compensation is an operation the flight director performs. It is not a
flight state. V0 compensation is synchronous and always happens inside
the intervention that required it, so a COMPENSATING state would add
transitions without changing who is allowed to decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeris.core.enums import ControlAction, FailureReason, SideEffectClass
from aeris.core.models import CommittedEffect, Flight, Waypoint


@dataclass(frozen=True)
class SafetyAssessment:
    allowed: bool
    compensation_required: bool = False
    follow_up: ControlAction | None = None
    blocked_reason: str | None = None
    failure_reason: FailureReason | None = None
    detail: str = ""


def _reversible(effects: list[CommittedEffect]) -> list[CommittedEffect]:
    return [effect for effect in effects if effect.side_effect is SideEffectClass.REVERSIBLE_WRITE]


def _irreversible(effects: list[CommittedEffect]) -> list[CommittedEffect]:
    return [effect for effect in effects if effect.side_effect is SideEffectClass.IRREVERSIBLE_WRITE]


def assess_action(
    flight: Flight,
    action: ControlAction,
    waypoint: Waypoint | None,
) -> SafetyAssessment:
    """Decide whether ``action`` is safe given committed side effects."""

    if action in {
        ControlAction.CONTINUE,
        ControlAction.HOLD,
        ControlAction.ABORT,
        ControlAction.ESCALATE_HUMAN,
        ControlAction.APPROVE_COMPENSATION,
        ControlAction.DENY_COMPENSATION,
        ControlAction.COMPENSATE,
    }:
        return SafetyAssessment(allowed=True)

    route_id = flight.current_route_id
    open_effects = flight.open_effects(route_id)
    current_committed = _current_committed(open_effects, waypoint)

    if action is ControlAction.RETRY:
        return _assess_retry(flight, waypoint, current_committed)
    if action is ControlAction.REROUTE:
        return _assess_reroute(flight, open_effects)
    return SafetyAssessment(allowed=True)


def _current_committed(
    open_effects: list[CommittedEffect],
    waypoint: Waypoint | None,
) -> CommittedEffect | None:
    if waypoint is None:
        return None
    for effect in open_effects:
        if effect.waypoint_id == waypoint.waypoint_id:
            return effect
    return None


def _assess_retry(
    flight: Flight,
    waypoint: Waypoint | None,
    committed: CommittedEffect | None,
) -> SafetyAssessment:
    if not flight.runtime_can_retry:
        return SafetyAssessment(
            allowed=False,
            blocked_reason="runtime cannot retry",
            failure_reason=FailureReason.UNSAFE_RETRY_BLOCKED,
            detail="runtime capability CAN_RETRY is absent",
        )
    if waypoint is None or committed is None:
        return SafetyAssessment(allowed=True)
    kind = committed.side_effect
    if kind is SideEffectClass.READ_ONLY:
        return SafetyAssessment(allowed=True)
    if kind is SideEffectClass.IDEMPOTENT_WRITE:
        if (
            waypoint.supports_idempotency
            and waypoint.idempotency_key
            and flight.runtime_supports_idempotency
        ):
            return SafetyAssessment(allowed=True, detail="retry reuses idempotency key")
        return SafetyAssessment(
            allowed=False,
            blocked_reason="idempotent write has no idempotency key",
            failure_reason=FailureReason.UNSAFE_RETRY_BLOCKED,
            detail="refusing automatic retry without an idempotency key",
        )
    if kind is SideEffectClass.REVERSIBLE_WRITE:
        if flight.runtime_can_compensate and waypoint.compensation_name:
            return SafetyAssessment(
                allowed=False,
                compensation_required=True,
                follow_up=ControlAction.RETRY,
                detail="compensate the reversible write before retry",
            )
        return SafetyAssessment(
            allowed=False,
            blocked_reason="reversible write cannot be compensated",
            failure_reason=FailureReason.UNSAFE_RETRY_BLOCKED,
            detail="retry would repeat a committed reversible write",
        )
    return SafetyAssessment(
        allowed=False,
        blocked_reason="irreversible write already committed",
        failure_reason=FailureReason.UNSAFE_RETRY_BLOCKED,
        detail="automatic retry of an irreversible write is prohibited",
    )


def _assess_reroute(flight: Flight, open_effects: list[CommittedEffect]) -> SafetyAssessment:
    irreversible = _irreversible(open_effects)
    if irreversible:
        return SafetyAssessment(
            allowed=False,
            blocked_reason="irreversible side effect is committed on this route",
            failure_reason=FailureReason.UNSAFE_REROUTE_BLOCKED,
            detail="automatic reroute would abandon an irreversible write",
        )
    reversible = _reversible(open_effects)
    if reversible:
        if flight.runtime_can_compensate and all(effect.compensation_name for effect in reversible):
            return SafetyAssessment(
                allowed=False,
                compensation_required=True,
                follow_up=ControlAction.REROUTE,
                detail="compensate reversible writes before reroute",
            )
        return SafetyAssessment(
            allowed=False,
            blocked_reason="reversible write cannot be compensated",
            failure_reason=FailureReason.UNSAFE_REROUTE_BLOCKED,
            detail="reroute requires compensation that the runtime cannot perform",
        )
    return SafetyAssessment(allowed=True)
