"""Failure-domain diversity, route failure history, and independent fallback preference."""

from __future__ import annotations

from datetime import UTC, datetime

from support_helpers import SupportRig

from aeris.cases.support import case
from aeris.cases.support.case import RouteSet
from aeris.core.enums import ControlAction, EventType, ExecutionState, FailureReason, HazardType
from aeris.core.models import (
    Agent,
    Flight,
    FlightPlan,
    Route,
    RouteDependencies,
    RouteFailureRecord,
    Waypoint,
)
from aeris.routing.diversity import failure_domain_diversity
from aeris.routing.planner import RoutePlanner


def _route(route_id: str, *, provider: str, model: str, data: str, services: list[str]) -> Route:
    return Route(
        route_id=route_id,
        name=route_id,
        description=route_id,
        waypoints=[Waypoint(waypoint_id=f"{route_id}-w", name="step", index=0, depends_on=services)],
        estimated_reliability=0.9,
        dependencies=RouteDependencies(model_provider=provider, model_family=model, data_source=data),
    )


ALPHA = _route("alpha", provider="ProviderA", model="ModelX", data="SearchAPI", services=["svc:search"])
BRAVO = _route("bravo", provider="ProviderA", model="ModelX", data="SearchAPI", services=["svc:search"])
CHARLIE = _route("charlie", provider="ProviderB", model="ModelY", data="LocalIndex", services=["svc:index"])


def test_identical_failure_domains_score_zero_and_independent_routes_score_high():
    same = failure_domain_diversity(ALPHA, BRAVO)
    different = failure_domain_diversity(ALPHA, CHARLIE)
    assert same.score == 0.0
    assert different.score is not None and different.score > 0.9
    assert "model_provider:ProviderA" in same.shared
    assert "diversity=" in different.explain()


def test_unknown_metadata_is_not_scored_optimistically():
    bare = Route(route_id="x", name="x", description="x", waypoints=[Waypoint(waypoint_id="w", name="w", index=0)])
    assert failure_domain_diversity(bare, bare.model_copy(update={"route_id": "y"})).score is None


def _flight(routes: list[Route], current: str, history: list[RouteFailureRecord]) -> Flight:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Flight(
        flight_id="f",
        mission_id="m",
        agent=Agent(agent_id="a", name="a"),
        plan=FlightPlan(plan_id="p", mission_id="m", routes=routes, selected_route_id=current),
        current_route_id=current,
        route_history=history,
        created_at=now,
        updated_at=now,
    )


def test_planner_penalizes_a_candidate_that_shares_the_failed_dependency():
    record = RouteFailureRecord(
        route_id="alpha", hazard_type=HazardType.TOOL_FAILURE, tool="search", dependency_ids=["svc:search"],
        most_recent=datetime(2026, 1, 1, tzinfo=UTC),
    )
    flight = _flight([ALPHA, BRAVO, CHARLIE], "alpha", [record])
    scored = {item.route.route_id: item for item in RoutePlanner().score_alternatives(flight, [])}
    assert scored["bravo"].shared_failed_dependencies == ["svc:search"]
    assert scored["bravo"].terms["shared_failed_dependency_penalty"] > 0
    assert scored["charlie"].shared_failed_dependencies == []
    assert RoutePlanner().next_route(flight, []).route.route_id == "charlie"
    for item in scored.values():
        assert {"diversity_bonus", "shared_failed_dependency_penalty", "success_term", "score"} <= set(item.terms)


def test_planner_prefers_independent_fallback_even_if_it_scores_lower():
    faster = BRAVO.model_copy(update={"estimated_reliability": 0.99, "estimated_latency_ms": 10.0})
    record = RouteFailureRecord(
        route_id="alpha", hazard_type=HazardType.TOOL_FAILURE, dependency_ids=["svc:search"],
        most_recent=datetime(2026, 1, 1, tzinfo=UTC),
    )
    flight = _flight([ALPHA, faster, CHARLIE], "alpha", [record])
    assert RoutePlanner().next_route(flight, []).route.route_id == "charlie"


async def test_shared_dependency_outage_refuses_the_non_independent_diversion():
    rig = SupportRig(scenario="shared_dependency_outage")
    await rig.run()
    assert rig.flight.state is ExecutionState.ABORTED
    assert rig.flight.route_changes == 0
    assert rig.flight.result.failure_reason is FailureReason.ROUTE_EXHAUSTION
    history = rig.flight.route_history
    assert history and "svc:runbook_search_api" in history[0].dependency_ids
    assert history[0].tool == "search_runbook"
    decisions = await rig.events(EventType.DECISION)
    blocked = [event for event in decisions if "blocked_alternative" in event.payload.get("evidence", {})]
    assert blocked and blocked[-1].payload["action"] == ControlAction.ABORT.value


async def test_independent_fallback_recovers_the_same_outage():
    rig = SupportRig(scenario="independent_fallback_outage")
    await rig.run()
    assert rig.flight.state is ExecutionState.COMPLETED
    assert rig.flight.current_route().name == "bravo"


def test_support_routes_declare_their_shared_domains():
    alpha, bravo_shared, bravo = case.build_routes(RouteSet.BOTH)
    independent = failure_domain_diversity(alpha, bravo)
    shared = failure_domain_diversity(alpha, bravo_shared)
    assert independent.score > shared.score
    assert "svc:runbook_search_api" in shared.shared
    assert "svc:crm" in independent.shared
