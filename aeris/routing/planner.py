"""RoutePlanner scores candidate airways without flying them.

Score is a weighted linear combination. Every decision returns the terms so
a human (or the recorder) can see why a route won.
"""

from __future__ import annotations

from aeris.core.models import Flight, FlightPlan, Hazard, Route, ScoredRoute
from aeris.core.ids import new_id


class RoutePlanner:
    reliability_weight: float = 1.5
    latency_weight: float = 0.5
    cost_weight: float = 0.3
    failure_weight: float = 0.25
    hazard_weight: float = 0.1

    def plan(
        self,
        mission_id: str,
        routes: list[Route],
        *,
        failed_route_ids: list[str] | None = None,
        hazards: list[Hazard] | None = None,
        exclude_human: bool = True,
    ) -> FlightPlan:
        scored = self.score_routes(
            routes,
            failed_route_ids=failed_route_ids,
            hazards=hazards,
            exclude_human=exclude_human,
        )
        if not scored:
            scored = self.score_routes(
                routes,
                failed_route_ids=failed_route_ids,
                hazards=hazards,
                exclude_human=False,
            )
        if not scored:
            raise ValueError("no routes available to plan")
        winner = scored[0]
        return FlightPlan(
            plan_id=new_id("plan"),
            mission_id=mission_id,
            routes=routes,
            selected_route_id=winner.route.route_id,
            rationale=winner.reasons,
        )

    def score_routes(
        self,
        routes: list[Route],
        *,
        failed_route_ids: list[str] | None = None,
        hazards: list[Hazard] | None = None,
        exclude_human: bool = False,
        exclude_route_ids: list[str] | None = None,
    ) -> list[ScoredRoute]:
        failed = set(failed_route_ids or [])
        excluded = set(exclude_route_ids or [])
        hazards = hazards or []
        scored: list[ScoredRoute] = []
        for route in routes:
            if route.route_id in excluded:
                continue
            if exclude_human and route.is_human:
                continue
            scored.append(self.score_route(route, failed=failed, hazards=hazards))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def score_route(
        self,
        route: Route,
        *,
        failed: set[str],
        hazards: list[Hazard],
    ) -> ScoredRoute:
        latency_n = min(route.estimated_latency_ms / 5000.0, 1.0)
        cost_n = min(route.estimated_cost / 10.0, 1.0)
        failures = 1.0 if route.route_id in failed else 0.0
        hazard_penalty = self.hazard_weight * len(hazards)
        criticals = sum(1 for hazard in hazards if hazard.severity.value == "CRITICAL")
        hazard_penalty += 0.3 * criticals
        score = (
            self.reliability_weight * route.estimated_reliability
            - self.latency_weight * latency_n
            - self.cost_weight * cost_n
            - self.failure_weight * failures * 4
            - hazard_penalty
        )
        if route.is_human:
            score -= 0.4
        reasons = [
            f"reliability={route.estimated_reliability:.2f} * {self.reliability_weight}",
            f"latency_norm={latency_n:.2f} * {self.latency_weight}",
            f"cost_norm={cost_n:.2f} * {self.cost_weight}",
            f"previous_failure_penalty={failures}",
            f"hazard_penalty={hazard_penalty:.2f}",
            f"score={score:.3f}",
        ]
        if route.route_id in failed:
            reasons.append("route previously failed on this flight")
        if route.is_human:
            reasons.append("human airway preferred only after machine routes are exhausted")
        return ScoredRoute(route=route, score=score, reasons=reasons)

    def next_route(
        self,
        flight: Flight,
        hazards: list[Hazard],
    ) -> ScoredRoute | None:
        if flight.plan is None:
            return None
        scored = self.score_routes(
            flight.plan.routes,
            failed_route_ids=flight.failed_route_ids,
            hazards=hazards,
            exclude_human=True,
            exclude_route_ids=[flight.current_route_id] if flight.current_route_id else None,
        )
        machine = [item for item in scored if not item.route.is_human and item.route.route_id not in set(flight.failed_route_ids)]
        if machine:
            return machine[0]
        human = self.score_routes(
            flight.plan.routes,
            failed_route_ids=flight.failed_route_ids,
            hazards=hazards,
            exclude_human=False,
        )
        for item in human:
            if item.route.is_human:
                return item
        return None
