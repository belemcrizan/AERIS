"""Built-in missions used by examples, the API, and the experiment."""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeris.core.ids import new_id
from aeris.core.models import Route, Waypoint
from aeris.simulation.agent import InjectedFault, SimulatedAgent


class Scenario(BaseModel):
    scenario_id: str
    objective: str
    success_criteria: str
    routes: list[Route]
    faults: list[InjectedFault] = Field(default_factory=list)
    injects_failure: bool = False
    notes: str = ""

    def runtime(self) -> SimulatedAgent:
        return SimulatedAgent(faults=[fault.model_copy() for fault in self.faults])


def _waypoints(*names: str) -> list[Waypoint]:
    return [
        Waypoint(waypoint_id=new_id("wp"), name=name, index=index, description=name)
        for index, name in enumerate(names)
    ]


def retrieve_routes() -> list[Route]:
    steps = ("interpret_objective", "retrieve_information", "produce_answer")
    return [
        Route(
            route_id=new_id("rte"),
            name="alpha",
            description="Primary model and primary tool",
            waypoints=_waypoints(*steps),
            estimated_reliability=0.92,
            estimated_latency_ms=800,
            estimated_cost=1.0,
        ),
        Route(
            route_id=new_id("rte"),
            name="bravo",
            description="Fallback model and alternative tool",
            waypoints=_waypoints(*steps),
            estimated_reliability=0.78,
            estimated_latency_ms=1500,
            estimated_cost=2.0,
        ),
        Route(
            route_id=new_id("rte"),
            name="charlie",
            description="Human escalation airway",
            waypoints=_waypoints("human_review"),
            estimated_reliability=0.99,
            estimated_latency_ms=30_000,
            estimated_cost=10.0,
            is_human=True,
        ),
    ]


def _base(scenario_id: str, notes: str, faults: list[InjectedFault], injects_failure: bool) -> Scenario:
    return Scenario(
        scenario_id=scenario_id,
        objective="Retrieve information and produce an answer.",
        success_criteria="An answer is produced without a terminal failure.",
        routes=retrieve_routes(),
        faults=faults,
        injects_failure=injects_failure,
        notes=notes,
    )


def builtin_scenarios() -> dict[str, Scenario]:
    return {
        "happy_path": _base(
            "happy_path",
            "Nominal flight. No injected degradation.",
            [],
            False,
        ),
        "latency_reroute": _base(
            "latency_reroute",
            "Waypoint 2 on Alpha is slow. AERIS should divert to Bravo.",
            [
                InjectedFault(
                    route_name="alpha",
                    waypoint_index=1,
                    latency_ms=5000,
                )
            ],
            False,
        ),
        "tool_failure_retry": _base(
            "tool_failure_retry",
            "Alpha retrieve fails once; a retry can succeed.",
            [
                InjectedFault(
                    route_name="alpha",
                    waypoint_index=1,
                    tool_failure=True,
                    persistent=False,
                )
            ],
            True,
        ),
        "persistent_tool_failure": _base(
            "persistent_tool_failure",
            "Alpha retrieve always fails. Recovery requires Bravo.",
            [
                InjectedFault(
                    route_name="alpha",
                    waypoint_index=1,
                    tool_failure=True,
                    persistent=True,
                )
            ],
            True,
        ),
        "low_confidence": _base(
            "low_confidence",
            "Alpha retrieve reports low confidence.",
            [InjectedFault(route_name="alpha", waypoint_index=1, low_confidence=0.15)],
            False,
        ),
        "stale_data": _base(
            "stale_data",
            "Alpha retrieve returns stale data.",
            [InjectedFault(route_name="alpha", waypoint_index=1, stale_data_s=10_000)],
            False,
        ),
        "repeated_action": _base(
            "repeated_action",
            "Alpha repeats the same action.",
            [InjectedFault(route_name="alpha", waypoint_index=1, repeated_action=True)],
            False,
        ),
        "timeout_critical": _base(
            "timeout_critical",
            "Alpha retrieve times out (CRITICAL). Human or diversion.",
            [InjectedFault(route_name="alpha", waypoint_index=1, timeout=True, fail=True)],
            True,
        ),
        "all_routes_fail": _base(
            "all_routes_fail",
            "Alpha and Bravo fail. Used to falsify a universal recovery claim.",
            [
                InjectedFault(route_name="alpha", waypoint_index=1, tool_failure=True, persistent=True),
                InjectedFault(route_name="bravo", waypoint_index=1, tool_failure=True, persistent=True),
            ],
            True,
        ),
        "budget_risk": _base(
            "budget_risk",
            "Token budget exceeded on Alpha.",
            [InjectedFault(route_name="alpha", waypoint_index=1, token_usage=120_000)],
            False,
        ),
    }


def get_scenario(scenario_id: str) -> Scenario:
    scenarios = builtin_scenarios()
    if scenario_id not in scenarios:
        raise KeyError(f"unknown scenario: {scenario_id}")
    return scenarios[scenario_id]
