from aeris.core.models import Route, Waypoint
from aeris.routing.planner import RoutePlanner


def _route(name: str, reliability: float, latency: float, cost: float, human: bool = False) -> Route:
    return Route(
        route_id=f"rte_{name}",
        name=name,
        description=name,
        waypoints=[Waypoint(waypoint_id=f"wp_{name}", name="step", index=0)],
        estimated_reliability=reliability,
        estimated_latency_ms=latency,
        estimated_cost=cost,
        is_human=human,
    )


def test_planner_prefers_reliable_cheap_machine_route_and_explains_why():
    planner = RoutePlanner()
    routes = [
        _route("alpha", 0.9, 800, 1.0),
        _route("bravo", 0.7, 1500, 2.0),
        _route("charlie", 0.99, 30_000, 10.0, human=True),
    ]
    plan = planner.plan("msn_1", routes)
    assert plan.selected_route_id == "rte_alpha"
    assert plan.rationale
    assert any("score=" in line for line in plan.rationale)


def test_failed_route_is_penalized():
    planner = RoutePlanner()
    routes = [_route("alpha", 0.95, 800, 1.0), _route("bravo", 0.8, 900, 1.2)]
    scored = planner.score_routes(routes, failed_route_ids=["rte_alpha"])
    assert scored[0].route.route_id == "rte_bravo"
