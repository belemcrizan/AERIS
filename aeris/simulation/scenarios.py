"""Built-in missions used by examples, the API, and the experiment."""

from __future__ import annotations

from pydantic import BaseModel, Field

from aeris.core.enums import HazardType, SideEffectClass
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
    ground_truth_hazards: list[HazardType] = Field(default_factory=list)
    notes: str = ""

    def runtime(self) -> SimulatedAgent:
        return SimulatedAgent(faults=[fault.model_copy() for fault in self.faults])


def _waypoints(*names: str, **flags: object) -> list[Waypoint]:
    side_effect = flags.get("side_effect", SideEffectClass.READ_ONLY)
    supports_idempotency = bool(flags.get("supports_idempotency", False))
    compensation = flags.get("compensation")
    idempotency_key = flags.get("idempotency_key")
    built: list[Waypoint] = []
    for index, name in enumerate(names):
        key = None
        if supports_idempotency:
            key = f"{idempotency_key or name}-key"
        built.append(
            Waypoint(
                waypoint_id=new_id("wp"),
                name=name,
                index=index,
                description=name,
                side_effect=side_effect if index == flags.get("effect_index", 0) else SideEffectClass.READ_ONLY,
                supports_idempotency=supports_idempotency and index == flags.get("effect_index", 0),
                idempotency_key=key if index == flags.get("effect_index", 0) else None,
                compensation_name=str(compensation) if compensation and index == flags.get("effect_index", 0) else None,
            )
        )
    return built


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


def _base(
    scenario_id: str,
    notes: str,
    faults: list[InjectedFault],
    injects_failure: bool,
    ground_truth: list[HazardType] | None = None,
) -> Scenario:
    return Scenario(
        scenario_id=scenario_id,
        objective="Retrieve information and produce an answer.",
        success_criteria="An answer is produced without a terminal failure.",
        routes=retrieve_routes(),
        faults=faults,
        injects_failure=injects_failure,
        ground_truth_hazards=ground_truth or [],
        notes=notes,
    )


def _effect_scenario(
    scenario_id: str,
    notes: str,
    faults: list[InjectedFault],
    injects_failure: bool,
    ground_truth: list[HazardType] | None = None,
) -> Scenario:
    return _base(scenario_id, notes, faults, injects_failure, ground_truth)


def _side_effect_case(
    *,
    scenario_id: str,
    notes: str,
    side_effect: SideEffectClass,
    fault: InjectedFault,
    ground_truth: list[HazardType],
    injects_failure: bool,
    supports_idempotency: bool = False,
    compensation: str | None = None,
    commit_on_success: bool = False,
) -> Scenario:
    del commit_on_success  # success already commits a non-read-only waypoint
    alpha = _waypoints(
        "write_record",
        "produce_answer",
        side_effect=side_effect,
        supports_idempotency=supports_idempotency,
        compensation=compensation,
        effect_index=0,
    )
    bravo = _waypoints("interpret_objective", "produce_answer")
    routes = [
        Route(
            route_id=new_id("rte"),
            name="alpha",
            description="Route with an explicit side effect",
            waypoints=alpha,
            estimated_reliability=0.9,
            estimated_latency_ms=400,
            estimated_cost=1.0,
            side_effect_risk=0.15 if side_effect is SideEffectClass.IRREVERSIBLE_WRITE else 0.05,
        ),
        Route(
            route_id=new_id("rte"),
            name="bravo",
            description="Read-only fallback",
            waypoints=bravo,
            estimated_reliability=0.85,
            estimated_latency_ms=700,
            estimated_cost=1.5,
        ),
    ]
    return Scenario(
        scenario_id=scenario_id,
        objective="Apply an external action and finish the mission.",
        success_criteria="The mission completes without a duplicate or abandoned side effect.",
        routes=routes,
        faults=[fault],
        injects_failure=injects_failure,
        ground_truth_hazards=ground_truth,
        notes=notes,
    )


def builtin_scenarios() -> dict[str, Scenario]:
    catalog = {
        "happy_path": _base(
            "happy_path",
            "Nominal flight. No injected degradation.",
            [],
            False,
        ),
        "latency_reroute": _base(
            "latency_reroute",
            "Waypoint 2 on Alpha is slow. AERIS should divert to Bravo.",
            [InjectedFault(route_name="alpha", waypoint_index=1, latency_ms=5000)],
            False,
            [HazardType.HIGH_LATENCY],
        ),
        "persistent_latency": _base(
            "persistent_latency",
            "Alpha stays slow across attempts, so a retry would not clear the hazard.",
            [InjectedFault(route_name="alpha", waypoint_index=1, latency_ms=5000, persistent=True)],
            False,
            [HazardType.HIGH_LATENCY],
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
            [HazardType.TOOL_FAILURE],
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
            [HazardType.TOOL_FAILURE],
        ),
        "low_confidence": _base(
            "low_confidence",
            "Alpha retrieve reports low confidence. The value is runtime-reported, not measured.",
            [InjectedFault(route_name="alpha", waypoint_index=1, low_confidence=0.15)],
            False,
            [],
        ),
        "false_low_confidence": _effect_scenario(
            scenario_id="false_low_confidence",
            notes=(
                "Alpha is healthy but reports low confidence. Bravo then fails. "
                "AERIS can do worse than CONTROL by diverting on an untrusted self-report."
            ),
            faults=[
                InjectedFault(route_name="alpha", waypoint_index=1, low_confidence=0.1),
                InjectedFault(route_name="bravo", waypoint_index=1, tool_failure=True, persistent=True),
            ],
            injects_failure=False,
            ground_truth=[],
        ),
        "false_high_confidence": _base(
            "false_high_confidence",
            "The tool fails while the agent reports confidence 0.99. Detection must follow the tool error.",
            [
                InjectedFault(
                    route_name="alpha",
                    waypoint_index=1,
                    tool_failure=True,
                    persistent=True,
                    reported_confidence=0.99,
                )
            ],
            True,
            [HazardType.TOOL_FAILURE],
        ),
        "stale_data": _base(
            "stale_data",
            "Alpha retrieve returns stale data.",
            [InjectedFault(route_name="alpha", waypoint_index=1, stale_data_s=10_000)],
            False,
            [HazardType.STALE_DATA],
        ),
        "repeated_action": _base(
            "repeated_action",
            "Alpha repeats the same action.",
            [InjectedFault(route_name="alpha", waypoint_index=1, repeated_action=True)],
            False,
            [HazardType.REPEATED_ACTION],
        ),
        "no_progress": _base(
            "no_progress",
            "Alpha stops making progress.",
            [
                InjectedFault(route_name="alpha", waypoint_index=0, no_progress=True),
                InjectedFault(route_name="alpha", waypoint_index=1, no_progress=True),
            ],
            False,
            [HazardType.NO_PROGRESS],
        ),
        "timeout_critical": _base(
            "timeout_critical",
            "Alpha retrieve times out (CRITICAL). Human or diversion.",
            [InjectedFault(route_name="alpha", waypoint_index=1, timeout=True, fail=True)],
            True,
            [HazardType.TIMEOUT_RISK],
        ),
        "all_routes_fail": _base(
            "all_routes_fail",
            "Alpha and Bravo fail. Used to falsify a universal recovery claim.",
            [
                InjectedFault(route_name="alpha", waypoint_index=1, tool_failure=True, persistent=True),
                InjectedFault(route_name="bravo", waypoint_index=1, tool_failure=True, persistent=True),
            ],
            True,
            [HazardType.TOOL_FAILURE],
        ),
        "budget_risk": _base(
            "budget_risk",
            "Token budget exceeded on Alpha.",
            [InjectedFault(route_name="alpha", waypoint_index=1, token_usage=120_000)],
            False,
            [HazardType.BUDGET_RISK],
        ),
        "healthy_noisy_telemetry": _base(
            "healthy_noisy_telemetry",
            "Latency, confidence, and freshness stay inside policy. AERIS should not intervene.",
            [
                InjectedFault(
                    route_name="alpha",
                    waypoint_index=1,
                    latency_ms=1500,
                    reported_confidence=0.62,
                    stale_data_s=30,
                )
            ],
            False,
            [],
        ),
        "idempotent_retry": _side_effect_case(
            scenario_id="idempotent_retry",
            notes="A lost response is retried with the same idempotency key. The write commits once.",
            side_effect=SideEffectClass.IDEMPOTENT_WRITE,
            supports_idempotency=True,
            fault=InjectedFault(
                route_name="alpha",
                waypoint_index=0,
                lose_response=True,
                commit_side_effect=True,
                persistent=False,
            ),
            ground_truth=[HazardType.TOOL_FAILURE],
            injects_failure=True,
        ),
        "unsafe_irreversible_retry": _side_effect_case(
            scenario_id="unsafe_irreversible_retry",
            notes="An irreversible write committed and the response was lost. Automatic retry is rejected.",
            side_effect=SideEffectClass.IRREVERSIBLE_WRITE,
            supports_idempotency=False,
            fault=InjectedFault(
                route_name="alpha",
                waypoint_index=0,
                lose_response=True,
                commit_side_effect=True,
                persistent=True,
            ),
            ground_truth=[HazardType.TOOL_FAILURE],
            injects_failure=True,
        ),
        "compensation_success": _side_effect_case(
            scenario_id="compensation_success",
            notes="A reversible write commits, a later step fails, compensation runs, then Bravo completes.",
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            compensation="undo_write",
            fault=InjectedFault(
                route_name="alpha",
                waypoint_index=1,
                tool_failure=True,
                persistent=True,
            ),
            commit_on_success=True,
            ground_truth=[HazardType.TOOL_FAILURE],
            injects_failure=True,
        ),
        "compensation_failure": _side_effect_case(
            scenario_id="compensation_failure",
            notes="Compensation is required and the compensator fails. The flight does not reroute.",
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            compensation="undo_write",
            fault=InjectedFault(
                route_name="alpha",
                waypoint_index=1,
                tool_failure=True,
                persistent=True,
                compensation_fails=True,
            ),
            commit_on_success=True,
            ground_truth=[HazardType.TOOL_FAILURE],
            injects_failure=True,
        ),
    }
    return catalog


def get_scenario(scenario_id: str) -> Scenario:
    scenarios = builtin_scenarios()
    if scenario_id not in scenarios:
        raise KeyError(f"unknown scenario: {scenario_id}")
    return scenarios[scenario_id]
