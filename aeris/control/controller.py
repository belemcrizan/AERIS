"""ATCController: choose CONTINUE / RETRY / HOLD / REROUTE / ESCALATE / ABORT.

The controller does not execute routes. It only returns a ControlDecision.
V0 is fully deterministic given (state, hazards, routes, policy).
"""

from __future__ import annotations

from aeris.control.safety import assess_action
from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import (
    ControlAction,
    FailureReason,
    HazardSeverity,
    HazardType,
    InterventionMode,
)
from aeris.core.errors import NoRouteAvailable
from aeris.core.models import ControlDecision, Flight, Hazard, ScoredRoute, make_decision
from aeris.policies.thresholds import ThresholdPolicy
from aeris.routing.planner import RoutePlanner

_ACTION_PRIORITY = {
    ControlAction.ABORT: 0,
    ControlAction.ESCALATE_HUMAN: 1,
    ControlAction.COMPENSATE: 2,
    ControlAction.REROUTE: 3,
    ControlAction.RETRY: 4,
    ControlAction.HOLD: 5,
    ControlAction.CONTINUE: 6,
    ControlAction.APPROVE_COMPENSATION: 1,
    ControlAction.DENY_COMPENSATION: 1,
}

_SEVERITY_RANK = {
    HazardSeverity.INFO: 0,
    HazardSeverity.CAUTION: 1,
    HazardSeverity.WARNING: 2,
    HazardSeverity.CRITICAL: 3,
}


class ATCController:
    def __init__(
        self,
        planner: RoutePlanner | None = None,
        policy: ThresholdPolicy | None = None,
        clock: Clock | None = None,
        mode: InterventionMode = InterventionMode.AERIS,
    ) -> None:
        self.planner = planner or RoutePlanner()
        self.policy = policy or ThresholdPolicy()
        self._clock = clock or SystemClock()
        self.mode = mode

    def decide(
        self,
        flight: Flight,
        hazards: list[Hazard],
        *,
        alternative: ScoredRoute | None = None,
    ) -> ControlDecision:
        decision = self._choose(flight, hazards, alternative=alternative)
        if self.mode == InterventionMode.CONTROL:
            return decision
        return self._finalize(flight, decision)

    def _choose(
        self,
        flight: Flight,
        hazards: list[Hazard],
        *,
        alternative: ScoredRoute | None = None,
    ) -> ControlDecision:
        now = self._clock.now()
        current_route = flight.current_route_id

        if self.mode == InterventionMode.CONTROL:
            return make_decision(
                flight.flight_id,
                ControlAction.CONTINUE,
                "CONTROL arm: observation only, no ATC intervention",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence={"hazards": [h.type.value for h in hazards], "mode": self.mode.value},
            )

        if not hazards:
            return make_decision(
                flight.flight_id,
                ControlAction.CONTINUE,
                "no hazards detected; remaining on current airway",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence={"state": flight.state.value},
            )

        worst = self._worst(hazards)
        asserted = self._dominant_action(hazards)
        alternative = alternative or self.planner.next_route(flight, hazards)

        if flight.route_changes >= self.policy.max_route_changes and asserted == ControlAction.REROUTE:
            asserted = ControlAction.ESCALATE_HUMAN if self.policy.human_on_critical else ControlAction.ABORT

        if worst.severity == HazardSeverity.CRITICAL and self.policy.human_on_critical:
            return make_decision(
                flight.flight_id,
                ControlAction.ESCALATE_HUMAN,
                f"CRITICAL hazard {worst.type.value} requires human ATC",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence=self._evidence(hazards, alternative),
            )

        if asserted == ControlAction.ABORT:
            return make_decision(
                flight.flight_id,
                ControlAction.ABORT,
                f"aborting after {worst.type.value}",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence=self._evidence(hazards, alternative),
            )

        if asserted == ControlAction.ESCALATE_HUMAN or (
            asserted == ControlAction.REROUTE and alternative is not None and alternative.route.is_human
        ):
            return make_decision(
                flight.flight_id,
                ControlAction.ESCALATE_HUMAN,
                "escalating to human controller",
                now,
                previous_route=current_route,
                new_route=alternative.route.route_id if alternative else current_route,
                evidence=self._evidence(hazards, alternative),
            )

        if asserted == ControlAction.REROUTE:
            if alternative is None:
                if self.policy.human_on_critical:
                    return make_decision(
                        flight.flight_id,
                        ControlAction.ESCALATE_HUMAN,
                        "no alternative machine route; escalating",
                        now,
                        previous_route=current_route,
                        new_route=current_route,
                        evidence=self._evidence(hazards, None),
                    )
                return make_decision(
                    flight.flight_id,
                    ControlAction.ABORT,
                    "no alternative route remains; aborting",
                    now,
                    previous_route=current_route,
                    new_route=current_route,
                    evidence=self._evidence(hazards, None),
                )
            return make_decision(
                flight.flight_id,
                ControlAction.REROUTE,
                f"diverting {current_route} -> {alternative.route.route_id}: {'; '.join(alternative.reasons)}",
                now,
                previous_route=current_route,
                new_route=alternative.route.route_id,
                evidence=self._evidence(hazards, alternative),
            )

        if asserted == ControlAction.RETRY:
            if flight.consecutive_retries >= self.policy.max_retries:
                if alternative is not None:
                    return make_decision(
                        flight.flight_id,
                        ControlAction.REROUTE,
                        "retries exhausted; diverting",
                        now,
                        previous_route=current_route,
                        new_route=alternative.route.route_id,
                        evidence=self._evidence(hazards, alternative),
                    )
                return make_decision(
                    flight.flight_id,
                    ControlAction.ABORT,
                    "retries exhausted and no diversion available",
                    now,
                    previous_route=current_route,
                    new_route=current_route,
                    evidence=self._evidence(hazards, None),
                )
            return make_decision(
                flight.flight_id,
                ControlAction.RETRY,
                f"retrying waypoint after {worst.type.value}",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence=self._evidence(hazards, alternative),
            )

        if asserted == ControlAction.HOLD:
            if flight.hold_count >= self.policy.max_holds:
                return make_decision(
                    flight.flight_id,
                    ControlAction.RETRY if flight.consecutive_retries < self.policy.max_retries else ControlAction.REROUTE,
                    "holding pattern exhausted",
                    now,
                    previous_route=current_route,
                    new_route=alternative.route.route_id if alternative else current_route,
                    evidence=self._evidence(hazards, alternative),
                )
            return make_decision(
                flight.flight_id,
                ControlAction.HOLD,
                f"holding after {worst.type.value}",
                now,
                previous_route=current_route,
                new_route=current_route,
                evidence=self._evidence(hazards, alternative),
            )

        return make_decision(
            flight.flight_id,
            ControlAction.CONTINUE,
            "hazards below intervention threshold; continue",
            now,
            previous_route=current_route,
            new_route=current_route,
            evidence=self._evidence(hazards, alternative),
        )

    def _dominant_action(self, hazards: list[Hazard]) -> ControlAction:
        return min(
            (hazard.recommended_action for hazard in hazards),
            key=lambda action: _ACTION_PRIORITY[action],
        )

    def _worst(self, hazards: list[Hazard]) -> Hazard:
        return max(hazards, key=lambda hazard: _SEVERITY_RANK[hazard.severity])

    def _evidence(self, hazards: list[Hazard], alternative: ScoredRoute | None) -> dict:
        payload: dict = {
            "hazards": [
                {
                    "type": hazard.type.value,
                    "severity": hazard.severity.value,
                    "recommended_action": hazard.recommended_action.value,
                    "evidence": hazard.evidence,
                }
                for hazard in hazards
            ]
        }
        if alternative is not None:
            payload["selected_route"] = {
                "route_id": alternative.route.route_id,
                "score": alternative.score,
                "reasons": alternative.reasons,
            }
        return payload

    def _finalize(self, flight: Flight, decision: ControlDecision) -> ControlDecision:
        decision = self._guard_side_effects(flight, decision)
        return self._guard_budget(flight, decision)

    def _guard_side_effects(self, flight: Flight, decision: ControlDecision) -> ControlDecision:
        if decision.action not in {ControlAction.RETRY, ControlAction.REROUTE}:
            return decision
        try:
            waypoint = flight.current_waypoint()
        except NoRouteAvailable:
            waypoint = None
        assessment = assess_action(flight, decision.action, waypoint)
        if assessment.allowed:
            return decision
        if assessment.compensation_required and assessment.follow_up is not None:
            return make_decision(
                flight.flight_id,
                ControlAction.COMPENSATE,
                assessment.detail,
                decision.timestamp,
                previous_route=decision.previous_route,
                new_route=decision.new_route,
                evidence={**decision.evidence, "safety": assessment.detail},
                compensation_required=True,
                follow_up=assessment.follow_up,
            )
        action = (
            ControlAction.ESCALATE_HUMAN
            if self.policy.escalate_irreversible or self.policy.human_on_critical
            else ControlAction.ABORT
        )
        evidence = {
            **decision.evidence,
            "side_effect_risk": {
                "type": HazardType.SIDE_EFFECT_RISK.value,
                "blocked_reason": assessment.blocked_reason,
                "failure_reason": assessment.failure_reason.value if assessment.failure_reason else None,
            },
        }
        return make_decision(
            flight.flight_id,
            action,
            assessment.detail or "side-effect policy blocked the intervention",
            decision.timestamp,
            previous_route=decision.previous_route,
            new_route=decision.previous_route,
            evidence=evidence,
            blocked_reason=assessment.blocked_reason,
        )

    def _guard_budget(self, flight: Flight, decision: ControlDecision) -> ControlDecision:
        route_id = decision.new_route
        if decision.action is ControlAction.COMPENSATE and decision.follow_up is ControlAction.REROUTE:
            route_id = decision.new_route
        elif decision.action is not ControlAction.REROUTE:
            return decision
        reason = self._budget_block_reason(flight, route_id)
        if reason is None:
            return decision
        action = ControlAction.ESCALATE_HUMAN if self.policy.human_on_critical else ControlAction.ABORT
        return make_decision(
            flight.flight_id,
            action,
            reason,
            decision.timestamp,
            previous_route=decision.previous_route,
            new_route=decision.previous_route,
            evidence={**decision.evidence, "failure_reason": FailureReason.BUDGET_EXHAUSTION.value},
            blocked_reason=reason,
        )

    def _budget_block_reason(self, flight: Flight, route_id: str | None) -> str | None:
        if flight.plan is None or route_id is None or route_id == flight.current_route_id:
            return None
        try:
            route = flight.plan.route_by_id(route_id)
        except KeyError:
            return None
        projected_cost = flight.accumulated_cost + route.estimated_cost
        if projected_cost > self.policy.max_cost:
            return (
                f"reroute would exceed remaining budget "
                f"({projected_cost:.2f} > {self.policy.max_cost:.2f})"
            )
        projected_time = flight.execution_time_ms + route.estimated_latency_ms
        if projected_time > self.policy.max_execution_time_ms:
            return (
                f"reroute would exceed remaining time budget "
                f"({projected_time:.0f}ms > {self.policy.max_execution_time_ms:.0f}ms)"
            )
        return None
