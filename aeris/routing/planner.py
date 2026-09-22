"""RoutePlanner scores candidate airways without flying them.

Score(r) = w_success * estimated_success
         - w_latency * normalized_latency
         - w_cost * normalized_cost
         - w_hazard * hazard_risk
         - w_failed * previously_failed
         - w_side_effect * side_effect_risk
         - w_shared * shares_a_failed_dependency
         + w_diversity * failure_domain_diversity(current, r)

Every term is returned in ``ScoredRoute.terms`` and in the human-readable
reasons, so a human (or the recorder) can see why a route won.
"""

from __future__ import annotations

from aeris.core.errors import NoRouteAvailable
from aeris.core.hashing import stable_hash
from aeris.core.ids import new_id
from aeris.core.models import Flight, FlightPlan, Hazard, Route, ScoredRoute
from aeris.routing.diversity import failure_domain_diversity


class RoutePlanner:
    reliability_weight: float = 1.5
    latency_weight: float = 0.5
    cost_weight: float = 0.3
    failure_weight: float = 0.25
    hazard_weight: float = 0.1
    side_effect_weight: float = 0.2
    diversity_weight: float = 0.6
    shared_dependency_weight: float = 1.0
    human_penalty: float = 0.4

    def __init__(self, *, use_diversity: bool = True) -> None:
        self.use_diversity = use_diversity

    def weights(self) -> dict[str, float | bool]:
        return {
            "w_success": self.reliability_weight,
            "w_latency": self.latency_weight,
            "w_cost": self.cost_weight,
            "w_failed_route": self.failure_weight * 4,
            "w_hazard": self.hazard_weight,
            "w_side_effect": self.side_effect_weight,
            "w_diversity": self.diversity_weight if self.use_diversity else 0.0,
            "w_shared_failed_dependency": self.shared_dependency_weight if self.use_diversity else 0.0,
            "human_penalty": self.human_penalty,
            "use_diversity": self.use_diversity,
        }

    def weights_hash(self) -> str:
        return stable_hash(self.weights())

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
            raise NoRouteAvailable("no routes available to plan")
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
        current: Route | None = None,
        failed_dependencies: set[str] | None = None,
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
            scored.append(
                self.score_route(
                    route,
                    failed=failed,
                    hazards=hazards,
                    current=current,
                    failed_dependencies=failed_dependencies or set(),
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored

    def score_route(
        self,
        route: Route,
        *,
        failed: set[str],
        hazards: list[Hazard],
        current: Route | None = None,
        failed_dependencies: set[str] | None = None,
    ) -> ScoredRoute:
        latency_n = min(route.estimated_latency_ms / 5000.0, 1.0)
        cost_n = min(route.estimated_cost / 10.0, 1.0)
        side_effect_n = min(max(route.side_effect_risk, 0.0), 1.0)
        failures = 1.0 if route.route_id in failed else 0.0
        hazard_penalty = self.hazard_weight * len(hazards)
        criticals = sum(1 for hazard in hazards if hazard.severity.value == "CRITICAL")
        hazard_penalty += 0.3 * criticals
        reliability_term = self.reliability_weight * route.estimated_reliability
        latency_penalty = self.latency_weight * latency_n
        cost_penalty = self.cost_weight * cost_n
        failure_penalty = self.failure_weight * failures * 4
        side_effect_penalty = self.side_effect_weight * side_effect_n

        diversity: float | None = None
        diversity_bonus = 0.0
        diversity_note = "diversity not evaluated (no current route)"
        shared_failed: list[str] = []
        shared_penalty = 0.0
        if self.use_diversity:
            if current is not None and current.route_id != route.route_id:
                report = failure_domain_diversity(current, route)
                diversity = report.score
                diversity_note = report.explain()
                if diversity is not None:
                    diversity_bonus = self.diversity_weight * diversity
            if failed_dependencies:
                shared_failed = sorted(route.dependency_ids() & failed_dependencies)
                if shared_failed:
                    shared_penalty = self.shared_dependency_weight
        else:
            diversity_note = "diversity disabled (ablation)"

        human_penalty = self.human_penalty if route.is_human else 0.0
        score = (
            reliability_term
            - latency_penalty
            - cost_penalty
            - failure_penalty
            - hazard_penalty
            - side_effect_penalty
            - shared_penalty
            + diversity_bonus
            - human_penalty
        )
        terms = {
            "estimated_success": route.estimated_reliability,
            "success_term": round(reliability_term, 4),
            "latency_penalty": round(latency_penalty, 4),
            "cost_penalty": round(cost_penalty, 4),
            "hazard_penalty": round(hazard_penalty, 4),
            "failed_route_penalty": round(failure_penalty, 4),
            "side_effect_penalty": round(side_effect_penalty, 4),
            "shared_failed_dependency_penalty": round(shared_penalty, 4),
            "diversity_bonus": round(diversity_bonus, 4),
            "human_penalty": round(human_penalty, 4),
            "score": round(score, 4),
        }
        reasons = [
            f"+ reliability {route.estimated_reliability:.2f} -> {reliability_term:.3f}",
            f"- latency penalty {latency_penalty:.3f}",
            f"- cost penalty {cost_penalty:.3f}",
            f"- current hazard penalty {hazard_penalty:.3f}",
            f"- failed route penalty {failure_penalty:.3f}",
            f"- side_effect_risk penalty {side_effect_penalty:.3f}",
            f"- shared failed dependency penalty {shared_penalty:.3f}",
            f"+ failure-domain diversity bonus {diversity_bonus:.3f}",
            diversity_note,
            f"reliability={route.estimated_reliability:.2f} * {self.reliability_weight}",
            f"latency_norm={latency_n:.2f} * {self.latency_weight}",
            f"cost_norm={cost_n:.2f} * {self.cost_weight}",
            f"previous_failure_penalty={failures}",
            f"hazard_penalty={hazard_penalty:.2f}",
            f"score={score:.3f}",
        ]
        if route.route_id in failed:
            reasons.append("route previously failed on this flight")
        if shared_failed:
            reasons.append(f"shares failed dependency {shared_failed}")
        if route.is_human:
            reasons.append("human airway preferred only after machine routes are exhausted")
        return ScoredRoute(
            route=route,
            score=score,
            reasons=reasons,
            terms=terms,
            diversity=diversity,
            shared_failed_dependencies=shared_failed,
        )

    def score_alternatives(self, flight: Flight, hazards: list[Hazard]) -> list[ScoredRoute]:
        if flight.plan is None:
            return []
        current = None
        if flight.current_route_id is not None:
            try:
                current = flight.current_route()
            except (KeyError, NoRouteAvailable):
                current = None
        return self.score_routes(
            flight.plan.routes,
            failed_route_ids=flight.failed_route_ids,
            hazards=hazards,
            exclude_human=False,
            exclude_route_ids=[flight.current_route_id] if flight.current_route_id else None,
            current=current,
            failed_dependencies=flight.failed_dependency_ids(),
        )

    def next_route(
        self,
        flight: Flight,
        hazards: list[Hazard],
    ) -> ScoredRoute | None:
        if flight.plan is None:
            return None
        scored = self.score_alternatives(flight, hazards)
        failed = set(flight.failed_route_ids)
        machine = [item for item in scored if not item.route.is_human and item.route.route_id not in failed]
        independent = [item for item in machine if not item.shared_failed_dependencies]
        if independent:
            return independent[0]
        if machine:
            return machine[0]
        for item in scored:
            if item.route.is_human:
                return item
        return None
