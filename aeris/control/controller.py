"""ATCController: choose CONTINUE / RETRY / HOLD / REROUTE / ESCALATE / ABORT.

The controller does not execute routes. It only returns a ControlDecision.
V0 is fully deterministic given (state, hazards, routes, policy).
"""

from __future__ import annotations

from aeris.core.clock import Clock, SystemClock
from aeris.core.enums import ControlAction, HazardSeverity, InterventionMode
from aeris.core.models import ControlDecision, Flight, Hazard, ScoredRoute, make_decision
from aeris.policies.thresholds import ThresholdPolicy
from aeris.routing.planner import RoutePlanner


_ACTION_PRIORITY = {
    ControlAction.ABORT: 0,
    ControlAction.ESCALATE_HUMAN: 1,
    ControlAction.REROUTE: 2,
    ControlAction.RETRY: 3,
    ControlAction.HOLD: 4,
    ControlAction.CONTINUE: 5,
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
